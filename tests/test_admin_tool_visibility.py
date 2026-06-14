from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.tool_visibility import (
    ToolVisibilityBindingState,
    ToolVisibilityExecutionState,
    ToolVisibilityManifestState,
    ToolVisibilityState,
)
from agent_sandbox.models import SessionManifest, ToolName


def write_config(tmp_path: Path, *, enable_directory_scoped_config: bool = True) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(
        "\n".join(
            [
                f'runtime_dir: "{runtime_dir}"',
                f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
                f'session_store_path: "{runtime_dir / "sessions.json"}"',
                f'approval_store_path: "{runtime_dir / "approvals.json"}"',
                f'draft_store_path: "{runtime_dir / "drafts.json"}"',
                f'email_store_path: "{runtime_dir / "emails.json"}"',
                f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
                f"enable_directory_scoped_config: {str(enable_directory_scoped_config).lower()}",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _tool_rows(payload) -> dict[str, object]:
    return {row.tool_name: row for row in payload.tools}


def _write_audit_log(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                """
                {"event_type":"tool_call","session_id":"session-1","vm_id":"vm-1","directory_path":"root/team","tool_name":"create_ado_backlog_item","request_id":"request-3","approval_request_id":"approval-3","workflow_run_id":"run-3","trace_id":"trace-3","decision":"denied","reason":"blocked by execution policy","args":{"session_id":"session-1","request_id":"request-3","token":"visible-secret"},"result":{"request_id":"request-3","secret":"visible-result-secret","status":"denied"},"created_at":"2026-05-18T10:06:00+00:00"}
                """.strip(),
                """
                {"event_type":"tool_call","session_id":"session-1","vm_id":"vm-1","directory_path":"root/team","tool_name":"mystery_tool","request_id":"request-4","decision":"denied","reason":"blocked by execution policy","args":{"session_id":"session-1","request_id":"request-4","token":"unknown-secret"},"result":{"request_id":"request-4","secret":"unknown-result-secret","status":"denied"},"created_at":"2026-05-18T10:07:00+00:00"}
                """.strip(),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_admin_service_tool_visibility_payload_classifies_rows_and_metadata(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.write_directory_domain_config(
        "root",
        "policy",
        {"mcp_tool_policies": {ToolName.RUN_SCRIPT.value: {"denied": True}}},
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            vm_provider="libvirt",
            allowed_tools=[
                ToolName.CREATE_ADO_BACKLOG_ITEM,
                ToolName.RUN_SCRIPT,
                ToolName.ASSISTANT_CREATE_WORKFLOW,
                ToolName.LIST_UNREAD_FEEDBACK,
            ],
            approval_required_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    _write_audit_log(service.config.audit_log_path)

    payload = service.tool_visibility_payload(
        target="session-1",
        workflow_run_id="run-7",
        mcp_tool_approval_lease_id="lease-9",
    )

    serialized = payload.model_dump(mode="json")
    rows = _tool_rows(payload)

    assert serialized["session_context"]["session_id"] == "session-1"
    assert serialized["session_context"]["vm_id"] == "vm-1"
    assert serialized["session_context"]["vm_provider"] == "libvirt"
    assert serialized["session_context"]["capability_profile"] is None
    assert serialized["query_context"] == {
        "workflow_run_id": "run-7",
        "mcp_tool_approval_lease_id": "lease-9",
    }

    visible = rows[ToolName.CREATE_ADO_BACKLOG_ITEM.value]
    assert visible.visibility_state is ToolVisibilityState.VISIBLE
    assert visible.source == "session.allowed_tools"
    assert visible.category == "Azure DevOps"
    assert visible.provider == "azure_devops"
    assert visible.label == "create ado backlog item"
    assert visible.requires_approval is True
    assert visible.approval_settings is not None

    hidden = rows[ToolName.RUN_SCRIPT.value]
    assert hidden.visibility_state is ToolVisibilityState.HIDDEN_BY_POLICY
    assert hidden.source == "directory.mcp_tool_policies"
    assert hidden.category == "Agent control"
    assert "denied by directory policy" in str(hidden.reason)

    unavailable = rows[ToolName.LIST_UNREAD_FEEDBACK.value]
    assert unavailable.visibility_state is ToolVisibilityState.UNAVAILABLE_TO_SESSION
    assert unavailable.source == "session.metadata.pr_feedback_streams"
    assert "missing metadata.pr_feedback_streams" in str(unavailable.reason)

    assistant_hidden = rows[ToolName.ASSISTANT_CREATE_WORKFLOW.value]
    assert assistant_hidden.visibility_state is ToolVisibilityState.HIDDEN_BY_POLICY
    assert assistant_hidden.source == "session.metadata.capability_profile"
    assert "capability profile" in str(assistant_hidden.reason)

    denied_visible = rows[ToolName.CREATE_ADO_BACKLOG_ITEM.value]
    assert denied_visible.execution_state is ToolVisibilityExecutionState.DENIED_AT_EXECUTION
    assert denied_visible.last_denied_at == datetime(2026, 5, 18, 10, 6, tzinfo=UTC)
    assert denied_visible.decision == "denied"
    assert denied_visible.error_summary == "blocked by execution policy"
    assert denied_visible.request_id == "request-3"
    assert denied_visible.approval_request_id == "approval-3"
    assert denied_visible.workflow_run_id == "run-3"
    assert denied_visible.mcp_tool_approval_lease_id is None
    assert denied_visible.related_links
    assert {
        "session",
        "vm",
        "approval_request",
        "workflow_run",
        "workflow_trace",
    }.issubset({link["kind"] for link in denied_visible.related_links})

    denied_unknown = rows["mystery_tool"]
    assert denied_unknown.visibility_state is ToolVisibilityState.UNKNOWN
    assert denied_unknown.execution_state is ToolVisibilityExecutionState.DENIED_AT_EXECUTION
    assert denied_unknown.category == "Unknown"
    assert denied_unknown.provider is None
    assert denied_unknown.last_denied_at == datetime(2026, 5, 18, 10, 7, tzinfo=UTC)
    assert denied_unknown.error_summary == "blocked by execution policy"

    assert payload.counts.total == len(ToolName) + 1
    assert payload.counts.visible == 1
    assert payload.counts.hidden_by_policy == len(ToolName) - 2
    assert payload.counts.unavailable_to_session == 1
    assert payload.counts.denied_at_execution == 2
    assert payload.counts.unknown == 1
    assert payload.counts.requires_approval == 1

    assert payload.notices
    assert payload.notices[0].kind == "missing_capability_profile"
    assert "assistant tools are hidden" in payload.notices[0].message
    assert payload.binding_context.state is ToolVisibilityBindingState.UNKNOWN
    assert payload.binding_context.manifest_state is ToolVisibilityManifestState.UNKNOWN
    serialized_text = json.dumps(serialized)
    assert "visible-secret" not in serialized_text
    assert "visible-result-secret" not in serialized_text
    assert "unknown-secret" not in serialized_text
    assert "unknown-result-secret" not in serialized_text


def test_admin_service_tool_visibility_classifies_delete_ado_backlog_item(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            vm_provider="libvirt",
            allowed_tools=[ToolName.DELETE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    payload = service.tool_visibility_payload(target="session-1")
    rows = _tool_rows(payload)
    delete_row = rows[ToolName.DELETE_ADO_BACKLOG_ITEM.value]

    assert delete_row.visibility_state is ToolVisibilityState.VISIBLE
    assert delete_row.source == "session.allowed_tools"
    assert delete_row.provider == "azure_devops"
    assert delete_row.category == "Azure DevOps"
    assert delete_row.label == "delete ado backlog item"
    assert delete_row.requires_approval is True


def test_admin_service_tool_visibility_payload_classifies_update_title_tool(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            vm_provider="libvirt",
            allowed_tools=[ToolName.UPDATE_ADO_BACKLOG_TITLE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    payload = service.tool_visibility_payload(target="session-1")
    rows = _tool_rows(payload)

    visible = rows[ToolName.UPDATE_ADO_BACKLOG_TITLE.value]
    assert visible.visibility_state is ToolVisibilityState.VISIBLE
    assert visible.source == "session.allowed_tools"
    assert visible.category == "Azure DevOps"
    assert visible.provider == "azure_devops"
    assert visible.label == "update ado backlog title"


def test_admin_service_tool_visibility_payload_flags_stale_manifest(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    initial_session = SessionManifest(
        session_id="session-1",
        vm_id="vm-1",
        vm_provider="libvirt",
        allowed_tools=[ToolName.GET_WORKFLOW_SNAPSHOT],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"capability_profile": "full-admin"},
    )
    service.runtime.session_repository.upsert_session(initial_session)

    visible_tool_names = sorted(
        visibility.tool.value
        for visibility in service.runtime.policy.visible_tools_for_session(initial_session)
    )
    service.runtime.mcp_transport_bindings.record_visible_tools(
        transport_session_id="transport-1",
        bound_session_id="session-1",
        visible_tool_names=visible_tool_names,
    )

    updated_session = initial_session.model_copy(
        update={"allowed_tools": [ToolName.RUN_SCRIPT]}
    )
    service.runtime.session_repository.upsert_session(updated_session)

    payload = service.tool_visibility_payload(
        target="session-1",
        mcp_transport_session_id="transport-1",
    )

    assert payload.binding_context.state is ToolVisibilityBindingState.BOUND
    assert payload.binding_context.manifest_state is ToolVisibilityManifestState.STALE_MANIFEST_POSSIBLE
    assert payload.binding_context.bound_session_id == "session-1"
    assert payload.binding_context.transport_session_id == "transport-1"
    assert payload.binding_context.last_tools_list_at is not None
    assert "stale" in payload.binding_context.message.lower()
    assert any(notice.kind == "stale_client_manifest" for notice in payload.notices)


def test_admin_service_tool_visibility_payload_reports_mismatch_for_other_session(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    session_a = SessionManifest(
        session_id="session-a",
        vm_id="vm-a",
        vm_provider="libvirt",
        allowed_tools=[ToolName.GET_WORKFLOW_SNAPSHOT],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session_b = SessionManifest(
        session_id="session-b",
        vm_id="vm-b",
        vm_provider="libvirt",
        allowed_tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.session_repository.upsert_session(session_a)
    service.runtime.session_repository.upsert_session(session_b)

    visible_tool_names = sorted(
        visibility.tool.value
        for visibility in service.runtime.policy.visible_tools_for_session(session_a)
    )
    service.runtime.mcp_transport_bindings.record_visible_tools(
        transport_session_id="transport-1",
        bound_session_id="session-a",
        visible_tool_names=visible_tool_names,
    )

    payload = service.tool_visibility_payload(
        target="session-b",
        mcp_transport_session_id="transport-1",
    )

    assert payload.binding_context.state is ToolVisibilityBindingState.MISMATCH
    assert payload.binding_context.bound_session_id == "session-a"
    assert payload.binding_context.transport_session_id == "transport-1"
    assert payload.binding_context.manifest_state in {
        ToolVisibilityManifestState.UNKNOWN,
        ToolVisibilityManifestState.STALE_MANIFEST_POSSIBLE,
        ToolVisibilityManifestState.CURRENT,
    }
    assert "not session-b" in payload.binding_context.message
