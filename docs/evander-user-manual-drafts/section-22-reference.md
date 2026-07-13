# 22. Reference

This section is the quick lookup surface for common operator terms, page locations, status
signals, and safe checklists when you are already in day-to-day work.

Use it when you want to move fast without re-reading the full manual.

## 22.1 Glossary

| Term | What it means for operators | Related section |
|---|---|---|
| Directory | Your current operational scope for many filtered lists and edits. Host-global Add-ons and Workers, plus the published Artifacts list, are important exceptions. | Section 2, Section 16 |
| Session | The active contract that lets an agent continue work in a target. | Section 5 |
| Agent | The running control-surface object for a workload (for example target status, tools, logs, wake state). | Section 4 |
| Worker | Background host service that runs orchestration, triggers, wakes, or integrations. | Section 18 |
| Workflow | An automation graph: trigger, gate, query, effect; plus run history and status. | Section 11 |
| Tool Call | A logged record of what was requested, allowed, denied, or errored. | Section 7 |
| Approval | A human safety gate for sensitive actions. | Section 6 |
| Wake | A scheduled or on-demand resume action that prompts agent continuation. | Section 5 |
| Add-on | Optional feature surface that adds pages and domain behavior (for example Team, Bokio, Skatteverket). | Section 13 and `docs/addons.md` |
| Review request | Team quality checkpoint before release movement. | Section 13 |
| Deliverable | Work output package prepared for review and possible release. | Section 13 |
| Release package | A controlled publish step that moves reviewed work toward canonical output. | Section 13 |
| Privilege lock | A safety mode in the header that blocks most changes until unlocked. | Section 2 |

## 22.2 Page Map

| Page | Purpose | Common tasks |
|---|---|---|
| Dashboard | Live health overview and first troubleshooting triage surface. | Shift start check, warning follow-up, quick context scan. |
| Agents | Target inventory, policy context, session links, and basic target control. | Find active agent, check recent logs, pause/resume steering. |
| Sessions | Session scope, expiry, allowed tools, file and wake settings. | Extend/revoke sessions, review allowed tools, inspect wake schedules. |
| VMs | VM lifecycle control for libvirt-based targets. | Inspect power and session linkage, stop/shutdown/destroy after checks. |
| Issues | Local intake queue and issue-to-workflow startup bridge. | Create/edit issue, start issue-based VM, review source/owner. |
| Approvals | Human decisions waiting for or already made. | Review requests, approve/deny with reason, check queue age. |
| Tool Calls | Audit trail for actions and decision outcomes. | Trace who/what ran, see allowed vs denied vs error paths. |
| Workflow | Graph editor and run inspection. | Edit graph nodes/edges, inspect failed runs, verify effect behavior. |
| Reports | Trend and summary reporting for automation health. | Check failure clusters, PR preflight, conformance snapshots. |
| Configuration | Directory settings and effective policy. | Edit execution/model/provider/user-facing settings safely. |
| Artifacts | Host-visible published archives plus artifact-space references governed by subject/grants. | Resolve artifact keys, verify provenance, download published outputs. |
| Add-ons (`/#addons`) | Host-global discovery, enablement, capability, runtime-pack, error, and restart state. | Enable/disable packages, refresh registered runtime packs, inspect headless add-ons. |
| Users & Groups | Global operator records plus directory assignments and workflow grants. | Add/delete users, create groups, review permissions. |
| MCP Tools | Directory tool deny/allow and default approval behavior. | Configure MCP tool limits and approval defaults. |
| Tool Visibility | Effective per-directory visibility for built-in, MCP, and add-on tools. | Confirm which tools agents can discover before changing policy. |
For optional add-on mode, this map also includes:

| Add-on Page | Purpose | Common tasks |
|---|---|---|
| Teams (`/addons/team-addon/`) | Team work across Workspace, Operations, Delivery, Memory, Projects, Task board, Cases, Teams, Chat, Mail, Governance, Planning, Timeline, and Org chart. | Assign work, review Agent Mail and memory, handle governance, review and publish delivery. |
| Bokio / Skatteverket pages | Dedicated accounting or bookkeeping workflows when enabled. | Run domain actions that are available only in the add-on scope. |
| MSSQL Seeds (`/addons/mssql-seed-addon/`) | Approved database seed aliases and worker-side restore-sidecar configuration. | Configure sidecars and seeds; provision/reset approved session databases. |
| Azure DevOps Activity | Headless MCP/workflow capability with no navigation tab. | Verify loaded capabilities on Add-ons; query durable ADO activity through its tools/workflows. |

## 22.3 Permission Summary

Permission is easiest to think of in layers.

