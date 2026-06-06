# Operator Field Help Inventory

This document is a working inventory of operator-facing fields and controls that need clearer help, validation copy, or stronger interaction patterns. It is intentionally descriptive rather than prescriptive about implementation details in code.

## Purpose

The goal of this inventory is to help future changes focus on the places where operators are most likely to make mistakes, hesitate, or misread the interface. Each row records what the current UI does today, why that can be confusing, and what kind of help treatment is likely to improve the experience.

## Scope

The inventory covers two areas only:

1. Workflow editor surfaces, including graph metadata, current-run controls, inspector fields, and workflow-catalog fields that affect workflow authoring or recovery.
2. Admin Web operator surfaces, including approvals, sessions, tool calls, configuration settings, and related execution-safety controls.

This document does not implement any UI changes. It is a planning artifact for future work.

## Priority Legend

- `operator-critical`: The field or control can block, misroute, or safely recover operator work. Confusion here can cause incorrect execution, missed approvals, or failed recovery steps.
- `lower-priority`: The field or control still benefits from better help, but confusion is less likely to cause immediate operator harm or task failure.

## Help-Treatment Legend

- `tooltip`: Short hover or tap help attached to the label or control.
- `inline help`: Visible helper text placed near the control.
- `validation copy`: Error or warning text that explains the rule the user must satisfy.
- `structured editor candidate`: A stronger UI pattern that replaces free-form input with a dedicated editor or selector.
- `empty-state copy`: Guidance shown when no data is available or no selection has been made.
- `documentation link`: A link to longer-form guidance or a reference page.

## Source Files Reviewed

The evidence in this document should come from the current repository state. When a row is added, cite the smallest useful source area rather than a broad file blob.

- `frontend/workflow-editor/src/App.tsx`
- `frontend/workflow-editor/src/features/approvals/ApprovalsScreen.tsx`
- `frontend/workflow-editor/src/features/admin/Tooltip.tsx`
- `frontend/workflow-editor/src/features/sessions/SessionsScreen.tsx`
- `frontend/workflow-editor/src/features/toolCalls/ToolCallsPage.tsx`
- `frontend/workflow-editor/src/features/configuration/ConfigurationScreen.tsx`
- `src/agent_sandbox/admin/service.py`
- `src/agent_sandbox/admin/config_tooltips.py`
- `src/agent_sandbox/admin/web/workers_config.py`
- `tests/test_admin_web.py`
- `tests/test_git_remote_docs.py`
- `tests/test_live_smoke_docs.py`
- `tests/test_workflow_run_inspector_demo_docs.py`
- `tests/test_root_config_docs.py`

## Inventory

Use one row per field, control, or tightly related group of controls that needs help work.

