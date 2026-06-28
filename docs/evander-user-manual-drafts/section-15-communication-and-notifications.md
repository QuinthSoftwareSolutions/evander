# 15. Communication And Notifications

Communication settings control where Evander can read messages, send status or alerts, and move files through
external channels. Use this section when you need reliable message flow, approval delivery, or file handoff.

Read this with:

- session and policy controls in section 5,
- approval flow control in section 6,
- evidence checks in section 7,
- file artifacts in section 10,
- Team wake and direct agent collaboration in section 13,
- directory/provider configuration in section 16.

## 15.1 Communication Targets

A communication target is a named destination in **Configuration > Communication targets**.

Targets are scoped by directory and can be inherited from the root directory.

Each target is built with:

1. `id`: short operator friendly key, such as `ops-discord-releases` or `lead-ops-email`.
2. `provider`: which service owns the destination.
3. `kind`: such as `discord`, `teams`, or `email`.
4. `address`: the exact channel/chat/mailbox target value.
5. capabilities: what this target is allowed to do (`read`, `write`, `reply`, `notify`).

Capability meanings:

1. `read` lets Evander fetch inbound messages and attachment metadata.
2. `write` sends top-level messages and attachment posts.
3. `reply` sends thread replies or reactions when `thread_id` is required.
4. `notify` receives operator-facing status and approval alert messages.

Common target address formats:

1. Teams channel: `team-id/channel-id`
2. Teams chat: `chat:<chat-id>`
3. Teams one-on-one: `user:<user-id-or-upn>` for send-only targets
4. Discord: configured channel ID
5. Email: mapped by mailbox policy, with host-side provider auth for the selected directory

If you want one directory to hear on one set of channels and another directory to use a different set,
configure in that directory's communication target list rather than duplicating sessions.

## 15.2 Read And Send Capabilities

Use capability checks to avoid silent failures:

1. Inbound reads and attachment listing/download need `read`.
2. Outbound top-level sends need `write`.
3. Replies and reaction-like follow-up flows need `reply`.
4. Approval notices and workflow notices need `notify`.

Provider guardrail note:

1. A target can have `read` and `notify` but no `write`; this is valid for read-only monitoring.
2. Teams `user:` targets are send-only in practice and do not support read/reaction/attachment fetch for
   these user direct targets.
3. Teams and Discord reads still follow provider-side allowlists in sessions, so check session and directory
   policy in section 5.

Use section 6 before turning on higher-risk outbound send behavior, and use section 5 if file send policies
need tightening.

## 15.3 Approval Notifications

Approval notifications are operator alerts sent when a new approval record is created.

They are separate from session destination permissions. A session can still be blocked from outbound sends while
approval notices continue to work.

How approval notifications are chosen:

1. Directory policy sets a base approval-notification target list.
2. Session policy may add more IDs for narrower cases.
3. A live workflow run can add run-scope targets.
4. The host deduplicates and sends through targets with `notify`.

If no list is configured, notifications stay off by default.

## 15.4 File Transfer

Evander handles two distinct file pathways:

1. Session file policy in section 5, which decides if a session can receive or send attachments.
2. Communication file policy here, which controls destination and payload safety for message attachments.

Inbound flow:

1. Provider receives a file in a configured target.
2. Operator or tool lists matching attachments from that message/thread/mailbox.
3. Selected attachment is downloaded to a staged artifact record for review.
4. If needed for agent tool use, it can be copied into the session's communication workspace.

Outbound flow:

1. File must be a valid staged artifact from the session workspace.
2. Send payload must name the exact artifact references.
3. Destination target must match send allowlists and target `write`/`reply` needs.
4. If approval for send is enabled, the exact payload must be approved before dispatch.

Operator reminder:

1. Keep receive and send permissions conservative by default.
2. Keep executable or unknown extensions blocked even when MIME filters look safe.
3. Keep size and MIME caps tied to workload needs.

## 15.5 Teams, Discord, And Email Workflows

Evander uses three practical incoming channels:

1. Discord commands and mentions through host-side wake and ingest paths.
2. Teams inbox/chat events through delegated Teams authentication and ingest workers.
3. Email read and draft workflows through mailbox policy and token-backed provider config.

In practice:

1. Discord/Teams inbound message flows are host-side only. Credentials stay on the host.
2. Email is commonly read and draft first; outbound send is controlled separately.
3. Provider-native auth is configured in directory provider settings and visible in Configuration.
4. Team and workflow routing can consume inbound messages even when outbound is disabled.

If a downstream workflow should wake from messages, verify trigger nodes are enabled and
their allowlist or target settings match the current directory/session.

## How-to: Add Or Update A Communication Target

Use this when you need to add a new destination or correct an existing one.

Prerequisites:

1. Correct directory selected.
2. Communication targets section opened in `Configuration`.
3. Privileges unlocked for configuration saves.

Steps:

1. Open **Configuration > Communication targets**.
2. Click **Add communication target**.
3. Enter:
   1. `id` and clear display name.
   2. `provider` and `kind` (`discord`, `teams`, or `email`).
   3. Exact `address`.
4. Add capabilities:
   1. add `read` for inbound workflow reads,
   2. add `write` for top-level outbound sends,
   3. add `reply` for thread replies,
   4. add `notify` if you want approval alerts.
