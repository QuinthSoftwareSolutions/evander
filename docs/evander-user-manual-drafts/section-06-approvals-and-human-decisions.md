# 6. Approvals And Human Decisions

Approvals let you keep automation fast while preserving human control. They are the pause point for
actions that need a person to confirm intent before a tool runs.

Use this section when you want to handle queued requests, confirm a safe decision, or investigate a
failed approval flow. For policy configuration, keep section 5 nearby; for execution traces, follow
to section 7.

## 6.1 When Approval Is Required

Approval is usually triggered by one of these layers:

1. Directory policy requires it. This is often used for sensitive or broad operational areas.
2. Session policy requires it for specific tools or communication flows.
3. The tool path is safety-gated and still needs human confirmation even if it is generally
   allowed.

In practice, an action can be blocked by policy before it reaches approval:

- The action is not in the session's allowed tools.
- Directory or session rules deny the tool family.

If the action is allowed but still needs review, Evander creates a human decision record in
Approvals. The safest way to predict this is to set expectations in section 5 before running the
task.

What to expect:

- High-risk and external-facing actions are often approval-gated first.
- Approval does not remove the underlying policy checks. It only adds a signed human checkpoint.
- A denied action stays in the system as evidence and should be reviewable with related records.

When you see frequent queue growth, review session rules and policy defaults in section 5 before
adding temporary operators to the queue.

## 6.2 Approval Queue

Open **Approvals** from the left navigation to work from a single decision list.

Use filters to focus the queue:

1. `status` to separate Pending, Approved, Denied, and failed/expired records.
2. `session` and `VM` to narrow by active work item.
3. `tool` to find one risky tool family.
4. `age` to surface items waiting the longest.
5. directory scope to avoid deciding on another tenant or project by mistake.

Queue health is a practical operating signal:

- Low and steady pending count is normal during heavy tool use.
- A rising pending count with little progress usually means one of:
  1. missing staffing,
  2. too-short lock windows,
  3. too-strict payload settings,
  4. blocked downstream delivery of notifications (section 15).

When you open a row, review the item in this order:

1. summary text and target action,
2. intended effect,
3. status and age,
4. related session/workflow context.

If you cannot identify the owner or purpose of a request quickly, stop and return only once you
have directory, session, and actor context.

## 6.3 Reviewing A Request

The request row is the operator decision surface; keep review structured:

1. Confirm request scope by reading session, directory, tool, and related entity fields.
2. Confirm what the action is expected to do from the request summary and intended effect.
3. Check the `digest`, which identifies the expected payload shape for this approval.
4. Review redacted payload for context and obvious safety flags.
5. Open the approval payload diagnostic when there is mismatch risk.
6. Read the decision history so you can see who approved, denied, or reopened context.
7. Use the matching audit context to confirm this request is tied to the call path you expected.

Why redacted data is normal:

- Redaction protects secrets and internal system fields in operator surfaces.
- Redacted records can still be enough for safe decisions.
- Missing payload details do not automatically imply missing approval, but they do require careful
  reliance on summary, context links, and scope.

If you need original data for a secure internal investigation, use the cross-record workflow in
section 7 and the local incident escalation path in section 21.

## 6.4 Approve Or Deny

Approving or denying is the final step in a gated action. Use this order:

1. Confirm your directory and lock state are correct.
2. Confirm you can read the request context completely enough for a decision.
3. Unlock privileged operations if the action buttons are hidden.
4. Enter approver identity (operator name or team handle).
5. Enter a short reason before submission.
6. Choose **Approve** or **Deny**.
7. Re-check status changes in the same record and in related Tool Calls.

Decision quality habits:

- Keep reasons specific enough for audits, for example:
  "Reviewed target path and approved send to known workspace."
- One-line reasons are better than blank or generic text.
- If you deny, include what change would be required to retry safely.

Remember:

- Approve or deny only affects the request outcome.
- It does not grant unlimited retries; follow-up logic is enforced by policy settings.
- A denied request remains reviewable; do not delete it as a cleanup shortcut.

## 6.5 Approval Diagnostics

Use this section when the outcome is not expected.

### Stale or Expired

An approval can become too old to use. Typical operator checks:

