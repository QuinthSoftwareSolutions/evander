# 21. Chat, Agent Mail, Voice, and Escalation

> **At a glance.** Team offers several ways to communicate because a person chatting with an agent is not the same as one agent assigning work to another. Chat serves durable operator identities from built-in signed-in UI or a fixed authenticated external bridge, Agent Mail carries agent-to-agent messages, voice supports a live spoken conversation, and escalation raises an issue beyond the ordinary reporting path. Choosing the right channel preserves who is speaking, what response is expected, and whether the message should wake anyone.

## Choose the path by who is speaking

| Source and destination | Correct path |
|---|---|
| Human operator → persistent agent | **Chat**, or an operator directive when durable Team work is intended |
| Persistent agent → direct superior | Agent Mail question or progress report |
| Persistent superior → direct subordinate | Agent Mail status request |
| Top-level manager → human/external operator target | Direct escalation workflow |
| Top-level manager → governance manager | Governance agenda escalation |
| Human operator ↔ persistent agent by audio | Browser voice call |

The paths are not interchangeable. A Chat transcript is not an agent inbox. A
communication target by itself does not grant inbound access to the manager's
session; the bridge needs explicit fixed identity and author authority. A voice
call is not an Agent Mail exchange. The channels may share some wake machinery,
but not identity, authorization, storage, or delivery semantics. The
distinction protects both user expectations and authorization.

For Northstar, an operator who wants to discuss a task with Imani uses Chat. Imani reports routine progress upward to Priya through Agent Mail. A durable instruction to the Team goes to Mira as an operator directive. Mira escalates beyond the Team only when ordinary reporting cannot resolve the issue. The speaker, intended recipient, and desired durability choose the channel.

## Operator Chat

A Team conversation contains exactly one durable Team operator identity and
one persistent agent. Built-in Chat binds that identity to the signed-in
operator. The external bridge maps an authenticated source message to its
fixed <code>operator_user_id</code> and records
<code>external_origin</code>; it does not treat message text as identity. That
operator/agent pair has one durable conversation. Messages record sender,
status, content, timestamps, error information, and dispatch metadata.

Operators normally see their own conversations. A user with <code>team_chats.read_all</code> may inspect another operator's history, but the UI marks that mode read-only and blocks sending. The permission does not broaden Agent Mail visibility.

Chat messages may contain text, attachments, or both. Use the attachment control beside the composer to select common documents and images; selected files appear before sending, and supported images render inline in the transcript. Downloads use the same conversation authorization as the message, so read-all access remains read-only and does not make attachments public.

The **Files** dialog controls Team Chat's directory-scoped attachment policy. Its root defaults allow operator-to-agent and agent-to-operator files, at most five files per message and 10 MiB per file, limited to common text, JSON, PDF, and raster-image types. Agent sends require exact approval by default. Child directories inherit the closest configured values. This policy belongs entirely to the Team Add-on: Microsoft Teams and the core communication connectors may use different file rules, and their settings neither grant nor deny built-in Chat attachments.

The reply may take time because sending a Chat message records work for the agent rather than running the model inside the browser request. A reply-only dispatch can answer with text but cannot attach agent files or change Team state; a mutation-enabled dispatch can perform an otherwise authorized Team action and then explain the result. Later messages in the same conversation may use a different dispatch mode.

If the agent is already working under an active Team lease, a queued plain-text Chat message is delivered once as soft steering in its next Team MCP response and is immediately marked `seen`. It does not require a Chat reply. Messages with attachments remain queued for a normal Chat dispatch so their files can be copied safely into the agent workspace. When the agent is available, queued messages in the same conversation and external return path are grouped into one dispatch; the agent receives every message ID and one reply covers the batch. Different conversations, providers, targets, or threads remain separate.

Live state-stream and automatic Chat refreshes preserve an operator's reading
position. If the reader is already at the bottom, changes conversations, or
sends a message, the view intentionally follows the newest message; otherwise
new data does not jump the transcript away from the passage being read.