5. For email targets, confirm mailbox/identity pairing in section 16 provider settings.
6. Save target and verify it appears in the current directory effective list.
7. In section 5, update session communication allowlists only if the new target should be usable by that session.

Expected result:

1. The target appears in `Communication` targets for the directory.
2. The target only works for the selected capabilities.
3. Sessions with matching allowlists can now read/write using that target.

If validation fails:

1. Recheck `address` format (`chat:` prefix for Teams chat IDs, channel IDs for Discord).
2. Re-check capability set for the exact action.
3. For email, re-check provider auth and target mailbox setup in section 16.

## How-to: Send An Approval Notification To The Right People

Use this when approval events need to land in a team channel instead of generic defaults.

Prerequisites:

1. At least one notify-capable communication target is defined.
2. Access to `approval_notification_target_ids` in Configuration.
3. Privileges unlocked for policy edits.

Steps:

1. Open `Configuration` and go to Access Policy (or policy tab view).
2. Confirm the selected directory is correct.
3. Open the approval notification target chooser.
4. Add one or more notify-capable targets (for example an operator channel).
5. Save policy changes.
6. Run a low-risk approval path to generate a real pending record.
7. Confirm the operator receives the notification and that it contains request context.

Expected result:

1. New approval records trigger a host-side alert to selected targets.
2. Missing recipients do not block approvals, but the notification entry stays visible in delivery history.

If alerts do not arrive:

1. Confirm targets still include `notify`.
2. Confirm target still exists in directory and is not accidentally disabled.
3. Confirm the notification action status in logs and Tool Calls from section 7.

## How-to: Review An Inbound Attachment

Use this when you receive a file and want to confirm safety before handling it.

Prerequisites:

1. Message or mail item ID from the expected inbound message/chat thread.
2. Read permissions (`read` target capability) in the active directory and session.
3. Basic file policy enabled for inbound receive.

Steps:

1. Open the source context where the item arrived (session workflow context or message reference).
2. List attachments for the item:
   1. Discord by channel and message ID,
   2. Teams by thread/channel target and message ID,
   3. Gmail/Graph mail by mailbox ID and message ID.
3. Choose the attachment and run download.
4. Record destination artifact id, file name, size, MIME type, and hash.
5. If an agent needs local access, copy the artifact into the communication workspace path and continue from
   section 10.

Expected result:

1. Metadata and file bytes are available as a staged artifact.
2. The artifact can be reviewed before any outbound handoff.

If download is blocked:

1. Confirm attachment policies for MIME type, extension, and max bytes.
2. Confirm the target capability includes `read`.
3. Check for audit denial in section 7 or the host audit log.

## How-to: Approve An Outbound File Send

Use this when a send tool call is waiting for human sign-off.

Prerequisites:

1. Correct directory and session selected.
2. The pending send request is visible in Approvals.
3. Privileges unlocked for approval actions.

Steps:

1. Open **Approvals** and pick the request.
2. Read intended target, message body, destination target IDs, and `artifact_ids`.
3. Compare the request against session destination allowlists in section 5.
4. Check for policy limits on size, MIME, and extension.
5. Enter approver ID and a concrete reason.
6. Approve only if:
   1. target is correct,
   2. recipient is expected,
   3. artifacts are the right files.
7. Continue with the send tool using the same approval request id.
8. Re-open Tool Calls and confirm send status.

Expected result:

1. Approved sends move to execution.
2. Tool Call shows success, or a clear error with the exact failure reason.

If approval is repeatedly denied unexpectedly:

1. Check file-policy mismatch between approved request and actual send payload.
2. Ensure target capability is `write` for top-level send or `reply` for thread responses.
3. Re-request with corrected payload and re-approve.

## How-to: Troubleshoot A Missing Teams Or Discord Wake

Use this when you expect a wake and the agent does not resume.

Prerequisites:

1. Active session in the expected directory.
2. Target service and worker logs accessible.
3. Privileges unlocked for runtime checks.

Steps:

1. Check service health:
   1. Discord wake dispatch,
   2. Discord inbox intake,
   3. Teams inbound polling,
   4. workflow trigger routing.
2. Confirm session is not expired and not revoked in **Sessions**.
3. Confirm channel/chat allowlist still contains the exact target:
   1. Discord wake comes from read-capable `discord_channel_allowlist`,
   2. Teams wake through configured trigger/allowlist paths in the active workflow/session.
4. Confirm wake type:
   1. live command/message wake vs scheduled wake,
   2. one-time or recurring wake status in the session wake ledger.
5. Check whether the incoming message was captured at host side:
   1. use session message/query logs,
   2. confirm no provider-level allowlist or auth block.
6. Verify wake result in audit or Tool Calls:
   1. look for denied reasons before wake dispatch,
   2. look for policy mismatch or credential failure reasons.
7. For Teams, verify delegated auth/session provider settings were recently refreshed and signed in.
8. For Discord, confirm the route is not blocked by source/user allowlist mismatch.
9. Retry the wake source command/message once after fixing any config mistake.

If it still fails:

1. Review scheduled wake config and pending state in section 5.6 for one-time queue aging.
2. Check workflow trigger wiring for provider-neutral or provider-specific wake nodes.
3. Use section 20 for full end-to-end incident capture, including IDs and timeline evidence.
