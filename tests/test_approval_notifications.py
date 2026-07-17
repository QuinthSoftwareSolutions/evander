from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from evander.config import GatewayConfig
from evander.approval_notifications import ApprovalNotificationDispatcher
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryStore
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.models import (
    ApprovalNotificationDeliveryStatus,
    ApprovalNotificationStatus,
    ApprovalRequest,
    ApprovalStatus,
    SessionManifest,
    ToolName,
)
from evander.runtime import build_runtime


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

    with patch("evander.approval_notifications.NotificationSender.send") as send_mock:
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
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PUBLIC_BASE_URL", "https://admin.example.com")
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
    with patch("evander.approval_notifications.NotificationSender.send", new=fake_send):
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
    assert "Admin Web:" in message
    assert "[Open approval](https://admin.example.com/#approvals?request_id=" in message
    assert "decision=approve" in message
    assert "decision=deny" in message
    assert "CLI fallback:" in message
    assert "evander-cli show-approval" in message
    assert f"evander-cli show-approval {approval.request_id}" in message
    assert (
        f"evander-cli approve {approval.request_id} <approver> --approve|--deny --reason" in message
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


def test_approval_notifications_fallback_to_local_admin_web_url_when_public_base_url_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_PUBLIC_BASE_URL", raising=False)
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
    runtime.session_repository.upsert_session(_session_manifest())

    send_calls: list[dict[str, object]] = []

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        send_calls.append(kwargs)
        return {"success": True, "provider": "teams", "communication_target_id": "ops-approvals"}

    with patch("evander.approval_notifications.NotificationSender.send", new=fake_send):
        approval = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"body": "secret body"},
            "send the email",
        )

    assert len(send_calls) == 1
    message = str(send_calls[0]["message"])
    assert "http://127.0.0.1:8100/#approvals?request_id=" in message
    assert f"http://127.0.0.1:8100/#approvals?request_id={approval.request_id}" in message
    assert "decision=approve" in message
    assert "decision=deny" in message


def test_approval_notifications_read_public_base_url_from_admin_web_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_PUBLIC_BASE_URL", raising=False)
    admin_env_path = tmp_path / "config" / "admin-web.env"
    admin_env_path.parent.mkdir(parents=True)
    admin_env_path.write_text(
        'EVANDER_ADMIN_WEB_PUBLIC_BASE_URL="https://admin.example.com/control/"\n',
        encoding="utf-8",
    )
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
    runtime.session_repository.upsert_session(_session_manifest())

    send_calls: list[dict[str, object]] = []

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        send_calls.append(kwargs)
        return {"success": True, "provider": "teams", "communication_target_id": "ops-approvals"}

    with patch("evander.approval_notifications.NotificationSender.send", new=fake_send):
        approval = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"body": "secret body"},
            "send the email",
        )

    assert len(send_calls) == 1
    message = str(send_calls[0]["message"])
    assert (
        f"https://admin.example.com/control/#approvals?request_id={approval.request_id}" in message
    )
    assert "http://127.0.0.1:8100" not in message
    assert "decision=approve" in message
    assert "decision=deny" in message


def test_approval_creation_survives_legacy_missing_notification_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = _runtime(tmp_path, approval_notification_target_ids=["missing-target"])
    runtime.session_repository.upsert_session(_session_manifest())

    approval = runtime.policy.create_approval(
        "session-1",
        "email_send_approved",
        {"body": "secret", "token": "secret-token"},
        "send email",
    )

    assert approval.status == ApprovalStatus.PENDING
    assert approval.notification_target_ids == ["missing-target"]
    assert approval.notification_status == ApprovalNotificationStatus.FAILED
    assert approval.notification_error == (
        "missing-target: unknown communication target: missing-target"
    )
    assert len(approval.notification_deliveries) == 1
    assert approval.notification_deliveries[0].target_id == "missing-target"
    assert approval.notification_deliveries[0].status == ApprovalNotificationDeliveryStatus.FAILED