> <span class="sidebar-kicker">Deeper detail</span> **How a Chat message is dispatched**
>
> Sending a Chat message does not execute the agent inline in the API request:
>
> 1. The Admin API records the operator message and queues <code>team.chat.message_queued</code>.
> 2. The Team runtime claims queued messages only when the agent is active, not stood down, not cost-blocked, not already leased, and its core session is not busy. Compatible messages in the same conversation and external return path form one batch.
> 3. It creates one Chat dispatch and prompts the agent with the human operator identity, every batched message ID, and message context. Attached files are first copied into <code>/home/agent/team-chat-attachments/&lt;message-id&gt;/</code> and are identified as untrusted operator input.
> 4. The agent replies once with <code>team_reply_to_chat</code>, covering the batch.
> 5. Dispatch finalization marks every operator message in the batch replied; a dispatch without a successful reply marks the batch failed with the available error context.
>
> A Chat dispatch can be **reply-only** or allow ordinary Team mutations. Mutation-enabled Admin Chat requires privileged operations to be unlocked and the sender to hold directory-scoped <code>team_chats.mutate</code> authority over both the recipient agent and each included project. The server snapshots the intersection of that authority and the agent's visible projects when the message is accepted; repository scope is derived from those projects. A mutation-authorized message fails without resuming the agent if that intersection becomes empty before lease. Missing or legacy mutation metadata is reply-only. When a human asks “please change Imani's task priority,” an authorized mutation-enabled dispatch can perform the state change and reply. A reply-only dispatch must answer with text only and without changing Team state.
>
> The agent may use <code>team_send_operator_chat_message</code> only for operator participants listed in its current dispatch context. That is a bounded proactive message, not a way to enumerate arbitrary Admin users.
>
> For an outbound file from a mutation-enabled Chat dispatch, the agent first calls <code>team_stage_chat_attachment</code> with a regular file under <code>/home/agent</code>. It then supplies the session-owned staged ID to <code>team_reply_to_chat</code> or <code>team_send_operator_chat_message</code>. Reply-only Chat cannot stage or attach agent files, including files staged by an earlier dispatch. Staging expires after 24 hours. When the effective policy requires approval, the approval is bound to the target, message body, staged IDs, filenames, sizes, MIME types, and SHA-256 hashes.

![Team Chat conversation between a synthetic operator and Imani](../../assets/evander-user-manual/screenshots/team-chat.png)

<p class="figure-caption">Figure 21.1 — Human Chat is visually and operationally separate from Agent Mail and ordinary Team work. Sanitized Northstar fixture.</p>

## External communication into Team Chat

A workflow can bridge an authenticated Discord or Microsoft Teams message into
the same durable operator Chat lifecycle:

<code>trigger:communication:new-message → effect:team:enqueue-operator-chat-message</code>

Configure exactly one fixed <code>conversation_id</code> or
<code>agent_id</code>, a fixed <code>operator_user_id</code>, and a declared
<code>communication_target_id</code>. Never derive the Team conversation,
agent, or operator identity from message text. The target provider must match
the incoming provider and the target must grant <code>read</code>. A preserved
Microsoft Teams channel thread additionally requires <code>reply</code>;
top-level Teams replies and every Discord reply require <code>write</code>.
Discord thread IDs are discarded so the response is a top-level channel
message.

Ingress is durable and idempotent on provider, communication target, and source
message ID. An exact replay returns the original conversation, message, and
<code>team-chat:&lt;message-id&gt;</code> dispatch. The effect queues the normal
<code>team.chat.message_queued</code> event, so transcript, lease, busy-session,
tool-grant, and reply behavior remain the built-in Chat behavior.

Set <code>import_attachments=true</code> to opt into first-class external file
delivery. Teams inline hosted images, ordinary Teams and Discord files, and
email attachments are copied into Team-owned private storage, policy-checked,
shown in the Chat transcript, and delivered to the agent through the normal
attachment workspace path. Partial failures are shown as warnings and do not
discard valid files or the external message.

The bridge defaults to <code>allow_team_mutations=false</code>. Enabling Team
mutations requires a non-empty fixed <code>authorized_author_ids</code> list,
and the authenticated source author must match it. A Discord trigger should
also filter allowed authors and exclude the Evander bot, but the effect-side
author allowlist remains required. The recipient agent and every included
project must also remain inside the workflow effect's directory scope.

When the agent replies, Team commits the internal sent message first, creates a
pending <code>ExternalChatDeliveryRecord</code>, and transactionally emits
<code>team.chat.external_reply_requested</code>. The packaged runtime sends
through the generic communication effect and records external delivery as
<code>sent</code> or <code>failed</code>; the durable state model also
distinguishes <code>pending</code>, <code>sending</code>, and
<code>unknown</code>. The Team reply remains visible if external delivery
fails, so inspect the persisted delivery ID and state before deciding whether
any retry is safe.

For a reply with Team Chat attachments, the outbound event retains the exact
internal text as <code>team_chat_body</code> and carries both
<code>attachment_ids</code> and attachment metadata. Its external
<code>body</code> appends textual references containing each filename, MIME
type, and byte count; an attachment-only reply therefore still has a
deliverable non-empty body. This contract represents the Team files in text
for the generic communication effect. It does not claim or require
provider-native file upload.

## Agent Mail

Agent Mail is structured internal coordination, not free-form access to any agent. The reporting relationship chooses the legal recipient, while priority and reply intent tell Team how urgently to present the item and whether a meaningful response is expected.

