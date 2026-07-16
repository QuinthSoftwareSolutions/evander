# 13. Communications, Meetings, Calls, and Wakes

> **At a glance.** A message can inform an agent, ask for a reply, or become new work, but receiving it should not wake every agent indiscriminately. Evander first records communication through an allowed channel, then applies project rules to decide what it means and whether an agent should resume. This chapter follows that path across chat systems, email, meetings, calls, and scheduled reminders.

## From a message to a wake

A message does not travel straight from a chat system into an agent's active conversation. Evander
first decides whether the project may use that communication destination, records the message, lets
the workflow decide whether it matters, and only then asks an eligible session to resume.

For the Northstar Platform team, an operator might configure a readable and replyable Teams chat for
`mission-control`. The project workflow can select messages about the telemetry-retry work item and
dispatch a wake only to the implementation session. A review session can reply in the same thread
without gaining access to unrelated chats.

Four identities keep those handoffs clear:

| Concept | Example | Meaning |
|---|---|---|
| Communication target | `northstar-ops` | Stable, secret-free policy name for a Discord channel, Teams chat/channel/user, or similar endpoint |
| Stored message | provider + target + message/cursor ID | Locally materialized content and thread context |
| Workflow event | `trigger:communication:new-message` | A durable fact eligible to start workflow logic |
| Wake dispatch | session + source event + mode | A request to resume or create an agent turn |

Receiving a message does not automatically wake every session. A send permission does not imply read
permission. A successful provider API call does not prove that a workflow consumed the resulting
event.

## What an operator may notice

Because each handoff is separate, partial progress is visible and useful:

| What you see | What it usually means | Useful information to capture |
|---|---|---|
| The provider shows a message, but Evander does not | The message has not crossed the ingest boundary | Directory, target, sender, provider message time and ID |
| Evander shows the message, but no workflow starts | The message was recorded but did not match the configured trigger or sender rules | Stored message ID, target, trigger, and last workflow activity |
| A workflow runs, but the agent does not resume | The wake was denied, deferred, or failed after workflow selection | Run ID, wake record, session ID, and visible status |
| Reading works, but replying does not | The target or thread may allow `read` without `reply` | Target ID, message or thread ID, requested operation, and denial |
| A call or meeting is visible, but no summary arrives | Transport, event ingest, workflow, and wake are at different stages | Call or meeting ID, latest provider state, event time, and session ID |

An operator does not need to inspect every service to make a useful report. The durable IDs reveal
how far the communication travelled.

## Who may be contacted

Operators and workflow authors select communication targets by a stable, readable ID rather than by
pasting an arbitrary provider address into each job. Each target says whether the project may read,
write, or reply. Direct messages can additionally restrict which senders are accepted.

> <span class="sidebar-kicker">Deeper detail</span> **Target resolution and policy**
>
> A target definition has a stable ID, provider and target kind, provider-native address, display
> metadata, and capabilities. Capabilities are explicit—typically `read`, `write`, and `reply`—and
> are checked per operation. Sessions refer to target IDs or legacy provider references; the resolver
> applies directory inheritance and overlays before returning the effective target.
>
> Target definitions contain addresses, not access tokens. Discord bot tokens, Microsoft Graph
> credentials, email credentials, and telephony secrets remain in host environment/configuration.
> Directory-scoped policy can narrow the root catalog. A child directory cannot turn an unknown or
> forbidden endpoint into an allowed target merely by supplying its raw provider ID.
>
> Direct-message routes deserve a second check: the target may be allowed while the sender is not.
> Inbound routing and workflow triggers apply sender/user allowlists where configured.

## Why a stored message may not wake an agent

Recording a message and deciding to act on it are intentionally different steps. A project can keep
an auditable inbox without treating every notification as new work. If a message does matter, the
workflow chooses the session and the wake behavior.

> <span class="sidebar-kicker">Technical sidebar</span> **Ingest, query, trigger, and wake**
>
> **Ingest.** `evander-discord-ingest` and the Teams ingest command poll provider APIs using host
> credentials, normalize messages, and upsert them into provider-specific inbox stores. Cursor state
> makes repeated polling safe. Teams meeting ingest consumes the media bridge's structured event
> stream rather than pretending a meeting transcript is an ordinary Graph chat message.
>
> **Query and trigger.** `query:communication:messages` reads stored messages through the normalized
> target boundary. `trigger:communication:new-message` evaluates new stored messages for explicit
> targets and sender rules. Provider-specific compatibility triggers still exist, but the normalized
> trigger is the preferred design for portable workflow packs.
>
> **Wake.** Wake dispatch is a later decision. `evander-discord-wake` considers unread Discord
> messages for eligible active sessions. `evander-scheduled-wake` handles persisted schedules. Azure
> DevOps PR feedback is handled by `evander-ado-worker`, not either communication wake worker. There
> is no current `evander-host-heartbeat` service.
>
> This separation prevents a provider polling hiccup from becoming a session lifecycle decision and
> prevents one stored message from being delivered repeatedly without a durable cursor or dispatch
> record.

