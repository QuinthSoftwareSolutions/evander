# Control Plane Admin UI User Manual

This manual covers the Control Plane Admin UI served by `agent-sandbox-admin-web`.
It is written for operators who need to monitor the control plane, manage execution targets,
review privileged work, adjust directory-scoped policy, and inspect workflow runtime behavior.

The Admin UI is a host-side operator surface. It uses the same runtime state, configuration files,
audit logs, workflow packs, and worker status that the CLI and systemd services use. When the UI
shows an unavailable or empty state, treat that as live evidence from the current control plane
rather than placeholder sample data.

## Open The Admin UI

The default local URL is:

```text
http://127.0.0.1:8100/
```

The service entry point is:

```bash
agent-sandbox-admin-web --runtime-dir runtime --host 127.0.0.1 --port 8100
```

For persistent deployments, start the systemd unit after `config/admin-web.env` is configured:

```bash
sudo systemctl start agent-sandbox-admin-web.service
```

If the Admin UI is exposed beyond localhost, put it behind TLS and configure authentication in
`config/admin-web.env`. The UI supports:

- `none`: no sign-in gate, intended for trusted local-only operation.
- `local_password`: username/password sign-in backed by the signed local session cookie.
- `azure_sso`: Microsoft Entra OIDC sign-in backed by the same local session cookie.

## Shared Shell

![Admin dashboard and navigation](assets/admin-ui/manual-dashboard.png)

Every Admin UI section uses the same shell:

- The header shows the active section, selected directory, current user, privilege lock state,
  theme toggle, directory chooser, and sign-out button when authentication is enabled.
- The left navigation opens each major operator section. It can be collapsed to short labels with
  the section toggle.
- The selected directory is shared by directory-scoped views. It defaults to `root`.
- Privileged operations are locked by default. Unlock before creating, deleting, starting,
  stopping, approving, denying, saving policy, or editing workflow state.
- Deep links use hash routes, for example `/#workflow`, `/#sessions`, `/#tool-calls`, and
  `/#configuration`.
- A deployment banner appears when the host reports an active or stale deployment/update status.
- The assistant launcher appears in the lower corner when the assistant integration is configured.

### Choose Directory Scope

![Directory chooser](assets/admin-ui/manual-directory-chooser.png)

Use **Choose directory** in the header when you need to work in a specific tenant, product, team,
or environment scope.

1. Click **Choose directory**.
2. Expand the tree to find the desired path.
3. Select the directory.
4. If directory-scoped configuration is enabled and privileges are unlocked, use the plus button
   to create a child directory.
5. Empty non-root directories can be deleted with the delete button when privileges are unlocked.

Changing directory affects pages that filter or edit directory-owned state, including Agents,
Approvals, VMs, Issues, Sessions, Tool Calls, Configuration, Users, Workflow, and dashboard panels.

### Unlock Privileged Operations

Use **Unlock** in the header before performing mutations. Locked mode is useful for read-only
inspection because destructive or sensitive actions are disabled.

Privileged actions include:

- creating, stopping, destroying, or resuming agents and VMs
- approving or denying requests
- editing sessions and approval policy
- saving configuration and worker settings
- creating, editing, or deleting issues, users, directories, skills, and workflow graphs

Use **Lock** again when you are finished.

## Assistant

The Assistant launcher appears in the lower corner when the Admin assistant integration is
configured. Depending on backend configuration and startup state, the launcher can be hidden,
disabled, loading, minimized, expanded, or show an error.

Common tasks:

1. Click **Assistant** to expand the chat panel.
2. Check the mode badge before asking it to act. **Read only** can inspect; **Full access** can
   perform configured operations.
3. Type a message and click **Send**, or press Enter without Shift.
4. Use Shift+Enter for multiline messages.
5. Use **New chat** to start a fresh assistant thread.
6. Minimize the panel when you want it out of the way.
7. If the launcher is disabled or shows an error, check assistant configuration and
   `/api/chat/session` server logs before relying on it for operator work.

## Dashboard

The Dashboard is the default landing view after sign-in.

![Dashboard](assets/admin-ui/manual-dashboard.png)

Use it to answer "what needs attention right now?" It combines live reads from the detail sections:

- deployment and admin state
- worker service status
- agent inventory
- workflow runs and graph activity
- approval queue health
- session counts
- tool-call audit status
- version, changelog, and news context
- workflow document and catalog availability

Common tasks:

