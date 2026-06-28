# 9. Execution Targets

Execution targets are the environments where agent work actually runs. In Evander today, operators mainly use two user-facing target types:

1. Virtual machines (VMs), usually for longer or heavier tasks that need a full guest environment.
2. Container workers, for disposable container execution with tighter, bounded defaults.

This section helps you decide what to inspect and how to operate safely. For creating sessions, use **Section 4: Working With Agents** and **Section 5: Sessions, Access, And Policy**. For background worker status and health, use **Section 18: Workers And Background Services**.

## 9.1 Choosing An Execution Target

Pick the target type that matches what you want to run:

1. If you need to see a live VM-style guest with network bootstraps, traditional tooling compatibility, or predictable full-host behavior, use a VM-backed target.
2. If you need quick disposable sessions with container-backed execution, use container worker-backed targets.
3. If the selected directory or provider policy is set to `libvirt_vm`, most operator workflows route to VMs.
4. If the provider policy is set to `container_worker_vm`, operator sessions and related actions are executed as container jobs.

Operator checks before proceeding:

1. Confirm directory context is correct.
2. Confirm unlocked state if you plan to create or change anything.
3. Confirm the current backend expectation from your session create flow.

Where to find each type:

1. **VMs page**: visible and useful for VM-backed work, usually on `libvirt_vm`.
2. **Agents and Sessions**: both show target identity, session links, and state across both target types.
3. **Container worker action context**: shows execution/job evidence in execution-oriented views.

If a target appears in a different shape than expected, pause and verify whether you are using the right backend for the directory and session.

## 9.2 Virtual Machines

VM targets are useful for full guest-style execution and traditional lifecycle actions.

What operators usually inspect:

1. Session linkage: which session is attached and who started it.
2. Directory and provider: so you know scope and ownership.
3. Power state and timestamps: whether the VM is running, stopped, or recently changed.
4. IP and labels: for quick triage of connectivity and routing.

Where to operate:

1. Open **VMs** for direct target management.
2. Open **Agents** for target-linked context and quick policy/approval review.

Lifecycle controls (mutations):

1. **start**
2. **shutdown**
3. **destroy**

Use lifecycle actions only after confirming you are on the right VM and the session is the expected one.

Important reminder:

- Destroy removes VM state managed by the control plane for that target. Use with care.
- If work came from an issue, cross-check **Section 8: Issues And Work Intake** for expected start context before deleting.

## 9.3 Container Workers

Container workers run sessions through the worker daemon and container jobs.

What operators usually inspect:

1. Worker-backed execution status on the session and agent views.
2. Execution history and thread continuity for the same session.
3. Cleanup state, especially after stop/completion/failure.

How container logs are presented:

1. **Executions**: raw job-level log stream by run.
2. **Threads**: Codex conversation log grouped by thread id (for container sessions where available).
3. Agent output summary is visible alongside session and tool-call context.

Cleanup states to recognize:

1. `stopped`
2. `failed`
3. `abandoned`
4. `cleanup_failed`
5. `cleaned_up`

Use these states to decide whether to wait, retry cleanup, or escalate to artifact-based review.

## 9.4 Logs And Output

Execution output is spread across a few tabs by purpose:

1. **Agent reasoning and output**
   Use the agent/session views for user-facing messages tied to the target.
2. **Execution logs**
   Use job-level execution logs for timing, command-level progress, and worker failures.
3. **Workflow results**
   Use **Workflow** runs and related run inspection for approval chains and orchestration context.
4. **Artifacts**
   Use **Artifacts** for downloaded or downloadable run outputs and bundles.

Quick reading order:

1. Confirm the target state and state change times.
2. Open the target view (VMs or Agents/Sessions).
3. Read recent execution/job logs.
4. If an approval or external action is involved, open **Approvals** and then **Tool Calls**.

Use **Section 7: Audit Trail And Tool Calls** when you need a strict action timeline.

## 9.5 Cleanup And Recovery

Cleanup and recovery are separate from normal stop/complete behavior.

