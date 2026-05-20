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
- `confirmation copy`: Confirmation-dialog text that explains the consequence of a destructive action.
- `structured editor candidate`: A stronger UI pattern that replaces free-form input with a dedicated editor or selector.
- `empty-state copy`: Guidance shown when no data is available or no selection has been made.
- `documentation link`: A link to longer-form guidance or a reference page.

## Source Files Reviewed

The evidence in this document should come from the current repository state. When a row is added, cite the smallest useful source area rather than a broad file blob.

- `frontend/workflow-editor/src/App.tsx`
- `frontend/workflow-editor/src/features/approvals/ApprovalsScreen.tsx`
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
| Workflow editor > Current runs | Resume from failure / Dismiss run / Clear run | Errored resumable runs show `Resume from failure`; every visible run has a `Dismiss run` or `Clear run` button, and the handler uses `window.confirm(...)` before deleting the run record. | The UI does not explain whether resume continues from the failed node or restarts, and the clear/dismiss action sounds destructive without clarifying what history remains. | `tooltip` + `confirmation copy` | `operator-critical` | `frontend/workflow-editor/src/App.tsx:2673-2708`; `frontend/workflow-editor/src/App.tsx:4981-5004` |
| Workflow editor > Inspector | Value mode and binding reference | Bindable fields can switch between `Literal`, `Bind`, and `Template`; the binding editor shows a `Suggested upstream output` select and a `Binding reference` input with placeholder `${{node.output.path}}`. | New users can confuse a direct binding token with a template fragment or a literal string and end up with malformed outputs. | `inline help` | `operator-critical` | `frontend/workflow-editor/src/App.tsx:2185-2215`; `frontend/workflow-editor/src/App.tsx:3941-3946` |
| Workflow editor > Workflow catalog | `effect:agent:prompt` target, session_id, vm_name, prompt, resume_thread_id | The catalog defines `target`, `session_id`, `vm_name`, `prompt`, `model`, `vm_user`, and `resume_thread_id`; the editor collapses the target choice into a separate selector with `Generic target`, `Session ID`, and `VM name`. | Dispatch target semantics and resume-thread behavior are implicit, so operators can route prompts to the wrong session or assume `resume_thread_id` creates a new thread. | `tooltip` + `structured editor candidate` | `operator-critical` | `src/agent_sandbox/admin/service.py:2224-2236`; `frontend/workflow-editor/src/App.tsx:3848-3888`; `src/agent_sandbox/admin/service.py:9150-9185` |
| Workflow editor > Workflow catalog | `effect:agent:exec` / `effect:vm:exec` environment, stdin, timeout_seconds, check | Both catalog entries expose `environment` as JSON, `stdin` as multiline text, `timeout_seconds` as a number, and `check` as a boolean, but the fields have no descriptions. | Operators may not know the timeout unit, the JSON object shape expected for environment variables, or how `check` changes failure behavior. | `tooltip` + `validation copy` | `operator-critical` | `src/agent_sandbox/admin/service.py:2277-2291`; `src/agent_sandbox/admin/service.py:2387-2401` |
| Workflow editor > Workflow catalog | `effect:vm:create` bootstrap and scope fields | `effect:vm:create` defines `vm_name`, `session_id`, `labels`, `model`, `vm_user`, `ttl_hours`, `allowed_tools`, `allowed_script_names`, `discord_channels`, `email_mailboxes`, `email_folders`, `dotnet`, `nuget`, `mssql`, `mssql_init_root`, `playwright`, `codex_skills`, and `directory_path`, while nearby `effect:agent:create` carries much richer descriptions for similar lifetime and bootstrap controls. | Operators can change the created VM's lifetime or bootstrap footprint without understanding how it differs from creating an agent session, especially when the same fields are mostly bare inputs. | `tooltip` + `documentation link` | `lower-priority` | `src/agent_sandbox/admin/service.py:1762-1794`; `src/agent_sandbox/admin/service.py:1860-2056` |
| Workflow editor > Workflow catalog | `effect:communication:send-message` communication_target_id, message, thread_id | `effect:communication:send-message` exposes `communication_target_id`, `message`, and `thread_id`; the target options are supplied from directory config at catalog-read time, but the field itself has no description. | Target provenance is opaque and `thread_id` can be mistaken for a destination id, so operators may send to the wrong channel or thread. | `tooltip` + `structured editor candidate` | `operator-critical` | `src/agent_sandbox/admin/service.py:2521-2526`; `src/agent_sandbox/admin/service.py:9150-9185` |
| Workflow editor > Graph metadata | Directories and User IDs | Graph metadata uses multiline textareas for `Directories` and `User IDs`; the backend normalizes `directory_paths` and `user_ids`, defaults empty directories to `root`, and strips blanks and duplicates. | Operators may treat these as free-form notes, but they affect graph scoping and ownership, and an empty directories field silently falls back to `root`. | `inline help` | `lower-priority` | `frontend/workflow-editor/src/App.tsx:5048-5072`; `src/agent_sandbox/admin/service.py:378-388`; `src/agent_sandbox/admin/service.py:8778-8808`; `src/agent_sandbox/admin/service.py:8890-8915` |

## Notes For Future Updates

When this inventory is expanded, keep the table rows concrete and tied to the current code path or rendered behavior. Avoid speculative rows that cannot be supported by the reviewed source files.
