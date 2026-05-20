from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import create_app
from agent_sandbox.models import (
    ApprovalNotificationDeliveryRecord,
    ApprovalNotificationDeliveryStatus,
    ApprovalNotificationStatus,
    ApprovalRequest,
    SessionManifest,
    ToolName,
)


ROOT = Path(__file__).resolve().parents[1]


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
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_sessions_screen_groups_voice_calling_and_workspace_staging_tools() -> None:
    source = (
        ROOT / "frontend" / "workflow-editor" / "src" / "features" / "sessions" / "SessionsScreen.tsx"
    ).read_text(encoding="utf-8")

    assert '"Voice calling"' in source
    assert '"list_contacts", "make_phone_call", "get_phone_call_result"' in source
    assert '"communication_copy_artifact_to_workspace",\n    "communication_stage_workspace_file",' in source


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
    assert detail_payload["request"]["notification_status"] == "delivered"
    assert detail_payload["request"]["notification_target_ids"] == ["ops-approvals"]
    assert detail_payload["request"]["notification_deliveries"][0]["target_id"] == "ops-approvals"
    assert detail_payload["request"]["notification_deliveries"][0]["status"] == "sent"
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
    assert "List directory-configured contact ids" in tool_options["list_contacts"]["description"]
    assert "Start Retell phone calls" in tool_options["make_phone_call"]["description"]
    assert "Read Retell phone call status" in tool_options["get_phone_call_result"]["description"]
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
    assert model_response.status_code == 200
    assert model_response.json()["action"] == "set_session_model"
    assert model_response.json()["session"]["model"] == "gpt-5.5"
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


def test_admin_web_patches_session_policy_fields(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14085-session",
            vm_id="vm-14085",
            allowed_tools=[ToolName.APPROVAL_CHECK],
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
            "metadata": {"keep": "new-value"},
        },
    )

    assert response.status_code == 200
    assert response.json()["action"] == "update_session_policy"
    assert response.json()["session"]["allowed_script_names"] == [
        "seed-demo",
        "collect-logs",
    ]
    assert response.json()["session"]["discord_channel_allowlist"] == ["chan-a", "chan-b"]
    assert response.json()["session"]["teams_channel_allowlist"] == [
        "team-b/channel-2",
        "chat:chat-1",
    ]
    assert response.json()["session"]["effective_tool_policy"]["tool_argument_policies"][
        "create_ado_backlog_item"
    ][0]["id"] == "issue-policy"
    updated = service.runtime.session_repository.get_session("issue-14085-session")
    assert updated is not None
    assert updated.tool_argument_policies["create_ado_backlog_item"][0].id == "issue-policy"
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
