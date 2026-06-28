# 8. Issues And Work Intake

Issues are how operators bring new work into Evander in a consistent, traceable way.
An issue record is a small ticket-like entry you control from Admin Web: it stores the task
description, repo source, and execution hints you want the workspace to use.

Use this section to create intake records, start work from them, and follow the first lifecycle after
an issue is launched.

## 8.1 Issue List

Open **Issues** to see local records available for your selected directory.

The list helps you answer:

1. What work is currently queued or active.
2. Which issue belongs to the selected directory and scope.
3. Which issue already has a VM or session linked.
4. Which issues are editable or already locked by system state.

For fast triage, use the state filters at the top:

1. **Active** to focus on in-progress and recently started items.
2. **All** to audit historical and inactive items.
3. A specific state if your deployment exposes more granular categories.

When you open an issue row, check these fields first:

1. Title and source.
2. Last-updated time.
3. Repo source.
4. Directory and YAML path.
5. Body text.
6. Metadata block (if present).

If actions are unexpectedly missing in the list:

1. Confirm directory selection.
2. Confirm the lock state is correct for mutation.
3. Refresh the page.

## 8.2 Create Or Import An Issue

Use manual entry when you are starting from a new internal request.
Use import when you already have a prepared issue file from another system or an exported template.

Use **New issue** for both cases.

For manual entry, fill:

1. **Title**: one clear sentence.
2. **Repo source**: where the work should run from.
3. **Body**: task goal plus constraints.
4. Optional VM bootstrap choices (for example dotnet, nuget, playwright, mssql) if required.

For import, choose the issue YAML file and let the form prefill fields.

Common safety expectations:

1. Keep one objective per issue.
2. Put only actionable bootstrap options in the setup part.
3. Save the issue before launching multiple downstream actions from it.

If an issue is linked to active work, the UI may mark it as non-editable.
In those cases, treat it as a reference row and start from a new issue entry or a defined follow-up path.

## 8.3 Start Work From An Issue

You can start execution from an issue record by creating a new VM or by creating work immediately during
issue creation.

Before launch, confirm:

1. Directory and lock state.
2. Correct issue selection.
3. Repo source and bootstrap options reflect the task.

Issue launch behavior:

1. Issue data is used to prefill the work target setup.
2. Launch inherits the current directory unless the issue form has a more specific value.
3. The system creates a target record you can move into the execution flow from.

How to think about VM launch:

1. Use immediate launch from issue creation when you want no delay between intake and execution.
2. Use later launch when priority or repo source needs review first.
3. Use bootstrap options to install only what this issue needs.

For target-specific differences (VM versus container backend), do not change assumptions in this section; use section 9 for target behavior details.

## 8.4 Track Issue Follow-Up

Once work starts, issue tracking continues across multiple pages:

1. **VMs** for runtime status, power state, and host machine details.
2. **Agents** for control state and live session context (see section 4).
3. **Sessions** for expiry and access policy on the running work context.
4. **Tool Calls** to audit what the agent requested and how calls were executed.
5. **Artifacts** when outputs are published.
6. **Reports** for workflow-level outcome signals and repeated failure patterns.

Use this sequence to keep handoff clean:

1. Confirm the issue to VM or session link is visible and stable.
2. Check if any approvals are pending for high-risk actions.
3. Capture artifact/result references once work reaches expected checkpoints.
4. Record issue outcomes and residual risks in notes before handoff.

If a problem appears after issue launch, use section 7 for operational trace reconstruction and
section 20 for end-to-end incident analysis.

## How-to: Create A Local Issue And Start Work Immediately

Use this when you want to capture a task and begin execution in one operator action.

Prerequisites:

1. Correct directory selected in the shared shell.
2. Privileges unlocked if your environment requires mutation unlock.

Steps:

1. Open **Issues**.
2. Click **New issue**.
3. Enter a clear **title**.
4. Set the correct **repo source**.
5. Write the task goal in the **body**.
6. Choose minimal bootstrap options needed for this task.
7. If supported in your deployment, enable immediate VM startup.
8. Save the issue.
9. Confirm the created VM or agent target in the related tab.
10. Open **Agents** or **VMs** to verify the target is running and linked.

Expected result:

1. New issue appears in the list.
2. A matching execution target is started from that issue.
3. The running target links back to the issue context for traceability.

If launch does not start:

1. Confirm bootstrap settings are supported in the selected directory.
2. Re-open the issue and check for validation errors.
3. Retry from **Issues** or open directly in section 9 for target-specific checks.

## How-to: Start A VM From An Existing Issue

Use this when an issue already exists and you now need to begin VM-backed execution.

Prerequisites:

1. The issue row shows the correct task and scope.
2. Privileges are unlocked.

Steps:

1. Open **Issues** and select the issue.
2. Verify repo source, title, and directory.
3. Open the action to start work from the selected issue.
4. Choose launch bootstrap options.
5. Confirm launch settings.
6. Start the VM.
7. Return to the VM row and wait for live status.
8. Open the linked session or agent entry for policy and thread context.

Expected result:

1. VM is created for that issue.
2. The issue row shows progress toward a running target.

If launch fails:

1. Check lock state and directory scope.
2. Verify the issue still allows launch actions.
3. Validate that the repo source is reachable.
4. Continue with the operator follow-up flow in this section, or inspect execution target details in section 9.

## How-to: Edit Or Delete A Local Issue

Use this when the intake details need correction or when cleanup is required.

Prerequisites:

1. Correct directory selected.
2. Privileges unlocked.
3. You have checked whether work has already started from the issue.

Steps:

1. Open **Issues**.
2. Find the target issue and confirm state.
3. If the issue is still editable, open **Edit**.
4. Update title, body, repo source, or bootstrap note fields only.
5. Save the update.
6. If the issue must be retired and no active context depends on it, use **Delete**.
7. Confirm deletion only after understanding downstream impact.

Expected result:

1. Edits apply immediately to the issue row.
2. Deletions remove only the local issue record that you selected.

If deletion is blocked:

1. Check whether the row is marked non-editable/non-deletable.
2. Create a replacement issue if the operational context must continue.
3. Use related active session or report evidence to hand off safely.
