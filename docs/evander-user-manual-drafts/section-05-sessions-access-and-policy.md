# 5. Sessions, Access, And Policy

Sessions are the operator control surface for what an agent is allowed to do next.
They are useful to think of as a short contract: when the session exists, the agent
can act; when the contract changes, the agent cannot.

This section helps you find sessions, tune what is allowed, apply approvals safely,
control file communication, and schedule wake behavior.

## 5.1 Find A Session

Open **Sessions** when you need to answer:

1. Which active work is tied to which session.
2. Which policy controls are currently active.
3. Whether a session is still usable or already expired.

Use these steps:

1. Open **Sessions** from the left navigation.
2. Verify the selected directory is correct.
3. Use the search box for session ID, VM name, issue label, or model.
4. Toggle **Active** and **Expired** lists.
5. Select a row to inspect its detail card.

What to read in the detail card:

- **Session ID**: the key used by Agents, Tool Calls, and approvals.
- **VM / Issue link**: where the work started and why this session exists.
- **Directory**: scope boundary that controls inherited policy.
- **Model**: current model used for resumption prompts and behavior.
- **Created time**: for handoff and audit context.
- **Expiry time**: when guest access stops unless renewed.
- **Allowed tools**: current allowed actions.
- **Wake schedule list**: any pending recurring or one-time resumes.

If a row is missing, check these first:

1. Directory context.
2. Lock state is irrelevant for reading but required for editing.
3. Manual filters or stale search text.
4. Active list vs Expired list.

If you still do not find the session, capture timestamp, directory, and search terms and use
the troubleshooting workflow in section 21.

## 5.2 Session Expiry And Revocation

Session timing and revocation decide whether an agent can continue receiving prompts.

- **Active session**: session is still accepted by dispatch and wake flows.
- **Expired session**: time window has ended and normal dispatch is blocked.
- **Revoked session**: manually denied future guest access regardless of expiry.
- **Deleted session**: local record is removed from session history.

You normally use:

1. Extend when work is not finished but will run longer than expected.
2. Revoke when work is complete or access must stop immediately.
3. Delete only when cleanup policy requires removing old records.

Keep in mind:

- Revocation is usually the safer cleanup step while preserving evidence.
- Revoking does not erase past records, but it blocks future execution.
- Expired sessions can be extended only through session edit actions and then become usable again.

## 5.3 Allowed Tools

`allowed_tools` is the boundary for what each session can request.

Use it to:

- Stop dangerous tool classes from being called.
- Let one session remain narrow while another can do file, communication, or workflow actions.
- Prevent accidental use of high-cost integrations in the wrong session.

Rules to use:

1. A tool must be in session `allowed_tools` for any call to be accepted.
2. Directory-level policy can deny a tool globally; session-level changes cannot re-enable blocked inherited denies.
3. Some high-impact tools still require human approval even when allowed.
4. Removing a tool from `allowed_tools` removes access immediately for future calls.

What changes in `allowed_tools` should be paired with:

- A quick check of approvals in the same session context.
- A check of related tool-call outcomes in section 7 if a call was denied.

## 5.4 Approval Requirements

Approval settings let you keep a session usable while adding a human checkpoint.

When you enable approval for a tool, that tool can still be in `allowed_tools` but cannot run
without queueing an approval request first.

Three practical settings matter for operators:

1. **Approval TTL**: how long an approval decision remains valid.
2. **Use count**: how many tool calls can reuse one approved ticket.
3. **Payload matching**: whether retries must match the original request details closely.

Common guidance:

- Keep strict matching for external writes, reviews, and releases.
- Use broader matching only when your operator workflow has clear and repetitive idempotent payloads.
- If approval was approved but action is still blocked, check section 6 for status reasons and
  section 7 for execution/audit detail.

## 5.5 Allowed Scripts And Communication

This area controls what in-session helper scripts and communication paths are trusted for a session.

- **Allowed scripts** control which named scripts can be requested by the session.
- **Communication allowlist** controls which configured targets are visible for send/list/download actions.
- **Communication file settings** control inbound and outbound transfer safety:
  - receive enabled / send enabled
  - per-send or per-file approval
  - max bytes
  - allowed MIME types
  - allowed extensions

For file safety:

1. Always set limits before enabling send.
2. Keep executable-style extensions blocked even if extension allowlists grow.
3. Use explicit MIME and extension checks together, not just one.
4. Keep approvals enabled for send actions when the work scope is external.

If you need provider setup, target ownership, outbound behavior, or inbound attachment flows in more depth,
continue in section 15.

## 5.6 Scheduled Wakes

Scheduled wakes let a session resume itself or get prompted on a timer.

Evander supports:

- **One-time wake** with a specific resume time.
- **Recurring wake** from cron-style rhythm.

Each wake entry records dispatch state in a durable ledger that operators can review:

- pending / dispatched
- failed / due / missed or late
- completed or cancelled

Guidance:

1. If a wake is not dispatched, confirm scheduled wake workers are running.
2. If it is repeatedly due or failed, use the retry flow before deleting any schedule.
3. If a session expires while wake entries remain pending, extend the session if additional work is still expected.

Recurring schedule entries stay visible after misses unless removed; this helps you see gaps and operator
interventions over time.

## How-to: Require Human Approval For A Tool

Use this when a tool is currently needed by the session but should never run without
human confirmation.

Prerequisites:

- Correct directory selected.
- Privileges unlocked.
- The target session is active and selected.

Steps:

1. Open **Sessions** and open the target session.
2. In **Model and access**, confirm the tool is allowed.
3. Turn on **Require approval** for that same tool.
4. Open the settings gear next to approval and set:
   1. the approval TTL,
   2. use count,
   3. payload matching strictness.
5. Save session settings.
6. Trigger one expected call in normal agent flow.
7. Open **Approvals** and confirm a pending request appears with the correct session/tool context.

Expected result:

- The tool does not execute until a human approves the request.

Common recovery:

1. If no approval appears, check that the tool remains in **allowed_tools**.
2. If still absent, confirm no hard policy lock blocks it in higher scope.
3. If approvals keep auto-failing, inspect request history using section 7 and continue in section 6.

## How-to: Extend A Session Before It Expires

Use this when work is ongoing and the expiry time is too soon.

Prerequisites:

- Correct directory selected.
- Privileges unlocked.
- The session is found in the active list.

Steps:

1. Open **Sessions**.
2. Search for the session and open it.
3. Read current expiry and confirm active state.
4. Click **Extend**.
5. Enter a new expiry and save.
6. Reload details or return to the list.
7. Verify the new expiry time appears in the session row.

Expected result:

- Session remains active and can resume work until the new expiry.

Common recovery:

1. If the session still appears expired, confirm the extension save completed successfully.
2. If extension is rejected, check lock/state or stale directory assumptions.
3. If active work still cannot resume, inspect worker availability and section 4 for agent lifecycle checks.

## How-to: Revoke A Session After Work Is Finished

Use this when work is complete, handover is done, or no further model access is needed.

Prerequisites:

- Correct directory selected.
- Privileges unlocked.
- Optional: a final check of artifacts or tool-call entries for completeness.

Steps:

1. Open **Sessions** and find the completed session.
2. Review any final status indicators or links to expected artifacts.
3. Click **Revoke**.
4. Confirm the action.
5. Re-open the session in the list and confirm it is no longer usable for new access.
6. If retention policy requires, delete only after handoff documentation is complete.

Expected result:

- The session is blocked from further guest access, but historical records remain.

Common recovery:

1. If the session still accepts resumes, refresh and confirm the status changed from active to revoked.
2. If an agent still runs after revoke, stop the runtime action first and then verify directory policy.
3. For questions about why a denied tool still appears in old evidence, use section 7.

## How-to: Schedule A Recurring Agent Wake

Use this when you want repeated checkpoints, periodic status prompts, or regular
maintenance checks without manually prompting each time.

Prerequisites:

- Correct directory selected.
- Privileges unlocked.
- The scheduled wake worker is enabled by your host setup.

Steps:

1. Open **Sessions** and select a stable session.
2. Go to wake schedule controls.
3. Choose **Recurring** and set:
   1. cron expression,
   2. prompt text,
   3. desired resume mode.
4. Enable the schedule and save.
5. Review the new schedule entry in the wake ledger on that session row.
6. Observe the first two wake runs for timing and result.
7. If a wake is due, late, or failed, use the retry action in the same session area.

Expected result:

- The session receives repeat prompts according to the schedule, and wake history is visible.

Common recovery:

1. If no wakes run, confirm scheduled wake worker is running and resume mode matches expectations.
2. If retries continue to fail, disable and re-enable with a corrected prompt.
3. If wake history shows "missed or late" repeatedly, reduce schedule intensity and confirm directory scope.

## How-to: Allow A Session To Receive Or Send Files

Use this when a session needs in-session file transfer or outbound artifact sharing.

Prerequisites:

- Correct directory and unlocked privileges.
- Communication target and file policy reviewed in configuration context if needed.
- A clear understanding of who should receive or send files.

Steps:

1. Open **Sessions** and pick the target session.
2. Under communication controls, open allowed targets and target list.
3. Enable receive and/or send as needed.
4. Set max file bytes and confirm limits are realistic for real outputs.
5. Set allowed MIME types and extensions.
6. Keep blocked extensions in place for executable and unknown script types.
7. If send is enabled, set approval expectations so outbound actions are never silent.
8. Save the policy.
9. For outbound flow, run a controlled approval test path and verify:
   1. request appears in **Approvals**,
   2. final send is in Tool Calls with matching result context.

Expected result:

- File exchange works only for approved targets and matching file-type rules.

Common recovery:

1. If inbound or outbound is blocked, confirm the size/MIME/extension policy in the session itself.
2. If approvals are missed, check section 6 and section 7 before changing limits.
3. If this is a temporary test, revert send settings back to standard limits afterward.