def test_approval_notifications_ignore_guest_allowlists(
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

    with patch("evander.approval_notifications.NotificationSender.send", new=fake_send):
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


def test_approval_notification_dispatch_uses_persisted_target_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "ops-policy",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            },
            {
                "id": "ops-request",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-2",
                "policy": {"capabilities": ["notify"]},
            },
        ],
    )
    runtime = _runtime(tmp_path, approval_notification_target_ids=["ops-policy"])
    runtime.session_repository.upsert_session(_session_manifest())
    dispatcher = ApprovalNotificationDispatcher(runtime=runtime, project_dir=tmp_path)
    request = ApprovalRequest(
        request_id="apr-1",
        session_id="session-1",
        action_type="email_send_approved",
        tool_name="email_send_approved",
        payload_digest="digest",
        human_summary="send email",
        payload={"body": "<redacted>"},
        directory_path="root",
        notification_status=ApprovalNotificationStatus.QUEUED,
        notification_target_ids=["ops-request"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    send_calls: list[dict[str, object]] = []

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        send_calls.append(kwargs)
        return {
            "success": True,
            "provider": "teams",
            "communication_target_id": kwargs["communication_target_id"],
            "message_id": "msg-123",
            "status": "stubbed",
        }

    with (
        patch("evander.approval_notifications.NotificationSender.send", new=fake_send),
        patch.object(
            runtime.policy_config,
            "resolve_for_directory",
            side_effect=AssertionError("dispatcher should not resolve policy targets"),
        ),
    ):
        approval = dispatcher.dispatch(request)

    assert approval is not None
    assert [call["communication_target_id"] for call in send_calls] == ["ops-request"]
    assert approval.notification_target_ids == ["ops-request"]
    assert approval.notification_status == ApprovalNotificationStatus.DELIVERED


def test_approval_notification_dispatch_records_failure_for_persisted_target_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "stale-target",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            }
        ],
    )
    runtime = _runtime(tmp_path, approval_notification_target_ids=["stale-target"])
    runtime.session_repository.upsert_session(_session_manifest())
    dispatcher = ApprovalNotificationDispatcher(runtime=runtime, project_dir=tmp_path)
    request = ApprovalRequest(
        request_id="apr-2",
        session_id="session-1",
        action_type="email_send_approved",
        tool_name="email_send_approved",
        payload_digest="digest",
        human_summary="send email",
        payload={"body": "<redacted>"},
        directory_path="root",
        notification_status=ApprovalNotificationStatus.QUEUED,
        notification_target_ids=["stale-target"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        raise RuntimeError(f"unknown communication target: {kwargs['communication_target_id']}")

    with (
        patch("evander.approval_notifications.NotificationSender.send", new=fake_send),
        patch.object(
            runtime.policy_config,
            "resolve_for_directory",
            side_effect=AssertionError("dispatcher should not resolve policy targets"),
        ),
    ):
        approval = dispatcher.dispatch(request)

    assert approval.status == ApprovalStatus.PENDING
    assert approval.notification_status == ApprovalNotificationStatus.FAILED
    assert approval.notification_target_ids == ["stale-target"]
    assert approval.notification_error == "stale-target: unknown communication target: stale-target"
    assert len(approval.notification_deliveries) == 1
    assert approval.notification_deliveries[0].target_id == "stale-target"
    assert approval.notification_deliveries[0].status == ApprovalNotificationDeliveryStatus.FAILED


def test_duplicate_approval_submission_does_not_resend_notifications(
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
            },
            {
                "id": "ops-refresh",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-2",
                "policy": {"capabilities": ["notify"]},
            },
        ],
    )
    runtime = _runtime(tmp_path, approval_notification_target_ids=["ops-approvals"])
    runtime.session_repository.upsert_session(_session_manifest())

    send_calls: list[dict[str, object]] = []

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        send_calls.append(kwargs)
        return {"success": True, "provider": "teams", "communication_target_id": "ops-approvals"}

    with patch("evander.approval_notifications.NotificationSender.send", new=fake_send):
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


@pytest.mark.parametrize(
    ("stale_status", "expires_delta"),
    [
        (ApprovalStatus.DENIED, timedelta(hours=1)),
        (ApprovalStatus.PENDING, timedelta(seconds=-1)),
        (ApprovalStatus.EXPIRED, timedelta(hours=1)),
    ],
)
def test_stale_approval_submission_refreshes_and_resends_notifications(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stale_status: ApprovalStatus,
    expires_delta: timedelta,
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
            },
            {
                "id": "ops-refresh",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-2",
                "policy": {"capabilities": ["notify"]},
            },
        ],
    )
    runtime = _runtime(tmp_path, approval_notification_target_ids=["ops-approvals"])
    runtime.session_repository.upsert_session(_session_manifest())

    send_calls: list[dict[str, object]] = []

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        send_calls.append(kwargs)
        return {"success": True, "provider": "teams", "communication_target_id": "ops-approvals"}

    with patch("evander.approval_notifications.NotificationSender.send", new=fake_send):
        first = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"subject": "hello", "body": "secret"},
            "send email",
        )
        runtime.session_repository.upsert_session(
            SessionManifest(
                session_id="session-1",
                vm_id="vm-42",
                allowed_tools=[ToolName.APPROVAL_SUBMIT],
                approval_notification_target_ids=["ops-refresh"],
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
        first.status = stale_status
        first.expires_at = datetime.now(UTC) + expires_delta
        first.approver = "alice"
        first.decision_at = datetime.now(UTC)
        first.decision_reason = "unsafe"
        runtime.approvals.put(first.request_id, first)

        second = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"subject": "hello", "body": "secret"},
            "send email",
        )

    assert first.request_id == second.request_id
    assert len(send_calls) == 3
    assert second.status == ApprovalStatus.PENDING
    assert second.expires_at > datetime.now(UTC)
    assert second.approver is None
    assert second.decision_at is None
    assert second.decision_reason is None
    assert second.notification_status == ApprovalNotificationStatus.DELIVERED
    assert second.notification_target_ids == ["ops-approvals", "ops-refresh"]
    assert len(second.notification_deliveries) == 2


def test_approval_notification_failures_are_recorded_per_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "ops-primary",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            },
            {
                "id": "ops-backup",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-2",
                "policy": {"capabilities": ["notify"]},
            },
        ],
    )
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

    with patch("evander.approval_notifications.NotificationSender.send", new=fake_send):
        approval = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"body": "secret", "token": "secret-token"},
            "send email",
        )

    assert approval.status == ApprovalStatus.PENDING
    assert approval.notification_status == ApprovalNotificationStatus.PARTIAL
    assert approval.notification_target_ids == ["ops-primary", "ops-backup"]
    assert approval.notification_error == "ops-backup: notification relay unavailable"
    assert len(approval.notification_deliveries) == 2
    assert approval.notification_deliveries[0].status == ApprovalNotificationDeliveryStatus.SENT
    assert approval.notification_deliveries[1].status == ApprovalNotificationDeliveryStatus.FAILED
    assert approval.notification_deliveries[1].error == "notification relay unavailable"
    assert "metric_approval_notification outcome=failed" in caplog.text
    assert f"evander-cli show-approval {approval.request_id}" in caplog.text