- **Read mode (default safe mode):** view-only for almost all pages. Good for investigation.
- **Unlocked mode:** allows create, edit, and lifecycle actions on the current surface if your user has permission.
- **Workflow write access:** required for graph edits, and includes read access.
- **Approval/decision rights:** required for queue action and direct review decisions.
- **Team read-all chat access:** `team_chats.read_all` permits read-only review of other operators' Chat histories only; it does not expand Agent Mail access.

Common operator permissions:

| Permission area | Gives you | Safe default use |
|---|---|---|
| `users.manage` | User and grant edits in Users & Groups. | Needed when onboarding/offboarding access. |
| `groups.manage` | Group creation and group membership management. | Use for broad role templates instead of many direct grants. |
| Workflow grants (read/write) | Access to workflow views and workflow editing. | Write grants should be limited to required graph ownership. |
| Workflow/section privileges + unlock | Mutations in Sessions, Agents, Workers, Configuration, VMs, and reviews. | Keep unlocked only while taking action. |

Add-on enablement is host-global and should be limited to designated platform administrators. Data
inside an add-on can still apply directory, subject, grant, role, and add-on-specific permission
rules.

## 22.4 Status Meanings

Use this to avoid changing state on a false signal.

### Sessions

- **Active**: dispatch and wakes can continue.
- **Expired**: time limit reached; normal dispatch is blocked.
- **Revoked**: manually stopped for future access.
- **Deleted**: session record is removed from local history.

### Workers

- **running**: service is active.
- **starting/stopping**: short transition states; check next refresh before action.
- **failed**: service reported an error and may need host/service check.
- **stopped**: intentional stop or not started.
- **missing page/state**: page may not include this worker for your deployment.

### Approvals

- **Pending**: waiting for human decision.
- **Approved**: queued request is cleared for the approved action path.
- **Denied**: action will not continue on that request.
- **Expired**: request timeout or invalid/too-old approval window.
- **Failed**: queue/action path could not complete cleanly.

### Workflow runs

- **running**: execution is in progress.
- **completed**: run reached its end without current hard fault.
- **failed**: at least one node could not complete.
- **error**: run or node ended in an execution fault.
- **dismissed/cleared**: record removed after manual cleanup; evidence for this run remains elsewhere.

### Team reviews

- **pending**: waiting for the assigned reviewer.
- **in_progress**: work is actively being reviewed.
- **accepted**: review approved; can move toward deliverable readiness.
- **rejected**: needs correction before forward progress.
- **blocked**: dependent states prevent continuation.

### Releases

- **requested**: release path started.
- **in_progress**: release branch/publish sequence is running.
- **published**: release package reached expected final branch/output.
- **failed**: one or more release checks did not pass.
- **blocked**: missing task/review prereqs or policy gate.

### Cleanup

- **stopped**: execution ended and cleanup is stable.
- **failed**: run ended with incomplete execution.
- **abandoned**: cleanup process did not finish automatically.
- **cleanup_failed**: explicit cleanup attempt did not complete.
- **cleaned_up**: worker-side cleanup done and host manifest is stable for review.

## 22.5 Default URLs And Deep Links

Base entry for local Admin UI:

- Admin Web base: `http://127.0.0.1:8100/`

Primary direct routes:

| Area | Deep link |
|---|---|
| Dashboard | `/#` |
| Agents | `/#agents` |
| Sessions | `/#sessions` |
| VMs | `/#vms` |
| Issues | `/#issues` |
| Approvals | `/#approvals` |
| Tool Calls | `/#tool-calls` |
| Workflow | `/#workflow` |
| Reports | `/#reports` |
| Configuration | `/#configuration` |
| Artifacts | `/#artifacts` |
| Add-ons | `/#addons` |
| Skills | `/#skills` |
| MCP Tools | `/#mcp-tools` |
| Tool Visibility | `/#tool-visibility` |
| Users | `/#users` |
| User Groups | `/#users/groups` |
| Workers | `/#workers` |

Optional add-on routes (enabled only where add-on modules are loaded):

- Teams: `/addons/team-addon/`
- Bokio: `/addons/bokio-addon/`
- Skatteverket: `/addons/skatteverket-addon/`
- MSSQL Seeds: `/addons/mssql-seed-addon/`

Azure DevOps Activity is headless and therefore has no optional UI route; inspect it at `/#addons`.

## 22.6 Operator Checklists

### Daily Monitoring Checklist

1. Open Dashboard and refresh.
2. Verify worker set health and major warning cards.
3. Open Approval queue and check pending age.
4. Open current workflow runs for at least one active graph.
5. Open Tool Calls if any warning appears in sessions, workflows, or approvals.
6. Open Version/Changelog in Dashboard and check release alignment.
7. Save two short notes: "green", "watch", or "investigate".