| Screen / component | Field / control name | Current behavior | User confusion or risk | Recommended help treatment | Priority | Source evidence |
|---|---|---|---|---|---|---|
| Workflow editor > Current runs | Run lookup key and lookup id | The Current runs panel exposes a lookup-key select with `Auto`, `Run ID`, `Event ID`, `Trigger event`, `Trace ID`, `Session ID`, and `Artifact ID`, followed by a plain text input with placeholder `Lookup id`. | Operators have to guess which identifier the runtime expects and can paste the wrong id type when several ids are available in logs or traces. | `inline help` + `validation copy` | `operator-critical` | `frontend/workflow-editor/src/App.tsx:4856-4868` |
| Workflow editor > Current runs | Resume from failure / Dismiss run / Clear run | Errored resumable runs show `Resume from failure`; every visible run has a `Dismiss run` or `Clear run` button, and the handler uses `window.confirm(...)` before deleting the run record. | The UI does not explain whether resume continues from the failed node or restarts, and the clear/dismiss action sounds destructive without clarifying what history remains. | `tooltip` + `validation copy` | `operator-critical` | `frontend/workflow-editor/src/App.tsx:2673-2708`; `frontend/workflow-editor/src/App.tsx:4981-5004` |
| Workflow editor > Inspector | Value mode and binding reference | Bindable fields can switch between `Literal`, `Bind`, and `Template`; the binding editor shows a `Suggested upstream output` select and a `Binding reference` input with placeholder `${{node.output.path}}`. | New users can confuse a direct binding token with a template fragment or a literal string and end up with malformed outputs. | `inline help` | `operator-critical` | `frontend/workflow-editor/src/App.tsx:2185-2215`; `frontend/workflow-editor/src/App.tsx:3941-3946` |
| Workflow editor > Workflow catalog | `effect:agent:prompt` target, session_id, vm_name, prompt, resume_thread_id | The catalog defines `target`, `session_id`, `vm_name`, `prompt`, `model`, `vm_user`, and `resume_thread_id`; the editor collapses the target choice into a separate selector with `Generic target`, `Session ID`, and `VM name`. | Dispatch target semantics and resume-thread behavior are implicit, so operators can route prompts to the wrong session or assume `resume_thread_id` creates a new thread. | `tooltip` + `structured editor candidate` | `operator-critical` | `src/agent_sandbox/admin/service.py:2224-2236`; `frontend/workflow-editor/src/App.tsx:3848-3888`; `src/agent_sandbox/admin/service.py:9150-9185` |
| Workflow editor > Workflow catalog | `effect:session:renew-if-expired` target, session_id, vm_name, extend_by, renew_if_expires_within | The catalog exposes one session selector and two free-form duration fields, and the editor seeds defaults for the renewal node with `session-id`, `1d`, and `2h`. | Operators can confuse the renewal window with the extension duration or assume the node renews every active session automatically. | `tooltip` + `validation copy` | `operator-critical` | `src/agent_sandbox/admin/service.py:1751-1753`; `src/agent_sandbox/admin/service.py:3810-3835`; `frontend/workflow-editor/src/App.tsx:1864-1872` |
| Workflow editor > Workflow catalog | `effect:agent:exec` / `effect:vm:exec` environment, stdin, timeout_seconds, check | Both catalog entries expose `environment` as JSON, `stdin` as multiline text, `timeout_seconds` as a number, and `check` as a boolean, but the fields have no descriptions. | Operators may not know the timeout unit, the JSON object shape expected for environment variables, or how `check` changes failure behavior. | `tooltip` + `validation copy` | `operator-critical` | `src/agent_sandbox/admin/service.py:2277-2291`; `src/agent_sandbox/admin/service.py:2387-2401` |
| Workflow editor > Workflow catalog | `effect:vm:create` bootstrap and scope fields | `effect:vm:create` defines `vm_name`, `session_id`, `labels`, `model`, `vm_user`, `ttl_hours`, `allowed_tools`, `allowed_script_names`, `discord_channels`, `email_mailboxes`, `email_folders`, `dotnet`, `nuget`, `mssql`, `mssql_init_root`, `playwright`, `codex_skills`, and `directory_path`, while nearby `effect:agent:create` carries much richer descriptions for similar lifetime and bootstrap controls. | Operators can change the created VM's lifetime or bootstrap footprint without understanding how it differs from creating an agent session, especially when the same fields are mostly bare inputs. | `tooltip` + `documentation link` | `lower-priority` | `src/agent_sandbox/admin/service.py:1762-1794`; `src/agent_sandbox/admin/service.py:1860-2056` |
| Workflow editor > Workflow catalog | `effect:agent:create` vm_image | The catalog exposes `vm_image` as a bindable string with libvirt-only VM template wording; it resolves approved aliases from `vm.json` instead of Docker image refs, so it can be confused with `container_image`. | Operators can assume the field swaps a container image or that any free-form image ref is allowed, leading to invalid selections or backend confusion. | `tooltip` + `validation copy` | `operator-critical` | `src/agent_sandbox/admin/service.py:2896-2977` |
| Workflow editor > Workflow catalog | `effect:communication:send-message` communication_target_id, message, thread_id | `effect:communication:send-message` exposes `communication_target_id`, `message`, and `thread_id`; the target options are supplied from directory config at catalog-read time, but the field itself has no description. | Target provenance is opaque and `thread_id` can be mistaken for a destination id, so operators may send to the wrong channel or thread. | `tooltip` + `structured editor candidate` | `operator-critical` | `src/agent_sandbox/admin/service.py:2521-2526`; `src/agent_sandbox/admin/service.py:9150-9185` |
| Workflow editor > Graph metadata | Directory and User IDs | Graph metadata uses one canonical `Directory` field plus `User IDs`; the backend normalizes `directory_path`, accepts a legacy one-item `directory_paths` value, defaults an empty directory to `root`, and rejects multiple directories. | Operators may treat these as free-form notes, but directory controls workflow graph scoping and RBAC filtering, while `user_ids` are ownership/audit metadata only. | `inline help` | `lower-priority` | `frontend/workflow-editor/src/App.tsx`; `src/agent_sandbox/admin/service.py`; `src/agent_sandbox/workflow/packs.py` |
| Admin Web > Approvals | Direct directory only / Session / VM / Tool / Min age minutes | The approvals sidebar exposes a `Direct directory only` checkbox plus plain text filters for `Session`, `VM`, `Tool`, and `Min age minutes`; the list header only hints at `Direct matches only` versus `Including descendants`. | Operators can hide descendant approvals by accident, paste the wrong identifier type, or misread `Min age minutes` as a freshness filter instead of request age. | `tooltip` + `inline help` + `validation copy` | `operator-critical` | `frontend/workflow-editor/src/features/approvals/ApprovalsScreen.tsx:232-261` |
| Admin Web > Approvals | Approver / Reason | The decision panel uses a plain text `Approver` input and a `Reason` textarea; the only enforcement is submit-time required-field validation. | The UI does not explain what approver identity is expected or how detailed the recorded reason should be for audit review. | `inline help` + `validation copy` | `operator-critical` | `frontend/workflow-editor/src/features/approvals/ApprovalsScreen.tsx:321-389` |
| Admin Web > Approvals | Redacted payload / Audit context | The detail view shows raw JSON panels for `redacted_payload` and `audit_context` with no helper text about redaction scope or how to compare the approval to audit entries. | Operators may assume the payload is complete or miss the relationship between the approval record and the matching tool-call audit trail. | `documentation link` + `inline help` | `operator-critical` | `frontend/workflow-editor/src/features/approvals/ApprovalsScreen.tsx:400-437`; `frontend/workflow-editor/src/features/toolCalls/ToolCallsPage.tsx:775-817` |
| Admin Web > Sessions | Model | The session editor keeps `Model` as a plain input with the placeholder `Required to start or resume the agent` and a short datalist of model names. | Operators can override the inherited or default model without understanding that the choice affects the next start or resume. | `tooltip` | `operator-critical` | `frontend/workflow-editor/src/features/sessions/SessionsScreen.tsx:1122-1143` |
| Admin Web > Sessions | Allowed tools / Require approval | Allowed tools are grouped into collapsible sections with one checkbox per tool, an `Approval required` badge, and a per-tool `Require approval` checkbox. | The difference between allowing a tool and gating it behind approval is implicit, so operators can misconfigure a tool's access path. | `inline help` | `operator-critical` | `frontend/workflow-editor/src/features/sessions/SessionsScreen.tsx:1146-1200` |
| Admin Web > Sessions | Allowed scripts / Discord channel allowlist / Teams target allowlist / Communication file max bytes / Allowed MIME types / Allowed extensions / Blocked extensions / Metadata JSON | These policy fields are plain textareas, a number input, and checkboxes with little or no helper text; only the Teams allowlist shows a short placeholder example. | Operators may not know the expected line format, list syntax, byte units, or that `Metadata JSON` must be valid JSON. | `inline help` + `validation copy` + `structured editor candidate` | `operator-critical` | `frontend/workflow-editor/src/features/sessions/SessionsScreen.tsx:1211-1318` |
| Admin Web > Sessions | Prompt / Schedule type / Cron / Resume at / Resume mode / Enabled | Wake schedules combine a free-text prompt, a recurring-versus-one-time toggle, a cron input, a datetime-local input, a resume-mode select, and an enabled checkbox. | Operators can mix up recurring cron scheduling with one-time resume-at scheduling or miss how `resume_mode` changes the restart behavior. | `tooltip` + `validation copy` | `operator-critical` | `frontend/workflow-editor/src/features/sessions/SessionsScreen.tsx:1348-1395` |
| Admin Web > Tool Calls | Search / Status / Created after / Created before / Limit / Direct directory only | The tool-call audit sidebar uses plain input and select controls with placeholders, plus a `Direct directory only` checkbox; the main list only shows a scoped count and refresh button. | Operators may not know which fields the search box scans, how the datetime filters are interpreted, or that `Limit` caps the returned rows. | `tooltip` + `inline help` | `operator-critical` | `frontend/workflow-editor/src/features/toolCalls/ToolCallsPage.tsx:524-592` |
| Admin Web > Tool Calls | Arguments / Result / Normalized metadata / relation buttons | The detail panel renders redacted JSON for arguments and results, an expanded normalized-metadata disclosure, and navigation buttons into Sessions, Approvals, Workflow runs, Agents, and VMs. | The UI does not explain which values are redacted or that the relation buttons jump to other admin tabs instead of filtering the current audit list. | `inline help` + `documentation link` | `operator-critical` | `frontend/workflow-editor/src/features/toolCalls/ToolCallsPage.tsx:704-817` |
| Admin Web > Configuration | approval_notification_target_ids / communication_file_require_approval_for_send / email_send_enabled / redacted_keys | The configuration screen renders each field with a backend-generated tooltip icon, and the backend already supplies descriptive text plus value hints for these safety-related settings. | The controls are still basic list, checkbox, and text inputs, so operators can change notification, send, or redaction behavior without enough contextual warning. | `tooltip` + `validation copy` + `documentation link` | `operator-critical` | `frontend/workflow-editor/src/features/configuration/ConfigurationScreen.tsx:2458-2472`; `src/agent_sandbox/admin/config_tooltips.py:10-12, 41-43, 65-73`; `src/agent_sandbox/admin/web/workers_config.py:687-705, 816-867` |
| Admin Web > Configuration | execution_backend / container_worker_* | The configuration screen also renders backend-generated tooltips for execution backend and container-worker knobs such as the base URL, default image, job timeout, request timeout, and workspace persistence. | These remain plain select, text, and number inputs, so operators can switch execution targets or tune endpoints and timeouts without a stronger editor pattern. | `tooltip` + `structured editor candidate` | `lower-priority` | `frontend/workflow-editor/src/features/configuration/ConfigurationScreen.tsx:2458-2472`; `src/agent_sandbox/admin/config_tooltips.py:140-160`; `src/agent_sandbox/admin/web/workers_config.py:236-259, 687-705` |

## Notes For Future Updates

When this inventory is expanded, keep the table rows concrete and tied to the current code path or rendered behavior. Avoid speculative rows that cannot be supported by the reviewed source files.