## Reading, replying, and exchanging files

Permission follows the action. Being allowed to read a project channel does not automatically permit
a new post, and permission to post does not imply access to every thread. Replies remain tied to the
message or thread an operator can recognize.

Attachments are not trusted merely because a provider displayed them. Evander checks an inbound
file before storing it as a communication artifact, and it sends only a safe file from the current
session's artifact area.

> <span class="sidebar-kicker">Technical sidebar</span> **Tool and attachment checks**
>
> Gateway tools first resolve the calling session, then the effective target, capability, directory
> policy, and approval requirement. Reads are limited to allowed targets. Replies additionally bind
> to an existing message/thread. New outbound messages require `write`; threaded responses require
> `reply`.
>
> Inbound attachments are metadata until explicitly fetched. Attachment helpers re-resolve the
> source message and target, enforce allowlists, bound file size, reject unsafe filenames and media
> types, and store accepted bytes as communication artifacts. An artifact link records the
> provider/message relationship without making provider URLs a permanent trust token.
>
> Outbound files follow the inverse path: Evander reads a safe regular file from the session artifact
> root, verifies the selected target and capability, then uploads through the host provider. A guest
> cannot supply an arbitrary host path.
>
> All sensitive tool calls pass through policy and audit. If an approval is required, the provider
> action does not happen until the approval is granted and still valid.

## Email: drafting and sending are separate

An agent can prepare a draft without automatically gaining permission to send it. Recipient and
domain rules still apply, and the final send can pause for explicit approval.

> <span class="sidebar-kicker">Deeper detail</span> **Mailbox providers and state**
>
> Email providers support Gmail and Microsoft Graph behind a normalized interface. Mailbox and
> folder reads are restricted by host policy. Sync cursors are stored locally so polling can continue
> without scanning the entire mailbox.
>
> Drafting and sending are different operations. Recipient and domain allowlists constrain drafts;
> a send can require explicit approval. The guest sees message and draft abstractions, not OAuth
> refresh tokens. JSON-backed email is available for controlled/local use but should not be mistaken
> for a live provider.

When a send result is uncertain—for example, the network fails after the provider accepted the
request—inspect the provider's sent/draft state and Evander's audit entry before retrying. Blind
retry can duplicate a message.

## Scheduled wakes

A scheduled wake asks Evander to resume a session at a particular time or on a repeating schedule.
The schedule is saved, so restarting the worker does not erase the reminder. Before dispatch, Evander
still checks that the target session can be resumed.

Two resume modes matter:

- `same_context` continues the existing agent context, so the reminder arrives in the current work;
- `new_context` creates an intentionally fresh turn where an allowed Admin surface permits it.

> <span class="sidebar-kicker">Technical sidebar</span> **Stored schedule and dispatch rules**
>
> A wake request contains a target session, prompt/context, source, directory, resume mode, and either
> a one-time `resume_at` or a cron expression. The SQLite-backed wake store also records status,
> lease/attempt state, next run, and dispatch history. Older JSON state may be imported once; SQLite
> is the live authority.
>
> MCP scheduling supports only `same_context`, because the calling session cannot grant itself
> authority to create an unrelated context. Admin operations can select the supported mode
> explicitly.
>
> The worker claims due records, validates that the session is still dispatchable, and records the
> outcome. A recurring schedule advances only across its handled-dispatch boundary. A failed one-time
> dispatch remains retryable with bounded backoff/deadline state rather than being marked successful.
> Disabled or completed one-time schedules have no next run.

> <span class="sidebar-kicker">For administrators</span> **Inspecting wake schedules**
>
> Useful commands include:
>
> ```bash
> evander-cli wake-schedules list
> evander-cli wake-schedules list --session-id <session-id>
> ```
>
> Use `evander-cli wake-schedules --help` for the current create/edit/delete syntax; timestamps are
> interpreted as explicit instants and should include a timezone.

## Voice calls and Teams meetings

Calls and meetings add a live media connection, but they follow the same saved-handoff principle as
messages. A provider may accept a call or a bridge may join a meeting before any transcript, result,
workflow event, or agent wake is available. Operators can therefore see where the handoff stopped.

Public session records use a contact ID or masked display information. The host keeps the telephony
credential, just as it keeps chat and email credentials.

