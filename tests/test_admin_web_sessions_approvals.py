from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MethodType

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

import agent_sandbox.admin.service as admin_service
from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import create_app
from agent_sandbox.admin.web.shared import AdminWebAuth, AdminWebConfig
from agent_sandbox.admin.web.sessions_approvals import session_approval_routes
from agent_sandbox.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.execution.container_worker_client import ContainerWorkerClientError
from agent_sandbox.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from agent_sandbox.models import (
    ApprovalNotificationDeliveryRecord,
    ApprovalNotificationDeliveryStatus,
    ApprovalNotificationStatus,
    ApprovalRequest,
    ApprovalStatus,
    SessionManifest,
    ToolName,
    WakeDispatchRecord,
    WakeResumePromptPayload,
)


ROOT = Path(__file__).resolve().parents[1]


def write_config(tmp_path: Path, *, enable_directory_scoped_config: bool = False) -> Path:
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


def _admin_json_request(
    *,
    method: str,
    path: str,
    path_params: dict[str, str] | None = None,
    payload: dict[str, object] | None = None,
    query_string: bytes = b"",
) -> Request:
    body = json.dumps(payload or {}).encode()
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"content-type", b"application/json")],
            "query_string": query_string,
            "path_params": path_params or {},
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        },
        receive,
    )


def test_sessions_screen_groups_voice_calling_and_workspace_staging_tools() -> None:
    source = (
        ROOT / "frontend" / "workflow-editor" / "src" / "features" / "sessions" / "SessionsScreen.tsx"
    ).read_text(encoding="utf-8")

    assert '"Voice calling"' in source
    assert (
        '"list_contacts", "make_phone_call", "get_phone_call_result", "submit_voice_query_response"'
        in source
    )
    assert (
        '"stage_artifact",\n    "communication_copy_artifact_to_workspace",\n'
        '    "communication_stage_workspace_file",'
        in source
    )


def test_mcp_tools_page_renders_tool_descriptions() -> None:
    source = (
        ROOT / "frontend" / "workflow-editor" / "src" / "features" / "mcpTools" / "McpToolsPage.tsx"
    ).read_text(encoding="utf-8")

    assert "mcp-tool-policy-description" in source
    assert "item.description" in source