> <span class="sidebar-kicker">Technical reference</span> **Inbox records and message types**
>
> The **Mail** view renders durable <code>AgentInboxItem</code> records. Each item has a type, sender, recipient, priority, reply intent, payload, state, and timestamps. Types include:
>
> - <code>operator_directive</code>;
> - <code>question</code>;
> - <code>status_request</code>;
> - <code>progress_report</code>;
> - <code>deliverable_notice</code>;
> - <code>escalation</code>.
>
> State is <code>pending</code> or <code>handled</code>. The UI presents received and sent projections over this one durable inbox collection, with structured payload details, errors, and fallback content. There is no separate Agent Mail outbox collection, and Mail is not backed by the Chat conversation tables.

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

It becomes a pending normal-priority, response-requested <code>operator_directive</code> in the manager's inbox, makes the manager due, and atomically records a debounced <code>team.agent.wake_requested</code> event. Publication is requested immediately, while the periodic Team runtime tick remains the recovery path. A governance manager can direct only a member Team and only its manager. The manager handles it through ordinary Team work and communication.

Use a directive when “please investigate telemetry retry safety” should survive a Chat failure, become schedulable work, and have explicit handled state.

## Browser voice

Voice is for a live spoken exchange with one persistent agent. It uses a bounded snapshot of that agent's Team context; it does not turn the audio session into unrestricted access to every Team record, and it does not share an implementation path with telephone or Agent Mail integrations.

> <span class="sidebar-kicker">Technical sidebar</span> **Voice projection and reconciliation**
>
> The Team UI can start a direct browser voice call to a persistent agent. The add-on prepares an OpenAI Realtime session with a projection of the agent's current:
>
> - Team, role, project, and reporting context;
> - tasks, cases, inbox, reviews, deliverables, and runtime state;
> - authorized visible tool context;
> - optional voice profile and speaking style.
>
> The browser talks to OpenAI Realtime using the prepared session. This path does not use DIDWW, Asterisk, or SIP. Core voice-session state is stored in <code>runtime/voice-sessions-store.json</code>, outside Team SQLite.

Projected context is intentionally bounded. If the realtime agent needs nonprojected detail, it can create a durable <code>query_origin_agent</code> request for the originating text agent. Terminal calls schedule reconciliation so those requests and final call state can re-enter the normal wake flow.

A call launched from Chat can add a voice-call system message to that conversation. The audio call still remains a voice session rather than a sequence of Chat messages.

An avatar is unrelated UI metadata. Saving an agent's portrait does not select that agent's voice or change prompt identity.

## Escalation

Only the top-level Team manager may escalate out of the Team.

### Writing an escalation

An operator should be able to understand an escalation without reading its internal
handoff. The escalation tool and managed Team/governance guidance ask agents to use
a short, plain-language title and three short lines separated by blank lines:

- **Need:** the action or decision needed from the operator. Say **No action needed
  from you** when the operator need not act.
- **Why:** the blocker and its practical impact, with just enough context to make sense.
- **Next:** who acts next and when, if known. For a decision, include a recommendation and
  short reply choices.

Aim for 60-100 words and normally stay under 120. Preserve essential risks and
deadlines. Use meaningful work names and explain unfamiliar terms. Keep logs,
policy identifiers, revision history and coordination instructions in Team notes or
linked task/blocker records, with at most one useful reference in the notification.
These are writing instructions, not a delivery limit: Team does not truncate or
reject an escalation because of its length. Automatic escalations for unresolved
blockers send their short summary and a Team blocker reference; the full details
and escalation history remain in the blocker record.

For example:

> **Need:** Choose whether to delay the telemetry release until Monday.
>
> **Why:** The fix passed review, but the test environment is unavailable, so we
> cannot finish release checks today. The current release is unaffected.
>
> **Next:** Mira recommends waiting. Reply **wait** or **restore tests today**.
> Once the environment is available, Priya will rerun the checks before release.

The managed guidance version changes when these instructions change, so existing
sessions receive the updated instructions on their next eligible wake after deployment.

### Direct operator escalation

The manager can send a durable escalation to the Team's configured operator destination. Team records the request first; the ordinary Evander communication workflow performs and records delivery.

> <span class="sidebar-kicker">Deeper detail</span> **Direct escalation routing**
>
> When <code>Team.escalate_to_governance</code> is false, the Team needs an <code>operator_communication_target_id</code>. The manager's <code>team_escalate_operator</code> command records durable intent and an outbox event. The runtime pack routes <code>team.operator.escalation_requested</code> to the generic <code>effect:communication:send-message</code> provider bound to that target.
>
> The Team service does not call a Discord or Teams sender directly. The workflow document owns routing and delivery evidence.