Typical outcomes:

1. `stopped`: work ended and target is inactive.
2. `failed`: run did not complete cleanly.
3. `abandoned`: cleanup path did not complete automatically.
4. `cleanup_failed`: cleanup was attempted but not completed.
5. `cleaned_up`: worker-side cleanup is done while host manifest remains for review.

General operator flow:

1. Check whether the run has already produced needed output.
2. Confirm no pending approvals are blocked by this state.
3. Decide whether automatic cleanup is sufficient or manual intervention is required.
4. If cleanup state is problematic, review logs before any destructive action.
5. Retain evidence through session, tool-call, and logs before retrying.

For unresolved worker service issues, check background services in **Section 18** before touching more targets.

## How-to: Inspect A VM Before Taking Action

Use this when you need to decide whether to start, shut down, or destroy a VM.

1. Open **VMs**.
2. Select a directory and verify the active scope.
3. Select the VM row.
4. Confirm VM session link, provider, and power state.
5. Check the latest status timestamps and any linked session IDs.
6. Review recent log entry notes or summaries.
7. If needed, open the related session from the linked row.
8. Decide action:
   1. If healthy but idle, keep it stopped and monitor.
   2. If running unexpectedly, shut down first and capture evidence.
   3. If obsolete and confirmed, proceed to destroy only when it is safe.
9. Confirm lock/unlock state matches your organization's change process.
10. Refresh and re-open the row after action to verify the new state.

Expected result:

1. You have a confirmed target action aligned with the current state.
2. The VM list shows the updated lifecycle state.

## How-to: Create A Standalone VM

Use this when you need an independent VM that is not tied to a workflow trigger.

1. Open **VMs**.
2. Unlock privileged mode.
3. Click **New VM**.
4. Enter a clear VM name.
5. (Optional) set session context and model.
6. Choose required bootstraps (for example .NET, NuGet, Playwright, or MSSQL) based on expected task needs.
7. Confirm directory and provider context.
8. Start creation.
9. Open the new VM row and confirm:
   1. session attachment
   2. power state
   3. timestamps
   4. provider and identity
10. Add any agent session notes after creation if your run plan requires special handling.

If creation does not appear:

1. Check directory and privileges.
2. Refresh the VMs view.
3. Re-check required bootstrap toggles.

## How-to: Review A Container Worker Execution

Use this when you need to decide if a container-backed run completed cleanly or needs follow-up.

1. Open **Agents** or **Sessions** and select the target session.
2. Confirm backend indicates container worker execution.
3. Open the agent execution section in **Executions**.
4. Read the latest execution entry and status.
5. Open **Threads** if you need task-level conversation continuity.
6. Check cleanup state and last worker completion signal if the job appears to have ended.
7. If the session includes external interactions, open related tool-call records.
8. If artifacts were produced, open **Artifacts** and resolve by artifact key.
9. Record the run outcome before making any next state transition.

Expected result:

1. You can answer whether the run is finished, failed, or waiting for cleanup.
2. You know the next safe action, either continue, re-run, or recovery cleanup.

## How-to: Handle A Cleanup Failure

Use this when an execution shows `cleanup_failed` or similar incomplete cleanup state.

1. Open the target session or agent record.
2. Confirm the session is in a cleanup-failed or failed completion state.
3. Capture the displayed cleanup error and last worker observation.
4. Open related execution log lines around the cleanup window.
5. Check for pending approvals or locked policies that could block safe cleanup.
6. Confirm you still have required evidence:
   1. session record
   2. logs
   3. tool-call link
7. Retry cleanup only once the immediate blocker is clear.
8. If cleanup still fails, keep state visible and escalate through normal operational notes.
9. Revisit the session for manual recovery or safe teardown only after the run context is clear.

Safe completion pattern:

1. cleanup retried successfully -> target returns to a stable non-actionable state.
2. cleanup still failing -> preserve evidence, create a follow-up ticket, and avoid destructive action until recovery path is clear.