def test_admin_web_exposes_approvals_index_detail_and_decision(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14084-session",
            vm_id="vm-14084",
            allowed_tools=[ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    service.runtime.approvals.put(
        "approval-1",
        ApprovalRequest(
            request_id="approval-1",
            session_id="issue-14084-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="digest",
            human_summary="Send release email",
            payload={"token": "secret", "body": "hello"},
            directory_path="root",
            vm_id="vm-14084",
            notification_status=ApprovalNotificationStatus.DELIVERED,
            notification_target_ids=["ops-approvals"],
            notification_attempted_at=datetime.now(UTC),
            notification_deliveries=[
                ApprovalNotificationDeliveryRecord(
                    target_id="ops-approvals",
                    status=ApprovalNotificationDeliveryStatus.SENT,
                    provider="teams",
                    message_id="msg-123",
                    result_summary="status=sent",
                )
            ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.config.audit_log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "tool_call",
                        "session_id": "issue-14084-session",
                        "vm_id": "vm-14084",
                        "tool_name": "email_send_approved",
                        "approval_request_id": "approval-1",
                        "decision": "denied",
                        "reason": "payload mismatch",
                        "args": {
                            "session_id": "issue-14084-session",
                            "request_id": "approval-1",
                            "token": "secret",
                        },
                        "result": {
                            "status": "denied",
                            "request_id": "approval-1",
                            "approval_diagnostic": {
                                "approval_request_id": "approval-1",
                                "rejection_kind": "payload_mismatch",
                                "approved_payload_digest": "digest",
                                "attempted_payload_digest": "attempted-digest",
                                "approved_payload": {
                                    "body": "hello",
                                    "token": "approved-secret",
                                },
                                "attempted_payload": {
                                    "body": "hello",
                                    "token": "attempted-secret",
                                },
                                "differences": {
                                    "changed": [
                                        {
                                            "path": "payload.token",
                                            "approved_value": "<redacted>",
                                            "attempted_value": "<redacted>",
                                        }
                                    ]
                                },
                            },
                        },
                        "created_at": "2026-05-18T10:00:00+00:00",
                    },
                    sort_keys=True,
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(service))

    index_response = client.get("/api/admin/approvals?statuses=pending")
    detail_response = client.get("/api/admin/approvals/approval-1")
    decision_response = client.post(
        "/api/admin/approvals/approval-1/decision",
        json={"approver": "alice", "approve": False, "reason": "unsafe"},
    )

    assert index_response.status_code == 200
    assert index_response.json()["health"]["pending"] == 1
    assert index_response.json()["items"][0]["request_id"] == "approval-1"
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["request"]["tool_name"] == "email_send_approved"
    assert detail_payload["request"]["payload"]["token"] == "<redacted>"
    assert detail_payload["redacted_payload"]["token"] == "<redacted>"
    assert detail_payload["payload_summary"] == '{"body":"hello","token":"<redacted>"}'
    assert detail_payload["approval_diagnostics"]["request_id"] == "approval-1"
    assert detail_payload["approval_diagnostics"]["status"] == "pending"
    assert detail_payload["approval_diagnostics"]["approved_payload"]["token"] == "<redacted>"
    assert detail_payload["approval_diagnostics"]["blocked_tool_call_diagnostics"][0][
        "attempted_payload"
    ]["token"] == "<redacted>"
    assert detail_payload["audit_context"][0]["result"]["approval_diagnostic"]["attempted_payload"][
        "token"
    ] == "<redacted>"
    assert detail_payload["request"]["notification_status"] == "delivered"
    assert detail_payload["request"]["notification_target_ids"] == ["ops-approvals"]
    assert detail_payload["request"]["notification_deliveries"][0]["target_id"] == "ops-approvals"
    assert detail_payload["request"]["notification_deliveries"][0]["status"] == "sent"
    serialized_detail = json.dumps(detail_payload)
    assert "approved-secret" not in serialized_detail
    assert "attempted-secret" not in serialized_detail
    assert "blocked-diagnostic-secret" not in serialized_detail
    assert decision_response.status_code == 200
    assert decision_response.json()["request"]["status"] == "denied"


def test_admin_web_approval_detail_falls_back_to_action_type_for_tool_name(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14084-session",
            vm_id="vm-14084",
            allowed_tools=[ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    service.runtime.approvals.put(
        "approval-2",
        ApprovalRequest(
            request_id="approval-2",
            session_id="issue-14084-session",
            action_type="shell_exec_approved",
            tool_name=None,
            payload_digest="digest",
            human_summary="Run approved shell command",
            payload={"secret": "top-secret", "command": "echo ok"},
            directory_path="root",
            vm_id="vm-14084",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    client = TestClient(create_app(service))

    detail_response = client.get("/api/admin/approvals/approval-2")

    assert detail_response.status_code == 200
    assert detail_response.json()["request"]["tool_name"] == "shell_exec_approved"
    assert detail_response.json()["request"]["payload"]["secret"] == "<redacted>"


def test_admin_web_rejects_invalid_approval_decisions(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14084-session",
            vm_id="vm-14084",
            allowed_tools=[ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    service.runtime.approvals.put(
        "approval-1",
        ApprovalRequest(
            request_id="approval-1",
            session_id="issue-14084-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="digest",
            human_summary="Send release email",
            payload={"body": "hello"},
            directory_path="root",
            vm_id="vm-14084",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    client = TestClient(create_app(service))

    missing_approver = client.post(
        "/api/admin/approvals/approval-1/decision",
        json={"approver": "", "approve": True, "reason": "ship it"},
    )
    missing_reason = client.post(
        "/api/admin/approvals/approval-1/decision",
        json={"approver": "alice", "approve": True, "reason": ""},
    )
    bad_approve_type = client.post(
        "/api/admin/approvals/approval-1/decision",
        json={"approver": "alice", "approve": "yes", "reason": "ship it"},
    )

    assert missing_approver.status_code == 400
    assert missing_approver.json()["error"] == "Approver is required."
    assert missing_reason.status_code == 400
    assert missing_reason.json()["error"] == "Reason is required."
    assert bad_approve_type.status_code == 400
    assert bad_approve_type.json()["error"] == "approve must be a boolean."


def test_admin_web_uses_authenticated_identity_for_approval_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14084-session",
            vm_id="vm-14084",
            allowed_tools=[ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    service.runtime.approvals.put(
        "approval-1",
        ApprovalRequest(
            request_id="approval-1",
            session_id="issue-14084-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="digest",
            human_summary="Send release email",
            payload={"body": "hello"},
            directory_path="root",
            vm_id="vm-14084",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    client = TestClient(create_app(service))

    login = client.post("/api/auth/login", json={"username": "operator", "password": "swordfish"})
    decision = client.post(
        "/api/admin/approvals/approval-1/decision",
        json={"approver": "spoofed-user", "approve": True, "reason": "safe"},
    )

    stored = service.runtime.approvals.get("approval-1")
    assert login.status_code == 200
    assert decision.status_code == 200
    assert stored is not None
    assert stored.status == ApprovalStatus.APPROVED
    assert stored.approver == "operator"
    assert stored.decision_history[-1].approver == "operator"


def test_admin_web_revokes_valid_approved_approval(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14084-session",
            vm_id="vm-14084",
            allowed_tools=[ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    service.runtime.approvals.put(
        "approval-1",
        ApprovalRequest(
            request_id="approval-1",
            session_id="issue-14084-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="digest",
            human_summary="Send release email",
            payload={"body": "hello"},
            directory_path="root",
            vm_id="vm-14084",
            status=ApprovalStatus.APPROVED,
            approver="ops",
            decision_at=datetime.now(UTC),
            decision_reason="safe",
            decision_history=[
                {
                    "status": "approved",
                    "approver": "ops",
                    "reason": "safe",
                    "decided_at": datetime.now(UTC),
                }
            ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    client = TestClient(create_app(service))

    revoked = client.post(
        "/api/admin/approvals/approval-1/revoke",
        json={"approver": "alice", "reason": "approval no longer applies"},
    )

    stored = service.runtime.approvals.get("approval-1")
    assert revoked.status_code == 200
    assert revoked.json()["request"]["status"] == "denied"
    assert stored is not None
    assert stored.status == ApprovalStatus.DENIED
    assert stored.approver == "alice"
    assert stored.decision_reason == "approval no longer applies"
    assert stored.decision_history[-1].status == ApprovalStatus.DENIED
    assert stored.decision_history[-1].reason == "approval no longer applies"


def test_admin_web_exposes_sessions_catalog_list_detail_and_mutations(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14085-session",
            vm_id="vm-14085",
            model="gpt-5.3-codex",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            tool_argument_policies={
                "create_ado_backlog_item": [
                    {
                        "id": "issue-policy",
                        "constraints": [
                            {
                                "id": "title",
                                "path": "title",
                                "operator": "required",
                            }
                        ],
                    }
                ]
            },
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    client = TestClient(create_app(service))

    catalog_response = client.get("/api/admin/sessions/catalog")
    list_response = client.get("/api/admin/sessions")
    detail_response = client.get("/api/admin/sessions/issue-14085-session")
    model_response = client.put(
        "/api/admin/sessions/issue-14085-session/model",
        json={"model": "gpt-5.5"},
    )
    tools_response = client.put(
        "/api/admin/sessions/issue-14085-session/allowed-tools",
        json={"allowed_tools": ["approval_check", "approval_submit"]},
    )
    renew_response = client.post(
        "/api/admin/sessions/issue-14085-session/renew",
        json={"extension": "6h"},
    )
    revoke_response = client.post("/api/admin/sessions/issue-14085-session/revoke")

    assert catalog_response.status_code == 200
    catalog_payload = catalog_response.json()
    assert any(item["value"] == "approval_check" for item in catalog_payload["tool_options"])
    assert any(
        item["field_name"] == "tool_argument_policies"
        and item["kind"] == "json"
        and item["section"] == "Tool argument policy"
        for item in catalog_payload["session_policy_fields"]
    )
    assert any(
        item["field_name"] == "allowed_contact_ids"
        and item["kind"] == "string_list"
        and item["section"] == "Contacts"
        for item in catalog_payload["session_policy_fields"]
    )
    session_policy_fields = {
        item["field_name"]: item for item in catalog_payload["session_policy_fields"]
    }
    assert session_policy_fields["communication_file_receive_enabled"]["section"] == (
        "Communication files"
    )
    assert session_policy_fields["communication_file_receive_enabled"]["kind"] == "bool"
    assert session_policy_fields["communication_file_max_bytes"]["kind"] == "int_or_null"
    assert (
        session_policy_fields["communication_file_allowed_mime_types"]["kind"]
        == "string_list"
    )
    catalog_tool_values = {item["value"] for item in catalog_payload["tool_options"]}
    assert {
        "add_message_reaction",
        "get_phone_call_result",
        "list_contacts",
        "make_phone_call",
        "post_pr_inline_comment",
        "teams_meeting_get_events",
        "teams_meeting_speak",
        "teams_meeting_stop",
    }.issubset(catalog_tool_values)
    tool_options = {item["value"]: item for item in catalog_payload["tool_options"]}
    missing_catalog_descriptions = [
        item["value"] for item in catalog_payload["tool_options"] if not item["description"].strip()
    ]
    assert missing_catalog_descriptions == []
    assert "List directory-configured contact ids" in tool_options["list_contacts"]["description"]
    assert "Start host-side phone calls" in tool_options["make_phone_call"]["description"]
    assert "Read phone call status" in tool_options["get_phone_call_result"]["description"]
    assert "Submit a human approval request" in tool_options["approval_submit"]["description"]
    assert "Check the current state" in tool_options["approval_check"]["description"]
    assert "Delete Azure DevOps backlog items" in tool_options[
        "delete_ado_backlog_item"
    ]["description"]
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["session_id"] == "issue-14085-session"
    assert (
        list_response.json()["items"][0]["effective_tool_policy"]["tool_argument_policies"][
            "create_ado_backlog_item"
        ][0]["id"]
        == "issue-policy"
    )
    assert list_response.json()["active_items"][0]["session_id"] == "issue-14085-session"
    assert list_response.json()["counts"]["active"] == 1
    assert detail_response.status_code == 200
    assert detail_response.json()["session"]["model"] == "gpt-5.3-codex"
    assert detail_response.json()["session"]["tool_summary"]["session_id"] == "issue-14085-session"
    assert any(
        item["tool_name"] == "approval_check"
        for item in detail_response.json()["session"]["tool_summary"]["available_tools"]
    )
    assert detail_response.json()["session"]["runtime_image"] == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Legacy session: VM template metadata unavailable",
        "template_alias": None,
        "image_ref": None,
        "image_ref_sanitized": False,
        "missing_reason": "legacy_missing_libvirt_vm_template",
    }
    assert detail_response.json()["session"]["effective_tool_policy"]["allowed_tools"] == [
        "approval_check"
    ]
    assert (
        detail_response.json()["session"]["effective_tool_policy"]["tool_argument_policies"][
            "create_ado_backlog_item"
        ][0]["id"]
        == "issue-policy"
    )
    assert detail_response.json()["summary_json"]["session_id"] == "issue-14085-session"
    assert list_response.json()["items"][0]["runtime_image"] == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Legacy session: VM template metadata unavailable",
        "template_alias": None,
        "image_ref": None,
        "image_ref_sanitized": False,
        "missing_reason": "legacy_missing_libvirt_vm_template",
    }
    assert model_response.status_code == 200
    assert model_response.json()["action"] == "set_session_model"
    assert model_response.json()["session"]["model"] == "gpt-5.5"
    assert model_response.json()["session"]["runtime_image"] == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Legacy session: VM template metadata unavailable",
        "template_alias": None,
        "image_ref": None,
        "image_ref_sanitized": False,
        "missing_reason": "legacy_missing_libvirt_vm_template",
    }
    assert tools_response.status_code == 200
    assert tools_response.json()["action"] == "set_session_allowed_tools"
    assert tools_response.json()["session"]["allowed_tools"] == [
        "approval_check",
        "approval_submit",
    ]
    assert renew_response.status_code == 200
    assert renew_response.json()["action"] == "renew_session"
    assert revoke_response.status_code == 200
    assert revoke_response.json()["action"] == "revoke_session"


def test_admin_web_sessions_catalog_uses_model_pricing_catalog_models(
    tmp_path: Path, monkeypatch
) -> None:
    catalog_path = tmp_path / "runtime" / "pricing.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(
            {
                "version": "test-pricing",
                "currency": "usd",
                "description": "Test pricing catalog.",
                "sources": ["https://platform.openai.com/docs/pricing"],
                "rules": [
                    {
                        "id": "provider-a:alpha",
                        "provider": "provider-a",
                        "models": ["alpha-model", "shared-model"],
                        "model_patterns": ["alpha-*"],
                        "effective_from": "2026-06-04T00:00:00Z",
                        "effective_to": None,
                        "rates_per_1m_tokens": {
                            "input": "1.00",
                            "cached_input": "0.10",
                            "output": "2.00",
                        },
                        "batch_multiplier": "0.5",
                        "service_tier": None,
                        "long_context": None,
                    },
                    {
                        "id": "provider-b:shared",
                        "provider": "provider-b",
                        "models": ["shared-model", "beta-model"],
                        "model_patterns": [],
                        "effective_from": "2026-06-04T00:00:00Z",
                        "effective_to": None,
                        "rates_per_1m_tokens": {
                            "input": "1.25",
                            "cached_input": "0.12",
                            "output": "2.50",
                        },
                        "batch_multiplier": "0.5",
                        "service_tier": None,
                        "long_context": None,
                    },
                    {
                        "id": "provider-c:wildcard",
                        "provider": "provider-c",
                        "models": [],
                        "model_patterns": ["wildcard-only-*"],
                        "effective_from": "2026-06-04T00:00:00Z",
                        "effective_to": None,
                        "rates_per_1m_tokens": {
                            "input": "1.50",
                            "cached_input": "0.15",
                            "output": "3.00",
                        },
                        "batch_multiplier": "0.5",
                        "service_tier": None,
                        "long_context": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_SANDBOX_MODEL_PRICING_PATH", str(catalog_path))
    service = AdminService(write_config(tmp_path))
    client = TestClient(create_app(service))

    response = client.get("/api/admin/sessions/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_options"] == [
        {"value": "alpha-model", "label": "alpha-model"},
        {"value": "shared-model", "label": "shared-model"},
        {"value": "beta-model", "label": "beta-model"},
    ]
    assert any(item["value"] == "approval_check" for item in payload["tool_options"])


def test_admin_web_sessions_catalog_returns_empty_model_options_when_pricing_catalog_errors(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "AGENT_SANDBOX_MODEL_PRICING_PATH",
        str(tmp_path / "runtime" / "missing-pricing.json"),
    )
    service = AdminService(write_config(tmp_path))
    client = TestClient(create_app(service))

    response = client.get("/api/admin/sessions/catalog")

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_options"] == []
    assert any(item["value"] == "approval_check" for item in payload["tool_options"])


def test_admin_web_exposes_runtime_image_payloads_for_session_list_and_detail(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    explicit_session_id = "libvirt-explicit"
    default_session_id = "libvirt-default"
    legacy_session_id = "libvirt-legacy"
    container_session_id = "container-session"
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id=explicit_session_id,
            vm_id="vm-explicit",
            vm_provider="libvirt_vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            metadata={
                "libvirt_vm_template": {
                    "image_ref": "/var/lib/libvirt/images/agent-sandbox/dotnet-agent.qcow2",
                    "template_alias": "dotnet",
                }
            },
        ),
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id=default_session_id,
            vm_id="vm-default",
            vm_provider="libvirt_vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            metadata={
                "libvirt_vm_template": {
                    "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
                    "template_alias": "default",
                }
            },
        ),
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id=legacy_session_id,
            vm_id="vm-legacy",
            vm_provider="libvirt_vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            metadata={"legacy": True},
        ),
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id=container_session_id,
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            metadata={
                "execution_backend": "container_worker_vm",
                "container_worker": {
                    "image": "runner:latest",
                    "image_alias": "default",
                },
            },
        ),
    )
    client = TestClient(create_app(service))

    list_response = client.get("/api/admin/sessions")
    detail_response = client.get(f"/api/admin/sessions/{explicit_session_id}")
    container_detail_response = client.get(f"/api/admin/sessions/{container_session_id}")

    assert list_response.status_code == 200
    items_by_session_id = {
        item["session_id"]: item for item in list_response.json()["items"]
    }
    assert items_by_session_id[explicit_session_id]["runtime_image"] == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Libvirt VM template",
        "template_alias": "dotnet",
        "image_ref": "dotnet-agent.qcow2",
        "image_ref_sanitized": True,
        "missing_reason": None,
    }
    assert items_by_session_id[default_session_id]["runtime_image"] == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Libvirt VM template",
        "template_alias": "default",
        "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
        "image_ref_sanitized": False,
        "missing_reason": None,
    }
    assert items_by_session_id[legacy_session_id]["runtime_image"] == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Legacy session: VM template metadata unavailable",
        "template_alias": None,
        "image_ref": None,
        "image_ref_sanitized": False,
        "missing_reason": "legacy_missing_libvirt_vm_template",
    }
    assert items_by_session_id[container_session_id]["runtime_image"] == {
        "backend": "container_worker_vm",
        "kind": "container_worker_image",
        "label": "Container worker image",
        "image_alias": "default",
        "image_ref": "runner:latest",
        "missing_reason": None,
    }
    assert detail_response.status_code == 200
    assert detail_response.json()["session"]["runtime_image"] == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Libvirt VM template",
        "template_alias": "dotnet",
        "image_ref": "dotnet-agent.qcow2",
        "image_ref_sanitized": True,
        "missing_reason": None,
    }
    assert (
        detail_response.json()["summary_json"]["metadata"]["libvirt_vm_template"]["image_ref"]
        == "/var/lib/libvirt/images/agent-sandbox/dotnet-agent.qcow2"
    )
    assert container_detail_response.status_code == 200
    assert container_detail_response.json()["session"]["runtime_image"] == {
        "backend": "container_worker_vm",
        "kind": "container_worker_image",
        "label": "Container worker image",
        "image_alias": "default",
        "image_ref": "runner:latest",
        "missing_reason": None,
    }


def test_admin_web_exposes_mcp_tools_directory_policy_api(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write("\nenable_directory_scoped_config: true\n")
    service = AdminService(config_path)
    auth = AdminWebAuth(AdminWebConfig.from_env())
    routes = session_approval_routes(service, auth)
    get_mcp_tools = next(
        route.endpoint
        for route in routes
        if getattr(route, "path", "") == "/api/admin/mcp-tools" and "GET" in route.methods
    )
    update_mcp_tools = next(
        route.endpoint
        for route in routes
        if getattr(route, "path", "") == "/api/admin/mcp-tools" and "PUT" in route.methods
    )

    initial_response = asyncio.run(
        get_mcp_tools(
            _admin_json_request(
                method="GET",
                path="/api/admin/mcp-tools",
                query_string=b"directory_path=root",
            )
        )
    )
    assert initial_response.status_code == 200
    initial_payload = json.loads(initial_response.body)
    initial_by_tool = {item["value"]: item for item in initial_payload["items"]}
    assert set(initial_by_tool) == {tool.value for tool in ToolName}
    missing_descriptions = [
        item["value"] for item in initial_payload["items"] if not item["description"].strip()
    ]
    assert missing_descriptions == []
    assert "Submit a human approval request" in initial_by_tool["approval_submit"][
        "description"
    ]
    assert "Check the current state" in initial_by_tool["approval_check"]["description"]
    assert initial_by_tool["semantic_delete_points"]["approval_required"] is True
    run_script = next(
        item for item in initial_payload["items"] if item["value"] == "run_script"
    )
    assert run_script["effective_settings"] == {
        "ttl_seconds": 3600,
        "max_uses": 1,
        "payload_mode": "exact",
    }

    locked_response = asyncio.run(
        update_mcp_tools(
            _admin_json_request(
                method="PUT",
                path="/api/admin/mcp-tools",
                query_string=b"directory_path=root",
                payload={
                    "expected_revision": initial_payload["revision"],
                    "mcp_tool_policies": {},
                },
            )
        )
    )
    assert locked_response.status_code == 403

    service.unlock_privileged_operations()
    update_request = _admin_json_request(
        method="PUT",
        path="/api/admin/mcp-tools",
        query_string=b"directory_path=root",
        payload={
            "expected_revision": initial_payload["revision"],
            "mcp_tool_policies": {
                "run_script": {
                    "require_approval": True,
                    "default_settings": {
                        "ttl_seconds": 600,
                        "max_uses": 2,
                        "payload_mode": "any",
                    },
                    "max_ttl_seconds": 300,
                    "max_uses": 5,
                    "require_exact_payload": True,
                },
                "get_agent_status": {"denied": True},
            },
        },
    )
    auth.mark_privileged_for_request(update_request)
    update_response = asyncio.run(update_mcp_tools(update_request))

    assert update_response.status_code == 200
    payload = json.loads(update_response.body)
    by_tool = {item["value"]: item for item in payload["items"]}
    assert by_tool["run_script"]["approval_required"] is True
    assert by_tool["run_script"]["effective_settings"] == {
        "ttl_seconds": 300,
        "max_uses": 2,
        "payload_mode": "exact",
    }
    assert by_tool["get_agent_status"]["denied"] is True
    stored = service.read_directory_domain_config("root", "policy")
    assert stored.ok
    assert stored.data is not None
    assert stored.data.config["mcp_tool_policies"]["get_agent_status"]["denied"] is True


def test_admin_web_exposes_container_session_lifecycle_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(admin_service, "_utc_now", lambda: now)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=now + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
                "worker_url": "http://worker.test",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="container-session",
            worker_job_id="job-1",
            worker_url="http://worker.test",
            target_name="container-target",
            started_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=4),
            last_status="exited",
            directory_path="root",
        )
    )

    class FakeContainerBackend:
        def delete_session(self, session_id: str) -> dict[str, object]:
            attempted_at = now
            completed_at = now
            for record in repository.list_by_session(session_id):
                record.last_status = "removed"
                record.cleanup_status = "cleanup_succeeded"
                record.cleanup_attempted_at = attempted_at
                record.cleanup_completed_at = completed_at
                record.cleanup_error = None
                record.metadata = {
                    **record.metadata,
                    "worker_status": "removed",
                    "cleanup_status": "cleanup_succeeded",
                    "cleanup_attempted_at": attempted_at.isoformat(),
                    "cleanup_completed_at": completed_at.isoformat(),
                }
                repository.put(record)
            return {"deleted": True, "session_id": session_id}

    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: FakeContainerBackend(), service.vm_agent_admin),
    )
    client = TestClient(create_app(service))

    list_response = client.get("/api/admin/sessions")
    detail_response = client.get("/api/admin/sessions/container-session")
    cleanup_response = client.post("/api/admin/sessions/container-session/cleanup")
    refreshed_detail = client.get("/api/admin/sessions/container-session")

    assert list_response.status_code == 200
    list_item = list_response.json()["items"][0]
    assert list_item["container_lifecycle"]["state"] == "stopped"
    assert list_item["container_lifecycle"]["actions"]["cleanup"]["supported"] is True
    assert list_item["container_lifecycle"]["last_signal_at"] == "2026-05-21T11:55:00Z"
    assert detail_response.status_code == 200
    assert detail_response.json()["session"]["container_lifecycle"]["state"] == "stopped"
    assert detail_response.json()["session"]["container_lifecycle"]["actions"]["cleanup"][
        "supported"
    ] is True
    assert cleanup_response.status_code == 200
    cleanup_payload = cleanup_response.json()
    assert cleanup_payload["action"] == "cleanup_container_session"
    assert cleanup_payload["message"] == "Cleaned up container worker session container-session"
    assert cleanup_payload["session"]["container_lifecycle"]["state"] == "cleaned_up"
    assert cleanup_payload["session"]["container_lifecycle"]["actions"]["cleanup"][
        "supported"
    ] is False
    assert refreshed_detail.json()["session"]["container_lifecycle"]["state"] == "cleaned_up"
    assert service.runtime.session_repository.get_session("container-session") is not None


def test_admin_web_cleanup_container_session_surfaces_failure_without_deleting_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(admin_service, "_utc_now", lambda: now)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=now + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
                "worker_url": "http://worker.test",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="container-session",
            worker_job_id="job-1",
            worker_url="http://worker.test",
            target_name="container-target",
            started_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=4),
            last_status="exited",
            directory_path="root",
        )
    )

    class FakeContainerBackend:
        def delete_session(self, session_id: str) -> dict[str, object]:
            attempted_at = now
            completed_at = now
            for record in repository.list_by_session(session_id):
                record.last_status = "cleanup_failed"
                record.cleanup_status = "cleanup_failed"
                record.cleanup_attempted_at = attempted_at
                record.cleanup_completed_at = completed_at
                record.cleanup_error = (
                    f"container worker DELETE /v1/sessions/{session_id} failed: cleanup exploded"
                )
                record.metadata = {
                    **record.metadata,
                    "worker_status": "cleanup_failed",
                    "cleanup_status": "cleanup_failed",
                    "cleanup_attempted_at": attempted_at.isoformat(),
                    "cleanup_completed_at": completed_at.isoformat(),
                    "cleanup_error": record.cleanup_error,
                }
                repository.put(record)
            raise ContainerWorkerClientError(
                f"container worker DELETE /v1/sessions/{session_id} failed: cleanup exploded"
            )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: FakeContainerBackend(), service.vm_agent_admin),
    )
    client = TestClient(create_app(service))

    cleanup_response = client.post("/api/admin/sessions/container-session/cleanup")
    detail_response = client.get("/api/admin/sessions/container-session")

    assert cleanup_response.status_code == 502
    cleanup_payload = cleanup_response.json()
    assert cleanup_payload["success"] is False
    assert cleanup_payload["action"] == "cleanup_container_session"
    assert cleanup_payload["error"] == cleanup_payload["message"]
    assert "cleanup exploded" in cleanup_payload["message"]
    assert cleanup_payload["session"]["container_lifecycle"]["state"] == "cleanup_failed"
    assert cleanup_payload["session"]["container_lifecycle"]["actions"]["cleanup"][
        "supported"
    ] is True
    assert cleanup_payload["session"]["container_lifecycle"]["cleanup_error"].endswith(
        "cleanup exploded"
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["session"]["container_lifecycle"]["state"] == "cleanup_failed"
    assert service.runtime.session_repository.get_session("container-session") is not None


def test_admin_web_patches_session_policy_fields(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.write_directory_domain_config(
        "root",
        "contacts",
        {
            "contacts": {
                "ops": {
                    "name": "Ops",
                    "phone_numbers": [
                        {"label": "main", "number": "+15550001", "primary": True}
                    ],
                },
                "support": {
                    "name": "Support",
                    "phone_numbers": [
                        {"label": "main", "number": "+15550002", "primary": True}
                    ],
                },
            }
        },
    )
    communication_targets = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(service.config.runtime_dir)
    )
    communication_targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "ops-approvals",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-1",
                    "policy": {"capabilities": ["notify"]},
                }
            ]
        },
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14085-session",
            vm_id="vm-14085",
            allowed_tools=[ToolName.APPROVAL_CHECK, ToolName.RUN_SCRIPT],
            allowed_script_names=["old-script"],
            discord_channel_allowlist=["old-discord"],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            metadata={"agent_thread_id": "old-thread", "keep": "value"},
        ),
    )
    client = TestClient(create_app(service))

    response = client.patch(
        "/api/admin/sessions/issue-14085-session",
        json={
            "allowed_script_names": ["seed-demo", " seed-demo ", "collect-logs"],
            "allowed_contact_ids": ["ops", " support ", "ops"],
            "discord_channel_allowlist": ["chan-a", "chan-b"],
            "teams_channel_allowlist": ["team-b/channel-2", "chat:chat-1"],
            "tool_argument_policies": {
                "create_ado_backlog_item": [
                    {
                        "id": "issue-policy",
                        "constraints": [
                            {
                                "id": "title",
                                "path": "title",
                                "operator": "required",
                            },
                            {
                                "id": "project",
                                "path": "project",
                                "operator": "fixed",
                                "value": "Example Project",
                            },
                        ],
                    }
                ]
            },
            "communication_file_receive_enabled": True,
            "communication_file_send_enabled": True,
            "communication_file_max_bytes": 4096,
            "communication_file_allowed_mime_types": ["application/pdf"],
            "communication_file_allowed_extensions": [".pdf"],
            "communication_file_blocked_extensions": [".exe"],
            "communication_file_require_approval_for_send": True,
            "approval_required_tools": ["run_script"],
            "approval_tool_settings": {
                "run_script": {
                    "ttl_seconds": 120,
                    "max_uses": None,
                    "payload_mode": "any",
                }
            },
            "approval_notification_target_ids": [" OPS-Approvals ", "ops-approvals"],
            "metadata": {"keep": "new-value"},
        },
    )

    assert response.status_code == 200
    assert response.json()["action"] == "update_session_policy"
    assert response.json()["session"]["allowed_script_names"] == [
        "seed-demo",
        "collect-logs",
    ]
    assert response.json()["session"]["allowed_contact_ids"] == ["ops", "support"]
    assert response.json()["session"]["discord_channel_allowlist"] == ["chan-a", "chan-b"]
    assert response.json()["session"]["teams_channel_allowlist"] == [
        "team-b/channel-2",
        "chat:chat-1",
    ]
    assert response.json()["session"]["effective_tool_policy"]["tool_argument_policies"][
        "create_ado_backlog_item"
    ][0]["id"] == "issue-policy"
    assert response.json()["session"]["tool_summary"]["approval_required_tools"] == ["run_script"]
    assert response.json()["session"]["approval_required_tools"] == ["run_script"]
    assert response.json()["session"]["approval_notification_target_ids"] == ["ops-approvals"]
    options = response.json()["session"]["approval_notification_target_options"]
    assert options[0]["value"] == "ops-approvals"
    assert options[0]["label"].startswith("ops-approvals (teams")
    assert options[0]["metadata"]["provider"] == "teams"
    assert options[0]["metadata"]["policy_capabilities"] == ["notify"]
    assert response.json()["session"]["approval_tool_settings"]["run_script"] == {
        "ttl_seconds": 120,
        "max_uses": None,
        "payload_mode": "any",
    }
    assert response.json()["session"]["effective_tool_policy"]["approval_tool_settings"][
        "run_script"
    ] == {
        "ttl_seconds": 120,
        "max_uses": None,
        "payload_mode": "any",
    }
    updated = service.runtime.session_repository.get_session("issue-14085-session")
    assert updated is not None
    assert updated.tool_argument_policies["create_ado_backlog_item"][0].id == "issue-policy"
    assert updated.approval_required_tools == [ToolName.RUN_SCRIPT]
    assert updated.approval_notification_target_ids == ["ops-approvals"]
    assert updated.approval_tool_settings["run_script"].ttl_seconds == 120
    assert updated.approval_tool_settings["run_script"].max_uses is None
    assert updated.communication_file_receive_enabled is True
    assert updated.communication_file_send_enabled is True
    assert updated.communication_file_max_bytes == 4096
    assert updated.communication_file_allowed_mime_types == ["application/pdf"]
    assert updated.communication_file_allowed_extensions == [".pdf"]
    assert updated.communication_file_blocked_extensions == [".exe"]
    assert updated.communication_file_require_approval_for_send is True
    assert updated.metadata == {"keep": "new-value"}