1. Click **Refresh** for a fresh read. Dashboard refreshes bypass the short-lived dashboard cache.
2. Expand panels to see the live details behind each summary.
3. Download the changelog from the Version and changelog panel when you need the full release log.
4. Use **Newer** and **Older** in the News card to page through previous blog entries.
5. Use quick links such as **Open workflow**, **Open approvals**, **Open sessions**,
   **Open tool calls**, and **Open config** to move into the detail tabs.
6. If a panel is marked missing or unavailable, open the corresponding detail tab to inspect the
   exact backend error or empty state.

## Agents

The Agents tab manages active and historical execution targets across the selected directory.

![Agents](assets/admin-ui/manual-agents.png)

Use this page to inspect sessions, target names, policies, logs, and lifecycle controls for agent
execution targets.

Common tasks:

1. Select a directory in the shell.
2. Open **Agents**.
3. Use **Refresh** to reload current target state.
4. Select an agent from the left list to inspect details, logs, labels, policy, metadata, and
   attached session or VM context.
5. Unlock privileges before starting any mutation.
6. Use **Create** to create an agent session. Fill in directory, session ID, model, TTL, target
   details, allowed tools/scripts, communication allowlists, bootstrap options, and optional Codex
   skills.
7. Use the policy edit action to adjust an existing agent's host-side policy.
8. Use the skill install action to copy managed Codex skills into an existing session.
9. Use the prompt action to queue a direct prompt for the selected agent. The dialog accepts a
   multiline prompt and an optional resume thread ID, prefilled from the session's latest known
   agent thread when available.
10. Use lifecycle actions only after confirming the target and directory are correct.

Notes:

- The provider field can be left blank to use the directory/default backend.
- Skill choices come from the managed Skills tab.
- Container-worker logs default to **Executions**, which shows raw worker job logs by execution.
  Use **Threads** when you need the Codex conversation grouped by thread ID. The thread view is
  available only for container-worker sessions; VM-backed agents continue to show their normal
  agent output.
- Some actions are unavailable until privileged operations are unlocked.

## Approvals

The Approvals tab reviews queued human-approval requests.

![Approvals](assets/admin-ui/manual-approvals.png)

Use it when a session or workflow attempts a tool/action that requires human approval.

Common tasks:

1. Open **Approvals** and confirm the directory scope.
2. Filter by status, session, VM, tool, minimum age, or direct-directory-only scope.
3. Select a request from the queue.
4. Review queue health, request summary, intended effect, digest, redacted payload, decision
   history, and matching audit context.
5. Unlock privileges.
6. Enter the approver identity and a reason.
7. Click **Approve** or **Deny**.
8. Re-open Tool Calls if you need to compare the request to the original audited tool invocation.

Approval requests are intentionally shown with redacted payloads. Do not assume redacted JSON is
the full original request body.

## Artifacts

The Artifacts tab resolves and downloads published agent artifact archives.

![Artifacts](assets/admin-ui/manual-artifacts.png)

Use it when a workflow or agent has published `/artifacts` as an immutable archive, or when an
operator needs to upload a single local file into the same artifact store for a later workflow.

Common tasks:

1. Open **Artifacts**.
2. Filter by artifact key, published time range, or result limit.
3. Click **Apply filters** to list matching archives.
4. If you already know the key, enter it and click **Resolve key**.
5. Select an archive row to inspect session ID, backend, byte counts, source file counts,
   source artifacts root, SHA-256, and metadata.
6. Use the download button to download the selected archive.
7. To upload a local file, click the upload button, enter a new artifact key, select the file,
   and click **Upload**. The published archive uses the selected file's name.

Artifact keys are durable identifiers. Prefer resolving by key when a workflow, tool call, or run
detail links to an artifact directly.

## Skills

The Skills tab manages the host-side Codex skill library exposed to agent creation and install
flows.

![Skills](assets/admin-ui/manual-skills.png)

Common tasks:

1. Open **Skills**.
2. Select a skill from the left list to preview and edit its Markdown content.
3. Upload a new `.md` skill by entering a name, selecting a file, or pasting Markdown content.
4. Check **Replace existing** when intentionally overwriting an existing skill.
5. Click **Save** to upload.
6. Use **Replace** to update the selected skill content.
7. Use **Delete** to remove a selected skill.
8. Install managed skills into sessions from the Agents tab.

Skill names should be stable and descriptive because they are selected during agent creation and
skill installation.

## VMs

The VMs tab is visible when the selected execution backend is `libvirt_vm`.

![VMs](assets/admin-ui/manual-vms.png)

Use it to inspect and operate host-managed libvirt VMs.

Common tasks:

1. Open **VMs**.
2. Select a VM from the left list.
3. Inspect machine status: sessions, directories, provider, IP address, power state, timestamps,
   labels, and recent actions.
4. Unlock privileges before changing VM state.
5. Use start, shutdown, and destroy actions from the toolbar.
6. Use **New VM** to create a standalone VM. Provide a VM name, optional session ID, model, and
   bootstrap options such as .NET, NuGet, Playwright, and MSSQL.

Destroying a VM removes associated host-side state. Confirm the VM name and directory before using
the destructive action.

## Issues

The Issues tab manages local issue records and issue-VM startup.

![Issues](assets/admin-ui/manual-issues.png)

Common tasks:

1. Open **Issues**.
2. Filter by state: **Active**, **All**, or a specific issue state.
3. Select an issue to inspect title, source, updated time, repo source, directory, YAML path, body,
   and metadata.
4. Unlock privileges to create, edit, delete, or launch work from issues.
5. Click **New issue** to create a local issue from the form or import an existing YAML file.
6. Fill in title, repo source, body, and optional immediate VM startup settings.
7. Save the issue.
8. For an existing issue, use the VM action to create a VM with the requested bootstrap options.
9. Use edit and delete only for issue records that the UI marks as editable/deletable.

Issue VM creation uses the selected issue and the current directory context unless the form
provides a more specific value.

## Sessions

The Sessions tab manages agent session records, tool access, approval policy, and scheduled wakes.

![Sessions](assets/admin-ui/manual-sessions.png)

Common tasks:

1. Open **Sessions**.
2. Search by session, VM, issue, or model.
3. Switch between active and expired session lists.
4. Select a session to inspect VM, issue, directory, model, creation time, expiry time, tools, and
   wake schedules.
5. Unlock privileges before editing.
6. Use the extend action to renew expiry.
7. Use revoke to deny further guest access before expiry.
8. Use delete only when you intend to remove saved local session state.
9. Edit the model field when a session needs a different model for future resume/prompt work.
10. Toggle allowed tools and **Require approval** for tools that should queue human decisions.
11. Edit allowed scripts and communication allowlists.
12. Configure communication-file receive/send permissions, max bytes, MIME types, extensions, and
    metadata.
13. Save settings.
14. Create, update, enable/disable, or delete wake schedules for recurring cron or one-time resume
    prompts.

Changing allowed tools can also remove approval requirements for tools that are no longer allowed.
Review both tool access and approval policy before saving.

## Tool Calls

The Tool Calls tab is the audit view for MCP/tool invocations.

![Tool Calls](assets/admin-ui/manual-tool-calls.png)

Use it to investigate what happened, who or what triggered it, and how it relates to sessions,
approvals, workflows, agents, and VMs.

Common tasks:

1. Open **Tool Calls**.
2. Filter by free-text search, session, tool, status, direct-directory-only scope, created-after,
   created-before, and limit.
3. Select a row from the audit table.
4. Inspect timestamp, status, duration, session, actor, directory, tool, request ID, correlation ID,
   approval request ID, workflow run, agent/VM fields, redacted arguments, redacted result, and
   normalized metadata.
5. Use relation buttons to jump to related Sessions, Approvals, Workflow runs, Agents, VMs,
   Reports, Configuration, Users, or Workers sections when the audit record carries those links.

The audit view is read-only. Use it as the evidence trail for operator decisions and incident
review.

## Configuration

The Configuration tab edits directory-scoped policy and runtime-global admin settings.

![Configuration](assets/admin-ui/manual-configuration.png)

Common tasks:

1. Choose the directory in the shell.
2. Open **Configuration**.
3. Select a configuration domain from the left list.
4. Review the effective values, explicit overrides, parent defaults, validation messages, and raw
   override JSON.
5. Unlock privileges before saving.
6. Edit only the fields for the selected domain.
7. Use field tooltips to confirm each setting's meaning and source domain.
8. Click the save icon in the toolbar.
9. If the save fails because the config changed on the server, reload and reapply the intended
   changes.

Specialized configuration areas:

- **Teams delegated auth**: inspect Teams auth status for the selected directory, start device-code
  sign-in, refresh status, or sign out.
- **Contacts**: maintain named contact entries with descriptions, email addresses, and phone
  numbers for directory-scoped use.
- **Communication targets**: declare inherited and explicit messaging targets used by workflow
  notification/message effects and by the provider-neutral communication message trigger when the
  target has `read` capability.
- **Approval notification targets**: choose notify-capable communication targets for approval
  notifications.
- **Directory override JSON**: copy the unified explicit overrides for review, diffing, or support.
- **Effective config** and **Parent defaults**: compare the live inherited result to the parent
  baseline before saving a local override.

