# 21. Troubleshooting

This section is for operator-facing diagnosis of common symptoms without assuming deep implementation knowledge. Use it as a quick path to the right evidence first, then follow the cross references for full repair steps.

Use section 20 (Common End-To-End Tasks) when a single symptom touches multiple pages.

If the UI does not match the behavior described here, treat the live UI and host state as authoritative and gather evidence before changing settings.

## 21.1 Empty Or Missing Pages

This usually means one of these is true:

1. The page is scoped to a different directory than the one you selected.
2. Your account currently has read-only mode or locked privileges.
3. A service used by that page is not running or not reachable.
4. A feature is not available for your deployment (for example add-on-specific pages).

Quick check:

1. Confirm the directory in the shared header.
2. Open the same area from the left navigation and wait for one refresh cycle.
3. Click **Refresh** on Dashboard or the target tab.
4. If still empty, open a detail tab for direct error text instead of staying on the summary card.
5. Confirm add-on visibility if the page is feature-specific.

Cross-check:

1. If Dashboard panels are missing, open section 3 procedures and then open the related tab.
2. If an Add-on page is missing, use section 13 (Team Add-On).

Expected result:

1. The page shows data or a clear backend message explaining why it is unavailable.
2. You know whether the next step is privilege, directory, or service recovery.

## 21.2 Disabled Buttons

Common causes are safety and permission controls, not UI defects.

1. Privilege lock is enabled for that session.
2. Your user role does not include the action.
3. You are viewing the wrong directory or a non-editable scope.
4. A form is incomplete or invalid.
5. The page state is stale while edits are in progress.

First pass:

1. Check privilege lock and unlock if required.
2. Verify directory and target record selection.
3. Look for inline field errors and required-field hints.
4. Perform a manual refresh.
5. Retry once, then check if a tooltip or status message appears.

If still blocked:

1. Review user permission paths in section 17 (Users, Groups, And Permissions).
2. Compare workflow editing expectations with section 11 (Workflows) if saving is blocked during graph edits.

## 21.3 Workflow Did Not Run

When automation is expected but no run is produced, treat the issue as staged and inspect three control points:

1. Trigger state and run start conditions.
2. Worker and orchestrator health.
3. Approval, tool-call, and target-side evidence.

Investigation order:

1. Open **Workflow** and confirm the trigger node is enabled.
2. Open **Current runs** and check whether a run started recently.
3. Open **Workers** and verify runtime processes are active (especially trigger/effect orchestration workers).
4. Open **Tool Calls** filtered by expected workflow or time window.
5. Open related **Approvals** if the path contains a human gate.
6. Open **Reports** to see whether the behavior is isolated.

If the trigger was disabled or queued:

1. Do not toggle unrelated settings.
2. Follow section 16 (Configuration) for worker and trigger-related config checks.
3. Re-run a narrow end-to-end trace from section 20.

## 21.4 Agent Did Not Respond

This section is for agents that look idle while action was recently expected.

1. Confirm session is not expired, paused, or revoked.
2. Confirm the selected directory and agent are correct.
3. Verify that the target (VM or container worker) is in expected state.
4. Check wake behavior if this is a scheduled resume case.
5. Review latest tool calls for model connectivity and blocked approvals.

Operational sequence:

1. Open **Agents** and **Refresh** target state.
2. Open the selected agent and check recent logs and steering state.
3. Open **Sessions** for expiry and approval requirements.
4. Open **Scheduled wakes** (if configured) and check dispatch state.
5. Open **Tool Calls** for failed or pending calls on the same session.

If no useful data appears in Agent logs:

1. Open **Workflow** for any node waiting on this session or dispatch ID.
2. Review relay-like symptom pages through the operator support process in section 19 (Assistant) only for guidance, not as a substitute for evidence.
3. Prepare escalation notes if the gap remains.

## 21.5 Approval Problems

Approval issues are usually due to queue state, policy boundaries, or payload matching.