def test_admin_web_rejects_direct_delete_for_agent_session(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14085-session",
            vm_id="vm-14085",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    client = TestClient(create_app(service))

    response = client.delete("/api/admin/sessions/issue-14085-session")

    assert response.status_code == 400
    assert "Delete the agent instead" in response.json()["error"]
    assert service.runtime.session_repository.get_session("issue-14085-session") is not None


def test_admin_web_rejects_blank_session_model(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14085-session",
            vm_id="vm-14085",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    client = TestClient(create_app(service))

    response = client.put(
        "/api/admin/sessions/issue-14085-session/model",
        json={"model": ""},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "Model is required."


def test_admin_web_exposes_wake_schedule_crud(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14086-session",
            vm_id="vm-14086",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    client = TestClient(create_app(service))

    create_response = client.post(
        "/api/admin/sessions/issue-14086-session/wake-schedules",
        json={
            "prompt": "Resume work",
            "cron": "*/15 * * * *",
            "resume_mode": "same_context",
            "enabled": True,
        },
    )
    request_id = create_response.json()["item"]["request_id"]
    list_response = client.get("/api/admin/sessions/issue-14086-session/wake-schedules")
    update_response = client.patch(
        f"/api/admin/sessions/wake-schedules/{request_id}",
        json={"prompt": "Resume carefully", "enabled": False},
    )
    update_resume_response = client.patch(
        f"/api/admin/sessions/wake-schedules/{request_id}",
        json={"resume_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(), "cron": None},
    )
    delete_response = client.delete(f"/api/admin/sessions/wake-schedules/{request_id}")

    assert create_response.status_code == 200
    assert create_response.json()["item"]["schedule_kind"] == "cron"
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["request_id"] == request_id
    assert update_response.status_code == 200
    assert update_response.json()["item"]["enabled"] is False
    assert update_resume_response.status_code == 200
    assert update_resume_response.json()["item"]["schedule_kind"] == "resume_at"
    assert delete_response.status_code == 200
    assert delete_response.json()["ok"] is True


def test_admin_web_lists_and_retries_scheduled_wakes(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14086-session",
            vm_id="vm-14086",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    failed = service.create_wake_schedule(
        session_id="issue-14086-session",
        prompt="Resume work",
        resume_at=datetime.now(UTC) + timedelta(minutes=10),
        resume_mode="same_context",
        enabled=True,
        source="admin_web",
    )
    service.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-failed",
            request_id=failed.request_id,
            session_id="issue-14086-session",
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work"),
            dispatched_at=datetime.now(UTC),
            error="stack trace\nsecret token",
        )
    )
    completed = service.create_wake_schedule(
        session_id="issue-14086-session",
        prompt="Already finished",
        resume_at=datetime.now(UTC) + timedelta(minutes=20),
        resume_mode="same_context",
        enabled=True,
        source="admin_web",
    )
    service.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-completed",
            request_id=completed.request_id,
            session_id="issue-14086-session",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Already finished"),
            dispatched_at=datetime.now(UTC),
        )
    )
    service.runtime.wake_store.mark_completed_by_dispatch_id(
        "dispatch-completed",
        completed_at=datetime.now(UTC),
        evidence={"dispatch_id": "dispatch-completed", "session_id": "issue-14086-session"},
    )
    client = TestClient(create_app(service))

    list_response = client.get(
        "/api/admin/scheduled-wakes",
        params={
            "session_id": "issue-14086-session",
            "statuses": "failed,completed_or_acknowledged",
            "include_completed": "false",
        },
    )
    retry_response = client.post(
        f"/api/admin/scheduled-wakes/{failed.request_id}/retry",
        json={"run_now": True},
    )

    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert [item["request_id"] for item in list_payload["items"]] == [failed.request_id]
    item = list_payload["items"][0]
    assert item["prompt_summary"] == "Resume work"
    assert item["redacted_prompt"] == "[redacted]"
    assert item["failure_reason"] is not None
    assert "\n" not in item["failure_reason"]
    assert retry_response.status_code == 200
    retry_item = retry_response.json()["item"]
    assert retry_item["request_id"] == failed.request_id
    assert retry_item["enabled"] is True
    assert retry_item["latest_dispatch"]["status"] == "failed"
    assert retry_item["failure_reason"] is None
    assert retry_item["last_error"] is None


def test_admin_web_lists_dispatched_wakes_with_confirmation_state(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14086-session",
            vm_id="vm-14086",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    dispatched = service.create_wake_schedule(
        session_id="issue-14086-session",
        prompt="Still waiting",
        resume_at=datetime.now(UTC) + timedelta(minutes=20),
        resume_mode="same_context",
        enabled=True,
        source="admin_web",
    )
    service.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-dispatched",
            request_id=dispatched.request_id,
            session_id="issue-14086-session",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Still waiting"),
            dispatched_at=datetime.now(UTC),
        )
    )
    client = TestClient(create_app(service))

    response = client.get(
        "/api/admin/scheduled-wakes",
        params={
            "session_id": "issue-14086-session",
            "statuses": "dispatched",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["request_id"] for item in payload["items"]] == [dispatched.request_id]
    item = payload["items"][0]
    assert item["status"] == "dispatched"
    assert item["confirmation_state"] == "awaiting_confirmation"
    assert item["completed_at"] is None


def test_admin_web_rejects_retry_for_completed_wake(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14086-session",
            vm_id="vm-14086",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    completed = service.create_wake_schedule(
        session_id="issue-14086-session",
        prompt="Already finished",
        resume_at=datetime.now(UTC) + timedelta(minutes=20),
        resume_mode="same_context",
        enabled=True,
        source="admin_web",
    )
    service.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-completed",
            request_id=completed.request_id,
            session_id="issue-14086-session",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Already finished"),
            dispatched_at=datetime.now(UTC),
        )
    )
    service.runtime.wake_store.mark_completed_by_dispatch_id(
        "dispatch-completed",
        completed_at=datetime.now(UTC),
        evidence={"dispatch_id": "dispatch-completed", "session_id": "issue-14086-session"},
    )
    client = TestClient(create_app(service))

    response = client.post(
        f"/api/admin/scheduled-wakes/{completed.request_id}/retry",
        json={"run_now": True},
    )

    assert response.status_code == 409
    assert "not retryable" in response.json()["error"]


def test_admin_web_reschedules_failed_wake_with_resume_at(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14086-session",
            vm_id="vm-14086",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    failed = service.create_wake_schedule(
        session_id="issue-14086-session",
        prompt="Resume work",
        resume_at=datetime.now(UTC) + timedelta(minutes=10),
        resume_mode="same_context",
        enabled=True,
        source="admin_web",
    )
    service.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-failed-reschedule",
            request_id=failed.request_id,
            session_id="issue-14086-session",
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work"),
            dispatched_at=datetime.now(UTC),
            error="resume failed secret=abc123",
        )
    )
    retry_at = datetime.now(UTC) + timedelta(hours=1)
    client = TestClient(create_app(service))

    response = client.post(
        f"/api/admin/scheduled-wakes/{failed.request_id}/retry",
        json={"resume_at": retry_at.isoformat()},
    )

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["request_id"] == failed.request_id
    assert item["status"] == "pending"
    assert item["resume_at"] == retry_at.isoformat().replace("+00:00", "Z")
    assert item["next_run_at"] == retry_at.isoformat().replace("+00:00", "Z")
    assert item["failure_reason"] is None