1. Check status and expiry timestamps.
2. Compare current queue age and session timing.
3. Re-request or trigger a fresh retry path as designed by the calling flow.
4. Re-open if needed with corrected context.

### Denied

A denied request can reflect risk policy, reviewer judgment, or changed conditions.

1. Check decision reason.
2. Confirm required manual change is understood (payload, tool, scope, or recipient).
3. Run the safe retry after the controlling operator action is complete.

### Missing

Missing means no active matching approval was available when the action retried.

1. Confirm the original request was tied to the right session and action.
2. Verify you did not review a different but similarly named request.
3. Ask whether the retry path includes the exact request ID and same context.

### Payload-Mismatched

This is a frequent source of confusion after approval. It means the later attempt differs from the
approved content.

1. Compare the approved digest against the latest attempted intent.
2. Confirm whether the change was intentional; if not, deny and re-request.
3. If intentional, approve a fresh request for the new payload before retrying.

For details on request-to-call linkage and diagnostics fields, use section 7 and the end-to-end flow
in section 20.

## How-to: Review And Decide A Pending Approval

Use this when an operator is waiting on a pending request and the work cannot progress.

Prerequisites:

- Correct directory selected.
- Privileges unlocked for mutation.
- You understand the underlying task context.

Steps:

1. Open **Approvals**.
2. Filter to `Pending`.
3. Select the request by session, VM, or tool.
4. Read request summary, intended effect, and directory context first.
5. Review the digest and redacted payload area.
6. Open the linked audit context (if present) to confirm the request is the one you expect.
7. Enter approver identity and reason.
8. Click **Approve** or **Deny**.
9. Re-open the request detail and confirm status changed.
10. Check the linked Tool Call record for execution result or retry status.

Expected result:

- The request state moves to approved or denied, and the calling workflow/agent action either
  resumes, retries correctly, or stops with an expected status.

If you cannot decide safely:

1. Pause and return when the session context is fully known.
2. Ask a second operator using the handover process if a policy boundary may be disputed.

## How-to: Investigate Why An Approved Action Still Failed

Use this when the request is approved but the action did not succeed.

Prerequisites:

- You have the approval ID and session context.
- Privileges unlocked for review actions.
- Related tool-call or workflow entries are visible.

Steps:

1. Open the approved request and note `approved`, `expired`, and `use` metadata.
2. Open the related Tool Call link from the request.
3. Compare timestamps and tool names:
   1. Was the retry tied to the same request?
   2. Did it use the same session?
   3. Did the digest and matching mode remain valid?
4. Check the approval payload diagnostic for stale/mismatch clues.
5. Check status path:
   - `denied` after review,
   - `expired` before retry,
   - missing approval record at retry time,
   - or payload mismatch on retry.
6. If the payload changed, deny further retries, close the old request, and request a fresh approval
   with corrected values.
7. If expiry is the issue, decide whether a fresh request under updated policy is needed.
8. Re-run the action with the expected request path and monitor Tool Calls again.

Expected result:

- You have a specific reason (stale, missing, mismatch, expiry, denial) and a safe repeat path.

Common recovery:

- Keep one retry policy in place and avoid repeatedly retrying by hand after mismatch until root cause is
  corrected.

## How-to: Set Up Approval Notifications

Use this when approvers should be notified as soon as new requests enter the queue.

Prerequisites:

- Access to Configuration controls.
- At least one notify-capable communication target defined.
- Privileges unlocked for config changes.

Steps:

1. Open **Configuration** and go to the directory-specific policy area.
2. Open the access policy controls for approval notifications.
3. Choose notify-capable communication targets from the known list.
4. Add one or more target IDs to the approval notification target list.
5. Save the policy.
6. Trigger a low-risk approval path (for example a non-production test session) and confirm a request appears.
7. Verify notification delivery in the communication target output or target provider channel.
8. Record the expected route and test time in handover notes.

Expected result:

- New approval requests notify the selected recipients.
- If delivery fails, pending approval state stays unchanged and requires operator review in this screen.

If notifications are not arriving:

1. Confirm target capabilities include notify.
2. Confirm target IDs are selected in this directory or session override.
3. Confirm the user is not filtering notification channels too narrowly.
4. Use section 15 for deeper communication troubleshooting if target integration is suspect.