1. Request stays pending longer than expected.
2. Approval appears expired or automatically denied.
3. Payload mismatch is shown during retry.
4. Requester received no notification.

Use this path:

1. Open **Approvals** and confirm filter by directory, session, and status.
2. Open the request and review summary, intended effect, and reason fields.
3. Open related **Tool Calls** and compare the linked IDs.
4. If payload mismatch is shown, request a corrected request flow rather than repeated retries.
5. If notifications fail, verify communication targets in section 15 (Communication And Notifications).

## 21.6 Configuration Save Problems

Most save failures are controlled by one of three conditions:

1. Validation errors in required fields.
2. Revision conflict because another operator updated the same page.
3. Directory-level inheritance or denied overwrite path.

Recovery flow:

1. Note the exact field and error message.
2. If the page says "changed while editing," reload, compare, then reapply minimal edits.
3. Confirm all required values are present and valid.
4. Retry after the targeted reload, not after broad unrelated changes.
5. Save again only when diagnostics are clear.

If the page still fails:

1. Capture a screenshot and validation summary.
2. Check section 16 (Configuration) for lock, revision, and inheritance behavior.

## 21.7 Team Add-On Problems

Team workflows can appear disconnected from operator views when state or wake pathways are not aligned.

Typical symptoms:

1. Teams tab is missing.
2. Team state appears empty.
3. Agents do not wake from team messages.
4. Review or promotion steps do not complete.
5. Team Chat is not visible where expected.
6. Deliverables are in stand-down state.

Action sequence:

1. Confirm Team Add-on scope and directory scope are correct.
2. Verify expected Team permissions and operator role in section 17.
3. Open section 13 (Team Add-On) and follow the specific Team visibility and wake checks there.
4. Cross-check worker-level context in section 18 (Workers And Background Services).
5. Confirm issue links and communication targets are configured for the same directory.

If only one directory is affected:

1. Compare two recent actions in two different directories to isolate deployment versus directory config.
2. Use the handover/review flow only with exact Team IDs and run/session references.

## 21.8 When To Escalate

Escalate when one page shows a result but related pages disagree, when data is missing across multiple sections, or when behavior is reproducible after basic checks.

Send a support packet with:

1. Current date/time and timezone.
2. Directory, operator user, and browser session context.
3. Relevant IDs:
   1. Session ID
   2. Agent ID
   3. Workflow run ID
   4. Tool call ID
   5. Approval ID
   6. Worker name
   7. Issue or wake request ID if used
4. Exact symptom timeline (when first seen, expected behavior, actual behavior).
5. Screenshots of:
   1. Dashboard
   2. The failing detail page
   3. Any error state banner.
6. Steps already performed and result from each step.
7. Last known change in operators' workflow, permissions, or target config.

If the incident touches multiple systems or production workloads, include a single-line impact statement and preferred recovery window.

## How-to: Recover From An Empty Page

Use this when one or more pages are blank or show no rows after navigation.

Prerequisites:

1. Read access to the target page.
2. Shared header showing current directory.
3. Access to both Dashboard and the related section page.

Steps:

1. Confirm the directory matches the scope of the missing page.
2. Refresh the current page and, if needed, the whole browser tab.
3. Click **Refresh** in Dashboard, then open the linked detail page.
4. Open the same page via deep link hash route.
5. Compare with a known working directory.
6. Check for feature scope (for example Add-on only pages).
7. Re-check section availability after unlock/lock state change.

Expected result:

1. The page populates correctly or shows a clear service-level or permission message.
2. You can name one next section to inspect (Dashboard, Workers, or section-specific page).

Common recovery paths:

1. If only one directory is blank, check that directory access.
2. If only one browser session is blank, log out, log in again, and retry.
3. If still empty, prepare escalation per 21.8.

## How-to: Find Why A Button Is Disabled

Use this when a control looks disabled and you need the reason quickly.

Prerequisites:

1. Operator account with that page visible.
2. Section loaded in the correct directory.

Steps:

1. Check the privilege lock icon/button in the header and unlock if allowed.
2. Check whether your role has the required permission for that section.
3. Select a valid item first (many actions target a row or session).
4. Resolve any form validation errors in required fields.
5. Try once after a manual refresh.
6. Check if the action target is outside the selected directory.
7. Check if the action belongs to a disabled global state (for example trigger disabled, workflow not editable).

Expected result:

1. Either the button becomes active or the exact prerequisite is identified.
2. You have a minimal corrective step, not broad configuration changes.

Common recovery paths:

1. Re-open the related section and confirm save prerequisites.
2. Follow section 11 if edits do not commit due to graph validation.
3. Log and escalate if no prerequisite change resolves the state.

## How-to: Diagnose A Workflow That Did Not Fire

Use this when a workflow should have started but no automation run appears.

Prerequisites:

1. Directory and privileges confirmed.
2. Access to Workflow, Tool Calls, Workers, and Approvals pages.

Steps:

1. Open **Workflow** and confirm the target trigger is enabled.
2. Open **Current runs** and capture the expected run window.
3. Open **Workers** and ensure trigger/effect workers are healthy.
4. If no run exists, open **Tool Calls** and filter by query/window.
5. If run exists with an error, open inspect mode and capture first failing node.
6. Open **Approvals** only if the graph includes required gate steps.
7. If it is now running elsewhere, check time-zone drift and scheduling expectations in wake/cron definitions.

Expected result:

1. A root cause category is established: trigger off, worker unhealthy, approval block, or missed scheduling.
2. Next action is limited to one repair path.

Common recovery paths:

1. Correct only the minimum trigger/worker issue.
2. Retry through section 20.1 if a narrow reproduce window exists.

## How-to: Diagnose A Silent Agent

Use this when no response appears in recent agent activity despite expected action.

Prerequisites:

1. Access to Agents and Sessions.
2. Target agent/session IDs available.

Steps:

1. Open **Agents** and refresh the selected agent.
2. Check if target status is active and associated with the expected directory.
3. Open the latest log lines and steering state.
4. Open **Sessions** and verify expiry, allowed tools, and approval requirements.
5. Open **Scheduled wakes** and check dispatch status if this was a resume.
6. Open **Tool Calls** for blocked/failed requests on the same session.
7. Confirm related Approvals for payload mismatch or stale approvals.
8. If no change appears in the same page, open a small reproduction attempt in section 20.

Expected result:

1. You identify whether this is policy gating, target inactivity, wake issue, or tool-call/approval blocking.
2. You can choose either a direct retry or a controlled escalation.

Common recovery paths:

1. Correct session or wake gating state in-place.
2. Avoid duplicate prompts while state is unknown.

## How-to: Prepare A Support Escalation

Use this when troubleshooting stalls after the basic checks above.

Prerequisites:

1. You have completed the relevant checks in sections 21.1 to 21.7.
2. You can capture screenshots.
3. You can copy IDs from the related records.

Steps:

1. Take one screenshot per page:
   1. Dashboard
   2. Failing detail page
   3. Related relation targets (for example Tool Calls, Approvals, Sessions, Workflow).
2. Record these IDs exactly as shown:
   1. Directory
   2. Session ID
   3. Agent or VM ID
   4. Workflow run ID
   5. Tool call ID
   6. Approval ID
   7. Worker name
   8. Issue/wake request ID if applicable
3. Write a short time line with:
   1. First observed time
   2. Last successful state
   3. Reproduction attempt steps and result
4. List the checks already done and what changed.
5. Include recent configuration or permission changes.
6. Add the current impact statement (who is blocked and why).
7. Open a single escalation record rather than multiple partial notes.

Expected result:

1. Support receives a complete, reproducible packet.
2. The first round of triage is faster and focused.
3. Operators spend less time repeating basic checks.