def test_admin_web_session_detail_preserves_cron_wake_schedule_kind(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14086-session",
            vm_id="vm-14086",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    service.create_wake_schedule(
        session_id="issue-14086-session",
        prompt="Resume work",
        cron="*/30 * * * *",
        resume_mode="same_context",
        enabled=True,
        source="admin_web",
    )
    client = TestClient(create_app(service))

    response = client.get("/api/admin/sessions/issue-14086-session")

    assert response.status_code == 200
    item = response.json()["wake_schedules"][0]
    assert item["cron"] == "*/30 * * * *"
    assert item["resume_at"] is None
    assert item["schedule_kind"] == "cron"


def test_admin_web_session_detail_exposes_cancelled_wake_history(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14086-session",
            vm_id="vm-14086",
            allowed_tools=[ToolName.SCHEDULE_RESUME, ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    created = service.create_wake_schedule(
        session_id="issue-14086-session",
        prompt="Resume work later",
        resume_at=datetime.now(UTC) + timedelta(hours=1),
        resume_mode="same_context",
        enabled=True,
        source="admin_web",
    )
    service.runtime.wake_store.cancel_request(
        created.request_id,
        caller_session_id="issue-14086-session",
        reason="No longer needed",
        metadata={"source": "ui"},
    )
    client = TestClient(create_app(service))

    response = client.get("/api/admin/sessions/issue-14086-session")

    assert response.status_code == 200
    item = response.json()["wake_schedules"][0]
    assert item["status"] == "cancelled"
    assert item["cancelled_by_session_id"] == "issue-14086-session"
    assert item["cancel_reason"] == "No longer needed"
    assert item["cancel_metadata"] == {"source": "ui"}
    assert item["schedule_kind"] == "resume_at"


def test_admin_web_rejects_cancelled_wake_schedule_reenable(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14086-session",
            vm_id="vm-14086",
            allowed_tools=[ToolName.SCHEDULE_RESUME, ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    created = service.create_wake_schedule(
        session_id="issue-14086-session",
        prompt="Resume work later",
        resume_at=datetime.now(UTC) + timedelta(hours=1),
        resume_mode="same_context",
        enabled=True,
        source="admin_web",
    )
    service.runtime.wake_store.cancel_request(
        created.request_id,
        caller_session_id="issue-14086-session",
        reason="No longer needed",
        metadata={"source": "ui"},
    )
    update_endpoint = next(
        route.endpoint
        for route in session_approval_routes(service, object())
        if getattr(route, "path", "") == "/api/admin/sessions/wake-schedules/{request_id:str}"
        and "PATCH" in getattr(route, "methods", set())
    )

    response = asyncio.run(
        update_endpoint(
            _admin_json_request(
                method="PATCH",
                path=f"/api/admin/sessions/wake-schedules/{created.request_id}",
                path_params={"request_id": created.request_id},
                payload={"enabled": True},
            )
        )
    )

    assert response.status_code == 400
    payload = json.loads(response.body)
    assert "cancelled and cannot be edited" in payload["error"]
    refreshed = service.runtime.wake_store.get_request(created.request_id)
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    assert refreshed.enabled is False
    assert refreshed.next_run_at is None


def test_admin_web_rejects_invalid_tool_argument_policy_updates(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14087-session",
            vm_id="vm-14087",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    client = TestClient(create_app(service))

    invalid_payload = {
        "tool_argument_policies": {
            "create_ado_backlog_item": [
                {
                    "id": "bad-policy",
                    "constraints": [
                        {
                            "id": "project",
                            "path": "project",
                            "operator": "bogus",
                        }
                    ],
                }
            ]
        }
    }

    session_response = client.patch(
        "/api/admin/sessions/issue-14087-session",
        json=invalid_payload,
    )
    agent_response = client.patch(
        "/api/admin/agents/issue-14087-session?backend=libvirt_vm",
        json=invalid_payload,
    )

    assert session_response.status_code == 400
    assert "operator" in session_response.json()["error"]
    assert agent_response.status_code == 400
    assert "operator" in agent_response.json()["error"]