The Team's <code>operator_communication_target_id</code> is outbound-only. It
does not by itself let external users address or wake Mira's real session.
Inbound access requires a separate, explicitly authorized core-session route
or the fixed-identity communication-to-Team-Chat workflow described above.

### Governance escalation

When <code>escalate_to_governance</code> is true, the command creates pending governance agenda items in the groups containing that Team and makes their runtimes due. The governance manager receives Team summaries and decides whether to route an operator directive, mark the agenda handled, or take another authorized action.

This path is useful when Mira must report that the telemetry fix is blocked by a cross-Team API policy. It is not a substitute for a routine question to Priya.

## Troubleshooting

Start by identifying the channel. A queued Chat message, pending Agent Mail item, undelivered escalation, and incomplete voice call have different owners and evidence. Capture the stable conversation, message, inbox, outbox, workflow, or voice-session ID shown by the relevant view rather than searching by text alone.

| Symptom | Interpretation and next check |
|---|---|
| Chat message remains queued | Inspect agent active status, stand-down, cost cap, active lease, busy core session, event publication, and Chat-claim workflow. |
| Chat dispatch finishes without a reply | Finalizer should mark the message failed; inspect command activity and prompt/session logs. |
| External message is ingested but no Team Chat message appears | Trace the communication trigger, fixed bridge configuration, source target capabilities, and durable ingress idempotency record. |
| Team reply is visible but missing externally | Inspect its <code>ExternalChatDeliveryRecord</code>, external-reply event, workflow effect execution, target capability, and recorded provider error before retrying. |
| Agent cannot mail a peer | Lateral routing is intentionally forbidden; use reporting or work-routing mechanisms. |
| No-reply normal mail woke the recipient | Correct behavior: priority is non-low. |
| Low/no-reply mail is unseen | It waits for another eligible wake; inspect pending Mail and eventual handling. |
| Escalation is recorded but not delivered | Follow the outbox row, internal event, workflow run, communication target, and provider result. |
| External operator cannot message the manager through the escalation target | The outbound target was mistaken for inbound authorization. Configure a separate fixed-identity Team Chat bridge or authorized core-session route. |
| Voice has incomplete knowledge | The projection is bounded; inspect or retry the durable origin-agent query path. |
| Voice record is absent from Team SQLite | Correct storage boundary; inspect the core voice-session store and optional Chat projection. |

## Operator handoff

For any communication problem, record the Team, channel, sender, intended recipient, time, visible state, and stable message or session ID. Include the agent's session and any dispatch or workflow-run ID if shown. Say whether the content was never accepted, accepted but waiting, dispatched without a reply, or completed without external delivery.

Do not resend under a new request ID merely because delivery is delayed, and do not reinterpret an outbound escalation target as inbound permission to reach an agent. The detailed trace below is for administrators and support.

## Administrator runbook

For Chat:

1. Record conversation ID, operator message ID, agent/session, and message status.
2. Follow <code>team.chat.message_queued</code> from its published internal event into the runtime workflow. When a queued event originated in a Team command or finalizer, include its outbox row in that trace.
3. Inspect lease/busy/cost/stand-down guards.
4. Match the Chat dispatch to <code>team_reply_to_chat</code> activity and finalization.
5. For externally originated Chat, correlate the ingress record and source
   message ID; then follow <code>team.chat.external_reply_requested</code> to
   the persisted external delivery record and success/failure recorder.

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

- <code>src/evander_team_addon/services/chat_service.py</code> and <code>services/domain/chat.py</code> — conversation ownership, external ingress idempotency, queued messages, claim guards, delivery state, and reply finalization.
- <code>src/evander_team_addon/services/commands/agent_message_commands.py</code>, <code>chat_commands.py</code>, and <code>operator_escalation_commands.py</code> — directional Mail, Chat replies, and escalation commands.
- <code>src/evander_team_addon/api.py</code> — Chat streams/messages, inbox views, operator directives, and browser voice routes.
- <code>src/evander_team_addon/providers.py</code> and <code>workflow_packs.py</code> — external Chat bridge and delivery recording, Chat wake prompts, escalation routing, governance wakes, and voice reconciliation integration.
- <code>src/evander_team_addon/models.py</code> — conversations, messages, external ingress/delivery, inbox, communication targets, voice profiles, and governance agenda.
- <code>docs/addons.md</code> — Chat/Mail distinction, voice boundary, communication direction, and escalation.
- <code>tests/test_team_addon_runtime.py</code>, <code>tests/test_team_addon_mcp.py</code>, and <code>tests/test_team_addon_api.py</code> — operator isolation, reply-only dispatch, direction, wake, and escalation behavior.
