# 21. Chat, Agent Mail, Voice, and Escalation

> **At a glance.** Team communication has four distinct paths. Chat is a signed-in human operator's durable conversation with one persistent agent. Agent Mail is directional agent-to-agent inbox state governed by reporting lines. Voice is a browser OpenAI Realtime session projected from Team context. Escalation is a manager-to-operator or manager-to-governance workflow. They share some wake machinery, but not identity, authorization, storage, or delivery semantics.

## Choose the path by who is speaking

| Source and destination | Correct path |
|---|---|
| Human operator → persistent agent | **Chat**, or an operator directive when durable Team work is intended |
| Persistent agent → direct superior | Agent Mail question or progress report |
| Persistent superior → direct subordinate | Agent Mail status request |
| Top-level manager → human/external operator target | Direct escalation workflow |
| Top-level manager → governance manager | Governance agenda escalation |
| Human operator ↔ persistent agent by audio | Browser voice call |

The paths are not interchangeable. A Chat transcript is not an agent inbox. A communication target does not grant inbound access to the manager's session. A voice call is not an Agent Mail exchange. The distinction protects both user expectations and authorization.

## Operator Chat

A Team conversation contains exactly one signed-in operator user and one persistent agent. That operator/agent pair has one durable conversation. Messages record sender, status, content, timestamps, error information, and dispatch metadata.

Operators normally see their own conversations. A user with <code>team_chats.read_all</code> may inspect another operator's history, but the UI marks that mode read-only and blocks sending. The permission does not broaden Agent Mail visibility.

Sending a Chat message does not execute the agent inline in the API request:

1. The Admin API records the operator message and queues <code>team.chat.message_queued</code>.
2. The Team runtime claims a queued message only when the agent is active, not stood down, not cost-blocked, not already leased, and its core session is not busy.
3. It creates a Chat dispatch and prompts the agent with the human operator identity and message context.
4. The agent replies with <code>team_reply_to_chat</code>.
5. Dispatch finalization marks the operator message replied; a dispatch without a successful reply is marked failed with the available error context.

A Chat dispatch can be **reply-only** or allow ordinary Team mutations. Its <code>allow_team_mutations</code> metadata controls whether the agent receives normal Team action tools alongside <code>team_reply_to_chat</code>. When a human asks “please change Imani's task priority,” a mutation-enabled dispatch can perform the authorized state change and reply. A reply-only dispatch must answer without changing Team state.

The agent may use <code>team_send_operator_chat_message</code> only for operator participants listed in its current dispatch context. That is a bounded proactive message, not a way to enumerate arbitrary Admin users.

![Team Chat conversation between a synthetic operator and Imani](../../assets/evander-user-manual/screenshots/team-chat.png)

<p class="figure-caption">Figure 21.1 — Human Chat is visually and operationally separate from Agent Mail and ordinary Team work. Sanitized Northstar fixture.</p>

## Agent Mail

The **Mail** view renders durable <code>AgentInboxItem</code> records. Each item has a type, sender, recipient, priority, reply intent, payload, state, and timestamps. Types include:

- <code>operator_directive</code>;
- <code>question</code>;
- <code>status_request</code>;
- <code>progress_report</code>;
- <code>deliverable_notice</code>;
- <code>escalation</code>.

State is <code>pending</code> or <code>handled</code>. The UI presents received and sent projections over this one durable inbox collection, with structured payload details, errors, and fallback content. There is no separate Agent Mail outbox collection, and Mail is not backed by the Chat conversation tables.

Direction is enforced:

- <code>team_ask_question</code> targets the caller's direct superior;
- <code>team_send_progress_report</code> targets the direct superior and defaults to low priority with no response requested;
- <code>team_request_status</code> targets a direct subordinate and normally requests a response;
- ordinary agent mail does not permit skip-level or lateral routing.

If broader coordination is necessary, change the work structure, ask the manager to route it, or use a governance/operator surface. Do not disguise lateral communication as a status report.

### Reply intent is not wake permission

<code>response_requested</code> tells the recipient whether a meaningful response is expected. It does not control delivery and is only one part of wake eligibility.

- normal or high priority can wake even when <code>response_requested=false</code>;
- low priority wakes by itself only when a response is requested, and is then normally batched;
- low/no-reply mail is delivered on another eligible wake;
- every processed item should eventually be marked handled.

When no response is requested, do not send a thank-you, receipt, or empty acknowledgement. Read it, act if needed, and handle it atomically with the relevant command where possible.

For Northstar, Imani can send Priya a low/no-reply progress report saying the retry algorithm is implemented and tests are running. It should not cause an immediate conversational cycle. Priya can send Imani a normal status request, which wakes Imani and expects a useful answer.

## Operator directives

A directive is durable Team work addressed to a Team manager, not merely a Chat utterance. It can be created through:

- the Team Admin API;
- <code>effect:team:create-operator-directive</code> from Discord, Teams, Admin Web, or another workflow;
- <code>team_create_operator_directive</code> by an authorized governance manager.

It becomes a pending normal-priority, response-requested <code>operator_directive</code> in the manager's inbox and makes the manager due. A governance manager can direct only a member Team and only its manager. The manager handles it through ordinary Team work and communication.

Use a directive when “please investigate telemetry retry safety” should survive a Chat failure, become schedulable work, and have explicit handled state.

## Browser voice

