# 20. Common End-To-End Tasks

This section brings together daily operator workflows that touch more than one Evander page. Use these recipes when a single feature page is not enough and you need a full incident or delivery flow.

Use section 20 as the bridge between:

1. Live operations and status in section 3.
2. Work execution and session control in sections 4, 5, and 8.
3. Safety gates in section 6 and evidence in section 7.
4. Workflow design and change control in section 11.
5. Team and operator coordination in sections 12, 15, and 17.

## 20.1 Investigate A Failed Automation

Use this when an external action was expected and did not complete, or when a run appears healthy in one tab but failed in another.

Execution order:

1. Confirm the directory and lock state from the shared header first.
2. Start from Dashboard warnings and identify:
   1. Which failure signal appeared (alert badge, missed action, stale card, or spike).
3. Open Workflow current runs and find the failing run row.
4. Read node-level failure cause in inspect mode.
5. Open the linked Tool Calls row for the same time window.
6. Open the related Session or Agent record to confirm who owned the action.
7. Check Approvals if the node required human confirmation.
8. Check Reports for trend confirmation before taking rollback action.
9. If applicable, open VMs for host/runtime state and then stop.

Decision rule:

1. If the issue is missing data or a transient timeout, trigger a narrow retry only after approval/timeouts are validated.
2. If repeated across runs, route to workflow diagnostics and temporary risk mitigation (pause trigger or narrow filters) before broad policy edits.

## 20.2 Create Work From A Local Issue

This path links intake, launch, and oversight for work that starts as a local request.

End-to-end flow:

1. Create or open an issue with scope, repo, and constraints.
2. Verify that the selected directory is correct.
3. Decide the launch mode:
   1. Start VM/session immediately from the issue, or
   2. Launch through later follow-up in case priority is uncertain.
4. Confirm bootstrap options are narrow and necessary.
5. Start the target.
6. Move to Agents or VMs immediately to verify target is running.
7. Open Sessions if policy or expiry constraints matter.
8. Keep the issue link visible in notes while the work is active.

Expected exit state:

1. Issue is linked to a live target.
2. Session and directory are correct.
3. You have a known check-in interval and a clear owner for the next escalation.

## 20.3 Prepare Agent Output For Human Review

Use this when agent outputs need to be consumed by someone outside the active run.

Recipe:

1. Open the relevant Agent or Session.
2. Confirm the work has reached a stable checkpoint (no active sensitive in-flight action).
3. Open Artifacts and download the relevant output bundle.
4. Capture logs for key steps in Tool Calls and Execution history.
5. Review verification markers in the run details (tests, status flags, and completion summary).
6. Note limitations:
   1. redactions in payloads,
   2. unresolved checks,
   3. any pending approvals.
7. Move to approval/review pages based on sensitivity.

Good review practice:

1. Always include exact IDs (issue, session, run, or artifact) in handover text.
2. Separate raw output from assessment notes.

## 20.4 Approve A Sensitive Action

Use this when the action path explicitly requires human intervention.

Flow:

1. Open Approvals and filter for the relevant directory and status.
2. Sort by most recent and capture one high-risk item.
3. Open the request and read:
   1. tool,
   2. requested effect,
   3. session/directory scope.
4. Review the redacted payload and the approval payload diagnostic.
5. Confirm the linked Tool Call and session context.
6. Enter approver identity and concise reason.
7. Approve or deny only if the chain is understood.
8. Re-open the linked Tool Call after the decision.
9. Watch for follow-up retries, expiry refresh, or mismatch messages.

If denied or stale:

1. Capture the reason in the handoff notes.
2. Re-run the specific step with corrected context.

## 20.5 Roll Out A Workflow Change

Use this path for small production-safe workflow edits.

Recommended sequence:

1. Open Workflow and pick the target graph.
2. Duplicate intent in a short note before editing.
3. Make the smallest possible edit (one node or one binding at a time).
4. Validate diagnostics and fix unresolved bindings.
5. Save and confirm revision update.
6. Watch Current runs for at least one expected healthy transition.
7. Open linked Runs/Tool Calls if side effects touch external tools.
8. If failure appears, immediately pause the trigger and use section 20.1 to trace.

Rollout rules:

1. Keep non-essential changes out of the same edit window.
2. Have a rollback plan:
   1. disable the trigger,
   2. restore previous graph revision from the page controls.
3. Capture report entry ID and before/after behavior in the operator note.

## 20.6 Handle An Operator Handover

Use this when ending a shift, covering incidents, or changing operators for a live directory.

Minimum handover package:

1. Directory and current lock state.
2. Active agents and sessions, including expiry and policy notes.
3. Pending approvals and any decision blockers.
4. Failed or retried workflow runs in the last few hours.
5. Worker health and any warning banners.
6. Open issues, active incidents, and Team tasks.
7. Artifact references and required follow-ups.

Suggested cadence:

1. Capture status from Dashboard first.
2. Confirm each item with linked detail pages.
3. Add a short "next action owner" line for each open item.
4. Share only non-sensitive IDs and results.
5. Re-lock UI access after handover is posted.

## How-to: Investigate A Failed Automation End To End

Use this when an expected external result failed and no single page explains why.

Prerequisites:

1. Correct directory selected in section 2.
2. Read access to Workflow, Tool Calls, Approvals, Sessions, Agents, and Reports.
3. Optional privilege unlock for any needed follow-up action.

Steps:

1. Open Dashboard and take a snapshot of current error signals.
2. Open Workflow current runs and copy the failing run ID.
3. Enter inspect mode for the failed run.
4. Record the first failed node and the node output or binding error.
5. Open linked Session and Agent rows from that run.
6. Open Tool Calls and filter to the same session, actor, and time window.
7. If the flow crossed an approval gate, open the related approval row and check expiry, digest, and mismatch flags.
8. If the action involved a target environment, open the VM and confirm target status.
9. Open Reports and check whether the failure is isolated or recurring.
10. Open a new handover note with:
    1. timeline,
    2. stable IDs,
    3. likely root cause category,
    4. required operator decision.

Expected result:

1. A clear chain from signal -> workflow run -> tool call -> approval/target state.
2. A single recommended next action, not multiple random retries.

Common recovery:

1. If approval payload mismatch appears, stop re-running from UI and request corrected approval.
2. If repeated missing target calls appear, halt workflow trigger and move to targeted diagnostics.

## How-to: Run A Human Review Cycle

Use this when work should be checked by a person before the result is accepted.

Prerequisites:

1. Target artifact and session references.
2. Access to Artifacts, Tool Calls, Approvals, and Sessions.
3. Clear ownership for final decision.

Steps:

1. Open the artifact list and download the latest relevant output package.
2. Capture the session and workflow run IDs from the run list.
3. Open Tool Calls for the run window and gather outcome flags.
4. Read the run verification summary and note missing validations.
5. If approvals were required, open the decision path and confirm status/identity.
6. Create a short review note with:
   1. what changed,
   2. what was validated,
   3. known risks,
   4. recommended disposition.
7. Choose review outcome:
   1. approve and proceed,
   2. deny with required changes,
   3. request additional run with clearer evidence.
8. Record decision outcome in handoff notes and link IDs.

Expected result:

1. The team can act from a single review record.
2. Review decisions are tied to concrete IDs and evidence, not memory.

## How-to: Perform A Shift Handover

Use this at shift boundaries or whenever one operator leaves active work running.

Prerequisites:

1. Shared directory and current operator context.
2. Dashboard and detail pages available in read mode.
3. Permission to add a handoff note or internal issue commentary.

Steps:

1. Open Dashboard and read:
   1. workers,
   2. active runs,
   3. approvals queue,
   4. recent warnings.
2. Confirm active directory, unlocked privileges, and any pending privilege-sensitive actions.
3. Open active sessions and mark those near expiry.
4. Open open approvals and identify blockers by session ID.
5. Open recent failed runs and note recurrence rate from Reports.
6. Open Team-related active work if configured, and verify outstanding deliverables.
7. Confirm artifact links needed by the next operator are copied.
8. Write a handover entry with:
   1. current state,
   2. top risks,
   3. owner suggestions,
   4. exact IDs for every open item.
9. Lock privileged operations once handover is complete.

Expected result:

1. Next operator receives enough context to continue without re-discovering evidence.
2. Open action items have owners and one clear next step each.
