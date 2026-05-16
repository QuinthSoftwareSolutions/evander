from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryStore
from agent_sandbox.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.models import (
    ApprovalNotificationDeliveryStatus,
    ApprovalNotificationStatus,
    ApprovalStatus,
    SessionManifest,
    ToolName,
)
from agent_sandbox.runtime import build_runtime


def _write_root_policy_overlay(config: GatewayConfig, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root",
        domain="policy",
        payload=payload,
    )


def _write_targets(project_dir: Path, targets: list[dict[str, object]]) -> None:
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(project_dir / "runtime")
    )
    repository.write_overlay("root", {"targets": targets})


def _runtime(project_dir: Path, *, approval_notification_target_ids: list[str]) -> object:
    config = GatewayConfig(
        runtime_dir=project_dir / "runtime",
        audit_log_path=project_dir / "runtime" / "audit.jsonl",
        session_store_path=project_dir / "runtime" / "sessions.json",
        approval_store_path=project_dir / "runtime" / "approvals.json",
        draft_store_path=project_dir / "runtime" / "drafts.json",
        email_store_path=project_dir / "runtime" / "emails.json",
        discord_command_store_path=project_dir / "runtime" / "discord_commands.json",
    )
    _write_root_policy_overlay(
        config,
        {"approval_notification_target_ids": approval_notification_target_ids},
    )
    return build_runtime(config)


def _session_manifest(session_id: str = "session-1") -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        allowed_tools=[ToolName.APPROVAL_SUBMIT],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_approval_notifications_are_disabled_when_no_targets_are_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = _runtime(tmp_path, approval_notification_target_ids=[])
    runtime.session_repository.upsert_session(_session_manifest())

    with patch("agent_sandbox.approval_notifications.NotificationSender.send") as send_mock:
        approval = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"body": "secret body", "token": "secret-token"},
            "send the email",
        )

    assert approval.notification_status == ApprovalNotificationStatus.DISABLED
    assert approval.notification_target_ids == []
    assert approval.notification_attempted_at is None
    assert approval.notification_error is None
    assert approval.notification_deliveries == []
    send_mock.assert_not_called()


def test_approval_notifications_send_safe_message_to_configured_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "ops-approvals",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            }
        ],
    )
    runtime = _runtime(tmp_path, approval_notification_target_ids=["ops-approvals"])
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-42",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            discord_channel_allowlist=[],
            teams_channel_allowlist=[],
            teams_chat_allowlist=[],
            teams_user_allowlist=[],
            email_folder_allowlist=[],
            email_mailbox_allowlist=[],
            email_draft_recipient_allowlist=[],
            email_draft_domain_allowlist=[],
            email_send_enabled=False,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    calls: list[dict[str, object]] = []

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        calls.append(
            {
                "project_dir": self.project_dir,
                "directory_path": self.directory_path,
                **kwargs,
            }
        )
        return {
            "success": True,
            "provider": "teams",
            "communication_target_id": kwargs["communication_target_id"],
            "channel_target": "team-a/channel-1",
            "message_id": "msg-123",
            "status": "stubbed",
        }

    payload = {
        "body": "super secret body",
        "authorization": "Bearer secret",
        "nested": {"token": "nested-secret", "safe": "ok"},
    }
    with patch("agent_sandbox.approval_notifications.NotificationSender.send", new=fake_send):
        approval = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            payload,
            "send the email",
        )

    assert len(calls) == 1
    assert calls[0]["project_dir"] == tmp_path
    assert calls[0]["directory_path"] == "root"
    assert calls[0]["sender_session_id"] == f"approval:{approval.request_id}"
    assert calls[0]["communication_target_id"] == "ops-approvals"
    message = str(calls[0]["message"])
    assert f"request_id: {approval.request_id}" in message
    assert "action_type: email_send_approved" in message
    assert "tool_name: email_send_approved" in message
    assert "session_id: session-1" in message
    assert "directory_path: root" in message
    assert "vm_id: vm-42" in message
    assert "human_summary: send the email" in message
    assert f"payload_digest: {approval.payload_digest}" in message
    assert f"expires_at: {approval.expires_at.isoformat()}" in message
    assert "super secret body" not in message
    assert "Bearer secret" not in message
    assert "nested-secret" not in message
    assert "body:" not in message.lower()
    assert "token:" not in message.lower()
    assert "authorization:" not in message.lower()
    assert "agent-sandbox-cli show-approval" in message
    assert f"agent-sandbox-cli show-approval {approval.request_id}" in message
    assert (
        f"agent-sandbox-cli approve {approval.request_id} <approver> --approve|--deny --reason"
        in message
    )
    assert "payload:" not in message.lower()
    assert approval.notification_status == ApprovalNotificationStatus.DELIVERED
    assert approval.notification_target_ids == ["ops-approvals"]
    assert approval.notification_attempted_at is not None
    assert approval.notification_error is None
    assert len(approval.notification_deliveries) == 1
    assert approval.notification_deliveries[0].status == ApprovalNotificationDeliveryStatus.SENT
    assert approval.notification_deliveries[0].target_id == "ops-approvals"
    assert approval.notification_deliveries[0].provider == "teams"
    assert approval.notification_deliveries[0].message_id == "msg-123"
    assert approval.notification_deliveries[0].result_summary is not None


