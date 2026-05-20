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

## Notes For Future Updates

When this inventory is expanded, keep the table rows concrete and tied to the current code path or rendered behavior. Avoid speculative rows that cannot be supported by the reviewed source files.
