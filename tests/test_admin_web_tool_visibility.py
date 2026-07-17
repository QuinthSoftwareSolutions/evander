from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.testclient import TestClient

from evander.admin.composition import build_admin_runtime
from evander.admin.tool_visibility import (
    ToolVisibilityBindingState,
    ToolVisibilityManifestState,
)
from evander.admin.web.app import create_app
from evander.models import SessionManifest, ToolName


def write_config(tmp_path: Path) -> Path:
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
                "enable_directory_scoped_config: true",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _write_denied_audit_log(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "tool_call",
                        "session_id": "session-1",
                        "vm_id": "vm-1",
                        "directory_path": "root/team",
                        "tool_name": "create_ado_backlog_item",
                        "request_id": "request-1",
                        "approval_request_id": "approval-1",
                        "workflow_run_id": "workflow-1",
                        "decision": "denied",
                        "reason": "blocked by execution policy",
                        "args": {
                            "session_id": "session-1",
                            "request_id": "request-1",
                            "token": "visible-secret",
                        },
                        "result": {
                            "request_id": "request-1",
                            "secret": "visible-result-secret",
                            "status": "denied",
                        },
                        "created_at": "2026-05-18T10:06:00+00:00",
                    },
                    sort_keys=True,
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_admin_web_tool_visibility_exposes_policy_binding_and_denial_summary(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.operations_admin.write_directory_domain_config(
        "root",
        "policy",
        {"mcp_tool_policies": {ToolName.RUN_SCRIPT.value: {"denied": True}}},
    )
    session = SessionManifest(
        session_id="session-1",
        vm_id="vm-1",
        vm_provider="libvirt",
        allowed_tools=[
            ToolName.CREATE_ADO_BACKLOG_ITEM,
            ToolName.APPROVAL_CHECK,
            ToolName.RUN_SCRIPT,
            ToolName.ASSISTANT_CREATE_WORKFLOW,
            ToolName.LIST_UNREAD_FEEDBACK,
        ],
        approval_required_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.session_repository.upsert_session(session)
    _write_denied_audit_log(service.config.audit_log_path)

    visible_tool_names = sorted(
        visibility.tool.value
        for visibility in service.runtime.policy.visible_tools_for_session(session)
    )
    service.runtime.mcp_transport_bindings.record_visible_tools(
        transport_session_id="transport-1",
        bound_session_id="session-1",
        visible_tool_names=visible_tool_names,
    )

    updated_session = session.model_copy(
        update={
            "allowed_tools": [
                ToolName.CREATE_ADO_BACKLOG_ITEM,
                ToolName.RUN_SCRIPT,
                ToolName.ASSISTANT_CREATE_WORKFLOW,
                ToolName.LIST_UNREAD_FEEDBACK,
            ]
        }
    )
    service.runtime.session_repository.upsert_session(updated_session)
    client = TestClient(create_app(service))

    response = client.get(
        "/api/admin/tool-visibility",
        params={
            "session_id": "session-1",
            "workflow_run_id": "workflow-7",
            "mcp_tool_approval_lease_id": "lease-9",
            "mcp_transport_session_id": "transport-1",
        },
    )

    payload = response.json()
    rows = {row["tool_name"]: row for row in payload["tools"]}

    assert response.status_code == 200
    assert payload["session_context"]["session_id"] == "session-1"
    assert payload["session_context"]["is_expired"] is False
    assert payload["session_context"]["vm_id"] == "vm-1"
    assert payload["session_context"]["vm_provider"] == "libvirt"
    assert payload["query_context"] == {
        "workflow_run_id": "workflow-7",
        "mcp_tool_approval_lease_id": "lease-9",
    }
    assert rows["create_ado_backlog_item"]["visibility_state"] == "visible"
    assert rows["create_ado_backlog_item"]["source"] == "session.allowed_tools"
    assert rows["create_ado_backlog_item"]["provider"] == "azure_devops"
    assert rows["create_ado_backlog_item"]["category"] == "Azure DevOps"
    assert rows["create_ado_backlog_item"]["requires_approval"] is True
    assert rows["create_ado_backlog_item"]["approval_locked"] is False
    assert rows["create_ado_backlog_item"]["execution_state"] == "denied_at_execution"
    assert rows["create_ado_backlog_item"]["error_summary"] == "blocked by execution policy"
    assert rows["delete_ado_backlog_item"]["provider"] == "azure_devops"
    assert rows["delete_ado_backlog_item"]["category"] == "Azure DevOps"
    assert rows["delete_ado_backlog_item"]["requires_approval"] is True
    assert rows["delete_ado_backlog_item"]["visibility_state"] == "hidden_by_policy"
    assert rows["run_script"]["visibility_state"] == "hidden_by_policy"
    assert rows["run_script"]["source"] == "directory.mcp_tool_policies"
    assert rows["assistant_create_workflow"]["visibility_state"] == "hidden_by_policy"
    assert rows["assistant_create_workflow"]["source"] == "session.metadata.capability_profile"
    assert rows["list_unread_feedback"]["visibility_state"] == "unavailable_to_session"
    assert rows["list_unread_feedback"]["source"] == "session.metadata.pr_feedback_streams"
    assert payload["binding_context"]["state"] == ToolVisibilityBindingState.BOUND.value
    assert (
        payload["binding_context"]["manifest_state"]
        == ToolVisibilityManifestState.STALE_MANIFEST_POSSIBLE.value
    )
    assert payload["binding_context"]["transport_session_id"] == "transport-1"
    assert payload["binding_context"]["bound_session_id"] == "session-1"
    assert any(notice["kind"] == "missing_capability_profile" for notice in payload["notices"])
    assert any(notice["kind"] == "stale_client_manifest" for notice in payload["notices"])
    assert payload["counts"]["visible"] == 1
    assert payload["counts"]["hidden_by_policy"] == len(ToolName) - 2
    assert payload["counts"]["unavailable_to_session"] == 1
    assert payload["counts"]["denied_at_execution"] == 1
    assert payload["counts"]["requires_approval"] == 1
    assert "visible-secret" not in json.dumps(payload)
    assert "visible-result-secret" not in json.dumps(payload)


def test_admin_web_tool_visibility_marks_locked_approval_rows(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    session = SessionManifest(
        session_id="session-locked",
        vm_id="vm-1",
        vm_provider="libvirt",
        allowed_tools=[
            ToolName.DELETE_ADO_BACKLOG_ITEM,
            ToolName.CREATE_ADO_BACKLOG_ITEM,
        ],
        approval_required_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.session_repository.upsert_session(session)
    client = TestClient(create_app(service))

    response = client.get(
        "/api/admin/tool-visibility",
        params={"session_id": "session-locked"},
    )

    payload = response.json()
    rows = {row["tool_name"]: row for row in payload["tools"]}

    assert response.status_code == 200
    assert rows["delete_ado_backlog_item"]["visibility_state"] == "visible"
    assert rows["delete_ado_backlog_item"]["requires_approval"] is True
    assert rows["delete_ado_backlog_item"]["approval_locked"] is True
    assert rows["create_ado_backlog_item"]["requires_approval"] is True
    assert rows["create_ado_backlog_item"]["approval_locked"] is False


def test_admin_web_tool_visibility_classifies_update_title_tool(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    session = SessionManifest(
        session_id="session-1",
        vm_id="vm-1",
        vm_provider="libvirt",
        allowed_tools=[ToolName.UPDATE_ADO_BACKLOG_TITLE],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.session_repository.upsert_session(session)
    client = TestClient(create_app(service))

    response = client.get("/api/admin/tool-visibility", params={"session_id": "session-1"})
    payload = response.json()
    rows = {row["tool_name"]: row for row in payload["tools"]}

    assert response.status_code == 200
    assert rows["update_ado_backlog_title"]["visibility_state"] == "visible"
    assert rows["update_ado_backlog_title"]["source"] == "session.allowed_tools"
    assert rows["update_ado_backlog_title"]["provider"] == "azure_devops"
    assert rows["update_ado_backlog_title"]["category"] == "Azure DevOps"


def test_admin_web_tool_visibility_reports_expired_session_and_binding_mismatch(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
        allowed_tools=[ToolName.APPROVAL_CHECK],
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
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
    client = TestClient(create_app(service))

    response = client.get(
        "/api/admin/sessions/session-b/tool-visibility",
        params={"mcp_transport_session_id": "transport-1"},
    )

    payload = response.json()

    assert response.status_code == 200
    assert payload["session_context"]["session_id"] == "session-b"
    assert payload["session_context"]["is_expired"] is True
    assert payload["binding_context"]["state"] == ToolVisibilityBindingState.MISMATCH.value
    assert payload["binding_context"]["bound_session_id"] == "session-a"
    assert "not session-b" in payload["binding_context"]["message"]
    assert payload["counts"]["visible"] == 1


def test_admin_web_tool_visibility_validates_session_id_and_unknown_session(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))

    missing_session = client.get("/api/admin/tool-visibility")
    unknown_session = client.get("/api/admin/tool-visibility", params={"session_id": "missing"})

    assert missing_session.status_code == 400
    assert missing_session.json()["error"] == "session_id is required."
    assert unknown_session.status_code == 404
    assert unknown_session.json()["error"] == "Unknown session: missing"