def test_approval_notifications_ignore_guest_allowlists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "ops-approvals",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            }
        ],
    )
    runtime = _runtime(tmp_path, approval_notification_target_ids=["ops-approvals"])
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-42",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            discord_channel_allowlist=[],
            teams_channel_allowlist=[],
            teams_chat_allowlist=[],
            teams_user_allowlist=[],
            email_folder_allowlist=[],
            email_mailbox_allowlist=[],
            email_draft_recipient_allowlist=[],
            email_draft_domain_allowlist=[],
            communication_file_send_enabled=False,
            communication_file_receive_enabled=False,
            email_send_enabled=False,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    send_calls: list[dict[str, object]] = []

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        send_calls.append(kwargs)
        return {"success": True, "provider": "teams", "communication_target_id": "ops-approvals"}

    with patch("agent_sandbox.approval_notifications.NotificationSender.send", new=fake_send):
        approval = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"body": "secret", "token": "secret-token"},
            "send email",
        )

    assert len(send_calls) == 1
    assert send_calls[0]["communication_target_id"] == "ops-approvals"
    assert approval.notification_status == ApprovalNotificationStatus.DELIVERED
    assert approval.notification_target_ids == ["ops-approvals"]


def test_duplicate_approval_submission_does_not_resend_notifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = _runtime(tmp_path, approval_notification_target_ids=["ops-approvals"])
    runtime.session_repository.upsert_session(_session_manifest())

    send_calls: list[dict[str, object]] = []

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        send_calls.append(kwargs)
        return {"success": True, "provider": "teams", "communication_target_id": "ops-approvals"}

    with patch("agent_sandbox.approval_notifications.NotificationSender.send", new=fake_send):
        first = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"subject": "hello", "body": "secret"},
            "send email",
        )
        second = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"subject": "hello", "body": "secret"},
            "send email",
        )

    assert first.request_id == second.request_id
    assert len(send_calls) == 1
    assert second.notification_status == ApprovalNotificationStatus.DELIVERED
    assert len(second.notification_deliveries) == 1


def test_approval_notification_failures_are_recorded_per_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = _runtime(
        tmp_path,
        approval_notification_target_ids=["ops-primary", "ops-backup"],
    )
    runtime.session_repository.upsert_session(_session_manifest())
    caplog.set_level("WARNING")

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        if kwargs["communication_target_id"] == "ops-primary":
            return {"success": True, "provider": "teams", "communication_target_id": "ops-primary"}
        raise RuntimeError("notification relay unavailable")

    with patch("agent_sandbox.approval_notifications.NotificationSender.send", new=fake_send):
        approval = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"body": "secret", "token": "secret-token"},
            "send email",
        )

    assert approval.status == ApprovalStatus.PENDING
    assert approval.notification_status == ApprovalNotificationStatus.PARTIAL
    assert approval.notification_target_ids == ["ops-primary", "ops-backup"]
    assert approval.notification_error is not None
    assert "notification relay unavailable" in approval.notification_error
    assert len(approval.notification_deliveries) == 2
    assert approval.notification_deliveries[0].status == ApprovalNotificationDeliveryStatus.SENT
    assert approval.notification_deliveries[1].status == ApprovalNotificationDeliveryStatus.FAILED
    assert approval.notification_deliveries[1].error == "notification relay unavailable"
    assert "metric_approval_notification outcome=failed" in caplog.text
    assert f"agent-sandbox-cli show-approval {approval.request_id}" in caplog.text