> <span class="sidebar-kicker">Technical sidebar</span> **Call and meeting transport**
>
> Phone calls use host-owned contact and provider configuration. Public session records contain a
> contact ID or masked display information, not a raw secret credential. Retell-backed calls and
> OpenAI Realtime over the DIDWW SIP bridge have different media transports but share a projected
> voice-session lifecycle that operators can inspect.
>
> Call creation, status reconciliation, and the wake that reports a terminal call result are
> separate steps. If the provider accepted a call but the agent has not resumed, inspect the
> provider/call record and the voice-session reconciliation/wake path independently.
>
> Teams meetings use a dedicated media bridge because real-time meeting media is not exposed as an
> ordinary Linux Graph polling API. The Windows bridge joins or leaves, captures media/transcript
> events, and sends authenticated structured events to the host ingest surface. Evander persists
> meeting/session records and turns eligible transcript events into workflow input. Starting or
> stopping a bridge session is operational control, not proof that a workflow saw a transcript.

> <span class="sidebar-kicker">For administrators</span> **Meeting bridge commands**
>
> ```bash
> evander-cli teams-meeting start --help
> evander-cli teams-meeting stop --help
> ```

## Troubleshooting

Start in Admin Web with the communication, run, wake, call, or meeting record that is visible. Record
the selected directory, target, provider message or event ID, workflow run, wake dispatch, session
ID, last status, and approximate time. Also state the last boundary that definitely worked—for
example, “the message appears in Evander, but no workflow run was created.”

| Symptom | Check first | Meaning |
|---|---|---|
| Provider shows a message; Evander does not | Ingest service journal and cursor | Polling/auth/target resolution failed before local storage |
| Message is stored; workflow did not run | Trigger target, sender filter, and trigger cursor | Ingest succeeded; workflow eligibility did not |
| Workflow ran; agent did not wake | Wake dispatch record and session state | Dispatch was denied, deferred, or failed |
| Read works but reply fails | Target capabilities and thread identity | `read` does not imply `reply` |
| Attachment fetch is rejected | Source target, filename/type/size, and stored message link | File did not pass the attachment trust boundary |
| Cron fires repeatedly at once | Schedule/cursor migration or clock assumptions | Inspect persisted `next_run_at`; do not edit the database live |
| Call/meeting exists but no summary arrives | Provider/bridge record, ingest event, then wake record | Transport, event ingestion, and agent delivery are distinct |

Workers keep last-known-good configuration on a failed hot reload where supported and log the rejected reload. They should not replace a working policy with a partially parsed file.

If the next check requires provider credentials, worker journals, cursors, or bridge health, give the
captured evidence to an Evander administrator. Do not resend a message while provider acceptance is
uncertain, and do not edit a wake cursor or schedule database to make it advance.

## Administrator runbook

1. **Name the stage.** Decide whether the failure is provider receipt, ingest, local query, trigger, effect/send, or wake.
2. **Resolve the target.** In Admin Web, inspect the effective directory target, provider kind, native address, capabilities, and sender restrictions.
3. **Check the owner service.** Use `journalctl` for `evander-discord-ingest`, `evander-discord-wake`, `evander-teams-ingest`, `evander-teams-meeting-ingest`, or `evander-scheduled-wake` as appropriate.
4. **Correlate durable IDs.** Follow provider message ID/cursor → stored record → trigger event ID → workflow run → wake dispatch/session. Do not correlate only by message text.
5. **Inspect policy and approval.** Confirm the session's target allowlist/capability and any pending or expired approval.
6. **Retry at the failed boundary.** Poll again, replay the eligible workflow event, retry the effect under its idempotency contract, or allow the wake worker's bounded retry. Avoid resending when provider acceptance is uncertain.
7. **For meetings/calls, check transport separately.** Verify bridge/provider health before debugging transcript or wake logic.

## Source trail

- `src/evander/messaging/targets.py`
- `src/evander/policy_config.py`
- `src/evander/messaging/discord_ingest.py`
- `src/evander/messaging/teams_ingest.py`
- `src/evander/messaging/discord_wake.py`
- `src/evander/messaging/scheduled_wake.py`
- `src/evander/messaging/wake_dispatch.py`
- `src/evander/messaging/wake_store.py`
- `src/evander/gateway/communication_tools.py`
- `src/evander/communication_artifacts.py`
- `src/evander/email/providers.py`
- `src/evander/voice_sessions.py`
- `src/evander/retell_phone.py`
- `src/evander/didww_sip_bridge.py`
- `src/evander/teams_meeting.py`
- `docs/teams-meeting-media-bridge.md`
- `tests/test_communication_targets.py`
- `tests/test_communication_new_message_trigger.py`
- `tests/test_scheduled_wake_worker.py`
- `tests/test_wake_store.py`
- `tests/test_gateway_communication_files.py`
- `tests/test_email_providers.py`
- `tests/test_voice_sessions.py`
- `tests/test_teams_meeting.py`