Avoid editing live pack or config files from stale local copies while other operators may be using
Admin Web. The UI save path uses revision checks to prevent silent stale overwrites.

## Reports

The Reports tab contains workflow activity and conformance snapshots.

![Reports](assets/admin-ui/manual-reports.png)

Workflow graph activity:

1. Open **Reports**.
2. Select **Workflow graph activity**.
3. Use 24h, 7d, or 30d presets, or enter a custom start/end range.
4. Click **Run report**.
5. Select a graph to inspect activity count, run count, node count, latest activity, status counts,
   user IDs, and active node breakdown.

Conformance:

1. Select **Conformance**.
2. Choose the trigger or effect report.
3. Review state, generated time, status, requirement counts, missing coverage, traceability task
   count, failing requirements, remediation snippets, and guidance.
4. Click **Run report** to regenerate the selected conformance snapshot from the UI.

The conformance report artifacts are also documented in `conformance-reports.md`.

## Users

The Users tab manages Admin Web user records when Azure SSO is enabled.

![Users](assets/admin-ui/manual-users.png)

Common tasks:

1. Sign in as a user with the `admin` role.
2. Open **Users**.
3. Click **New user**.
4. Enter the user's login/email-style identifier.
5. Optionally set a display name.
6. Choose `admin` or `regular`.
7. Optionally attach a directory-scoped contact.
8. Click **Save**.
9. Select an existing user to edit display name, role, or contact.
10. Use **Delete** to remove a user record and invalidate their local Admin Web access on the next
    authenticated request.

Only admin users can manage users. Regular users can sign in and use non-admin surfaces according
to the configured auth/session behavior, but they cannot edit the user registry.

## Workers

The Workers tab manages host background worker services and worker-global settings.

![Workers](assets/admin-ui/manual-workers.png)

Common tasks:

1. Open **Workers**.
2. Select a worker service from the left list.
3. Inspect active state, sub-state, result, unit name, main PID, global settings, and recent
   journal tail.
4. Unlock privileges before starting, stopping, or saving worker settings.
5. Use the start/stop toolbar action to change service state.
6. Edit fields in **Global settings** when the worker exposes an editable schema.
7. Click the save icon to write worker-global settings back to the host config file.
8. Use **Reload log** to refresh the current journal tail.

If a worker configuration save reports a revision conflict, reload the worker settings first. The
draft remains visible so you can reapply intentional changes after reviewing the newer server copy.

## Workflow

The Workflow tab is the graph editor and runtime inspector.

![Workflow](assets/admin-ui/manual-workflow.png)

Use it to maintain workflow packs, inspect current and historical runs, and diagnose node-level
runtime behavior.

Common editing tasks:

1. Choose the directory in the shell.
2. Open **Workflow**.
3. Unlock privileges before editing.
4. Use the **Graphs** panel to select a graph and edit graph name, description, directory paths, and
   user IDs.
5. Use the **Create** panel to add trigger, gate, query, or effect nodes. Click a node type or drag
   it onto the canvas.
6. Connect nodes on the canvas. Named output ports are offered where a source node has multiple
   routes.
7. Select a node to open the Inspector.
8. Review the selected node's **Outputs** section to see declared or derived binding paths, schema
   types, and copyable binding expressions for downstream fields.
9. Edit node ID, display name, and type-specific fields. Trigger nodes also expose an enabled
   toggle; disabled triggers are ghosted on the graph and do not poll for new events. Fields can
   support literal values, bindings, templates, directory selectors, structured lists/maps, JSON,
   and catalog-backed options. Agent/VM creation tool argument policies use a structured
   rule-and-constraint editor for common policies, with raw JSON still available for advanced
   policy maps.
10. Review diagnostics on dirty or invalid nodes.
11. Use **Reset changes** for a selected dirty node when you want to return it to the saved
    version.
12. Use **Save workflow** to persist the workflow document after all diagnostics are acceptable.

The experimental **Generate workflow** control opens a separate blank draft canvas. If the current
editor has unsaved changes, opening the generator asks for confirmation and discards those edits.
Enter a natural-language description, then choose **Generate draft** to request a preview from the
host-side OpenAI Responses API integration; the draft is not saved and cannot run. **Cancel**
returns to the regular saved workflow view. **Accept draft** imports the generated graph into the
normal editor as unsaved changes, where graph directory metadata and node fields can be reviewed
before **Save workflow**.

Common runtime inspection tasks:

1. Use **Tutorials** to open the walkthrough chooser. **First steps** teaches the workflow canvas
   basics in an isolated local tutorial graph; **Skip** marks the tutorial complete for the chooser
   attention state without launching it, and **Restart** can run it again later.
2. Use **Current runs** to inspect active workflow events and node activity.
3. Enter an event ID, trace ID, session ID, or artifact ID when you need an exact run.
4. Use **Inspect** to enter inspect mode for the selected run.
5. In inspect mode, select nodes on the run graph to see node status, duration, started/finished
   times, errors, binding errors, links, resolved inputs, outputs, input payload, inputs by source,
   binding trace, and raw records.
6. Use **Latest runs** search to find recent runs by graph or node scope, including runs without
   session links and completed runs when those toggles are enabled.
7. Use resume, clear/delete, or failure-dismiss controls only when you understand the runtime
   effect and have privileges unlocked.
8. Exit inspect mode to return to graph editing.

Workflow edits are staged in the browser until **Save workflow** succeeds. Leaving the workflow
view or changing directory while there are unsaved workflow changes prompts for confirmation.

## End-To-End Common Tasks

### Investigate a Failed Automation

1. Open **Dashboard** and expand **Automation activity** and **Tool-call audit trail**.
2. Click **Open workflow** if the dashboard points to a run or graph problem.
3. Inspect the current or latest run in **Workflow**.
4. Select failing nodes and review errors, binding trace, input payload, and outputs.
5. Open **Tool Calls** and filter by session, tool, request ID, correlation ID, or workflow run ID.
6. Follow relation buttons into Approvals, Sessions, Agents, or VMs as needed.
7. Use **Reports** to check whether the issue is isolated to one graph or visible across the
   selected period.

### Create Work From A Local Issue

1. Choose the target directory.
2. Unlock privileges.
3. Open **Issues**.
4. Click **New issue**.
5. Create the issue manually or import YAML.
6. Fill in repo source and issue body.
7. Enable immediate VM startup if desired and select bootstrap options.
8. Save the issue.
9. If you did not start a VM during creation, select the issue and use the VM action later.
10. Watch **VMs**, **Agents**, **Sessions**, and **Tool Calls** for follow-up state.

### Require Approval For A Tool

1. Choose the directory.
2. Unlock privileges.
3. Open **Sessions**.
4. Select the session.
5. In **Model and access**, enable the target tool if it is not already allowed.
6. Check **Require approval** for the same tool.
7. Save settings.
8. When the session uses that tool, review the queued request in **Approvals**.

### Review And Decide An Approval

1. Open **Approvals**.
2. Filter to pending requests and the relevant session/tool.
3. Select the request.
4. Compare summary, intended effect, payload digest, redacted payload, and audit context.
5. Unlock privileges.
6. Enter approver and reason.
7. Approve or deny.
8. Confirm the final decision is visible in Decision history and in **Tool Calls**.

### Update Directory Configuration Safely

1. Choose the directory.
2. Open **Configuration**.
3. Select the domain.
4. Review effective config, parent defaults, and existing override JSON.
5. Unlock privileges.
6. Change only the fields needed.
7. Save the tab.
8. If the UI reports that configuration changed before save, reload and compare the newer state
   before retrying.

### Start Or Stop A Worker

1. Unlock privileges.
2. Open **Workers**.
3. Select the worker.
4. Review overview and recent log.
5. Use start or stop.
6. Refresh and verify active state, sub-state, result, and journal output.

### Add A Managed Skill And Use It In A Session

1. Open **Skills**.
2. Upload or paste the Markdown skill and save it.
3. Open **Agents**.
4. Select an existing agent session or create a new one.
5. Choose the skill in the create form or install-skills modal.
6. Confirm the action log and session state.

### Download Published Artifacts

1. Open **Artifacts**.
2. Resolve the known artifact key or filter the archive list by time range.
3. Select the archive.
4. Confirm session, backend, file count, byte count, and SHA-256.
5. Download the archive.

## Troubleshooting

- If a page appears empty, confirm the selected directory. Many lists are scoped.
- If mutation buttons are disabled, unlock privileged operations.
- If VMs are absent from navigation, the current execution backend is not `libvirt_vm`.
- If a save reports stale configuration, reload before retrying.
- If workflow edits are not visible to another operator, confirm **Save workflow** succeeded and
  that both operators are viewing the same directory.
- If the dashboard feels stale, use **Refresh**. Detail tabs always use their normal live reads.
- If screenshots or labels differ from this manual, prefer the running UI and current codebase as
  authoritative; this manual should be updated with the UI change.