The Team UI can start a direct browser voice call to a persistent agent. The add-on prepares an OpenAI Realtime session with a projection of the agent's current:

- Team, role, project, and reporting context;
- tasks, cases, inbox, reviews, deliverables, and runtime state;
- authorized visible tool context;
- optional voice profile and speaking style.

The browser talks to OpenAI Realtime using the prepared session. This path does not use DIDWW, Asterisk, or SIP. Core voice-session state is stored in <code>runtime/voice-sessions-store.json</code>, outside Team SQLite.

Projected context is intentionally bounded. If the realtime agent needs nonprojected detail, it can create a durable <code>query_origin_agent</code> request for the originating text agent. Terminal calls schedule reconciliation so those requests and final call state can re-enter the normal wake flow.

A call launched from Chat can add a voice-call system message to that conversation. The audio call still remains a voice session rather than a sequence of Chat messages.

An avatar is unrelated UI metadata. Saving Rowan's portrait does not select Rowan's voice or change prompt identity.

## Escalation

Only the top-level Team manager may escalate out of the Team.

### Direct operator escalation

When <code>Team.escalate_to_governance</code> is false, the Team needs an <code>operator_communication_target_id</code>. The manager's <code>team_escalate_operator</code> command records durable intent and an outbox event. The runtime pack routes <code>team.operator.escalation_requested</code> to the generic <code>effect:communication:send-message</code> provider bound to that target.

The Team service does not call a Discord or Teams sender directly. The workflow document owns routing and delivery evidence.

The target is outbound-only. It does not let external users address or wake Mira's real session. Inbound Discord or Teams access must be configured on the core session with the appropriate readable communication target and tool policy.

### Governance escalation

When <code>escalate_to_governance</code> is true, the command creates pending governance agenda items in the groups containing that Team and makes their runtimes due. The governance manager receives Team summaries and decides whether to route an operator directive, mark the agenda handled, or take another authorized action.

This path is useful when Mira must report that the telemetry fix is blocked by a cross-Team API policy. It is not a substitute for a routine question to Priya.

## Failure and delivery semantics

| Symptom | Interpretation and next check |
|---|---|
| Chat message remains queued | Inspect agent active status, stand-down, cost cap, active lease, busy core session, event publication, and Chat-claim workflow. |
| Chat dispatch finishes without a reply | Finalizer should mark the message failed; inspect command activity and prompt/session logs. |
| Agent cannot mail a peer | Lateral routing is intentionally forbidden; use reporting or work-routing mechanisms. |
| No-reply normal mail woke the recipient | Correct behavior: priority is non-low. |
| Low/no-reply mail is unseen | It waits for another eligible wake; inspect pending Mail and eventual handling. |
| Escalation is recorded but not delivered | Follow the outbox row, internal event, workflow run, communication target, and provider result. |
| External operator cannot message the manager | Outbound target was mistaken for inbound session authorization. |
| Voice has incomplete knowledge | The projection is bounded; inspect or retry the durable origin-agent query path. |
| Voice record is absent from Team SQLite | Correct storage boundary; inspect the core voice-session store and optional Chat projection. |

## Practical runbook

For Chat:

1. Record conversation ID, operator message ID, agent/session, and message status.
2. Follow <code>team.chat.message_queued</code> from its published internal event into the runtime workflow. When a queued event originated in a Team command or finalizer, include its outbox row in that trace.
3. Inspect lease/busy/cost/stand-down guards.
4. Match the Chat dispatch to <code>team_reply_to_chat</code> activity and finalization.

For Agent Mail:

1. Confirm sender and recipient are a legal direct reporting pair.
2. Inspect type, priority, <code>response_requested</code>, and pending/handled state.
3. Apply the wake rule from Chapter 20 rather than assuming all pending mail wakes.
4. When acting, handle the item in the same command transaction.

For escalation:

1. Verify the caller is the top-level manager.
2. Confirm direct-versus-governance Team policy.
3. For direct delivery, verify the communication target and live workflow route.
4. For governance, verify group membership, agenda creation, manager lease, directive, and handled state.

For voice:

1. Capture voice-session ID and originating Team conversation if any.
2. Inspect the prepared projection and terminal status in the core voice store.
3. Follow unresolved <code>query_origin_agent</code> records and reconciliation wakes.

## Source trail

- <code>src/evander_team_addon/services/chat_service.py</code> and <code>services/domain/chat.py</code> — conversation ownership, queued messages, claim guards, and reply finalization.
- <code>src/evander_team_addon/services/commands/agent_message_commands.py</code>, <code>chat_commands.py</code>, and <code>operator_escalation_commands.py</code> — directional Mail, Chat replies, and escalation commands.
- <code>src/evander_team_addon/api.py</code> — Chat streams/messages, inbox views, operator directives, and browser voice routes.
- <code>src/evander_team_addon/providers.py</code> and <code>workflow_packs.py</code> — Chat wake prompts, escalation routing, governance wakes, and voice reconciliation integration.
- <code>src/evander_team_addon/models.py</code> — conversations, messages, inbox, communication targets, voice profiles, and governance agenda.
- <code>docs/addons.md</code> — Chat/Mail distinction, voice boundary, communication direction, and escalation.
- <code>tests/test_team_addon_runtime.py</code>, <code>tests/test_team_addon_mcp.py</code>, and <code>tests/test_team_addon_api.py</code> — operator isolation, reply-only dispatch, direction, wake, and escalation behavior.