### Pre-Change Checklist

1. Confirm directory scope.
2. Confirm your lock state is unlocked only if you are ready to save.
3. Confirm the target has the minimum permission for this action.
4. Check dependent records first:
   - session or agent status,
   - approvals or wake impact,
   - worker health,
   - related tool/graph dependencies.
5. Read the current status on related pages from the quick map:
   - Dashboard for overall impact,
   - Tool Calls for recent side effects,
   - Reports if similar failures are recurring.
6. Prepare rollback plan:
   - what to undo,
   - what evidence to keep,
   - who to notify.
7. Make only the smallest change needed.
8. Save, then immediately confirm changed state through linked records.

### Approval Review Checklist

1. Open Approvals with narrow filters for session or tool.
2. Verify status and scope timestamps.
3. Read request summary and directory context.
4. Confirm decision history and related audit link.
5. Apply decision with short reason.
6. Confirm request status changed and Tool Call outcome matches intent.

### Incident Response Checklist

1. Capture exact page, directory, and time.
2. Record top-level symptom status and first affected record IDs.
3. Jump from summary page to the linked detail record.
4. Confirm no conflicting records across Dashboard, Workers/Workflow, and Tool Calls.
5. Hold additional mutations until the chain is clear.

### Release Review Checklist

1. Confirm deliverable is complete and artifact set is present.
2. Confirm all required review requests are accepted.
3. Confirm no blocked release prerequisites remain.
4. Confirm project/release roles are valid for the request.
5. Request release and watch package publication state until final.
6. If blocked, stop and document why before retry.

### Operator Handover Checklist

1. Directory and lock state.
2. Active sessions and privileged actions in progress.
3. Open approvals and pending queue.
4. Current worker or workflow warnings.
5. Open incidents and incident notes.
6. Last meaningful artifact or run output for next operator.

## 22.7 Further Reading

For deeper setup and safety details, use:

- `docs/operations.md` for host setup, bootstrap flow, and service installation.
- `docs/admin-ui-user-manual.md` for detailed page behavior.
- `docs/configuration-reference.md` for parameter and domain behavior.
- `docs/addons.md` for Team, Bokio, Skatteverket, MSSQL Seeds, and headless Azure DevOps Activity enablement.
- `docs/live-smoke-tests.md` for post-change safety checks.
- `docs/container-worker-backend.md` when container worker execution is in scope.
- `docs/execution-backends.md` for backend selection and target behavior.
- `docs/libvirt-vm-template-operations.md` for VM template and boot-time dependencies.

## How-to: Look Up A Status Quickly

Use this when you only have a status term and need fast interpretation.

1. Copy the exact status text from the page.
2. Open the source page from the glossary area in this section:
   - session status -> Sessions,
   - worker status -> Workers,
   - approval status -> Approvals,
   - run status -> Workflow,
   - review/release status -> Teams page.
3. Confirm the record scope (directory and date/time).
4. Open the record detail and read one linked path (for example session, tool call, or workflow run).
5. Do not act on a stale or filtered status without checking the linked detail row.

Decision rule:

1. If status is healthy but action needed, continue.
2. If status is ambiguous, gather one linked ID and re-check linked records.
3. If status remains suspicious, route to the incident checklist.

## How-to: Use The Page Map To Find A Task

1. Name the task outcome in one sentence, for example:
   - "I need to review queued approvals."
2. Use the page map in 22.2 to pick the right starting page.
3. Open that page first and apply the smallest needed filter:
   - directory, session id, actor, or time window.
4. If missing records appear, open the linked detail page from the card.
5. If still unclear, jump to Dashboard then back to the chosen page.

Expected result:

1. You are on the right page for the intended action.
2. You have at least one supporting ID (session, request, run, or artifact key).

## How-to: Run A Pre-Change Checklist

Use this before changing workflow settings, approvals, sessions, workers, or add-on state.

1. Confirm scope and lock state:
   - correct directory,
   - unlock if change is required.
2. Confirm affected record status:
   - session state, approval queue, and worker health.
3. Confirm no dependent action is running:
   - open linked session/agent/tool-call/approvals as needed.
4. Reduce changes to one area only.
5. Prepare rollback note:
   - what field changed,
   - previous value,
   - owner to notify if rollback is needed.
6. Save once and re-open the changed record immediately.
7. Confirm expected delta:
   - status changed,
   - no unintended related record drift.
8. If behavior is different than expected:
   - stop further edits,
   - save the state snapshot,
   - continue with incident response.
