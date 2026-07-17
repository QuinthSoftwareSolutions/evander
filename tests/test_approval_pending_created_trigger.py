from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryStore
from evander.events.provider_models import ApprovalPendingCreatedTriggerDefinition
from evander.models import (
    ApprovalNotificationStatus,
    ApprovalRequest,
    ApprovalStatus,
    SessionManifest,
    ToolName,
)
from evander.runtime import build_runtime
from evander.storage import JsonStore
from evander.triggers.providers.approval_pending_created import (
    ApprovalPendingCreatedTriggerProvider,
)
from evander.triggers.providers.base import TriggerRuntimeContext


def _write_root_policy_overlay(config: GatewayConfig, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root",
        domain="policy",
        payload=payload,
    )


def _runtime(
    project_dir: Path,
    *,
    approval_notification_target_ids: list[str] | None = None,
    redacted_keys: list[str] | None = None,
) -> object:
    config = GatewayConfig(
        runtime_dir=project_dir / "runtime",
        audit_log_path=project_dir / "runtime" / "audit.jsonl",
        session_store_path=project_dir / "runtime" / "sessions.json",
        approval_store_path=project_dir / "runtime" / "approvals.json",
        draft_store_path=project_dir / "runtime" / "drafts.json",
        email_store_path=project_dir / "runtime" / "emails.json",
        discord_command_store_path=project_dir / "runtime" / "discord_commands.json",
    )
    policy_payload: dict[str, object] = {}
    if approval_notification_target_ids is not None:
        policy_payload["approval_notification_target_ids"] = approval_notification_target_ids
    if redacted_keys is not None:
        policy_payload["redacted_keys"] = redacted_keys
    if policy_payload:
        _write_root_policy_overlay(config, policy_payload)
    return build_runtime(config)


def _approval_store(runtime_dir: Path) -> JsonStore[ApprovalRequest]:
    return JsonStore(runtime_dir / "approvals.json", ApprovalRequest)


def _approval(
    request_id: str,
    *,
    created_at: datetime,
    directory_path: str = "root/team-a",
    action_type: str = "email_send_approved",
    tool_name: str | None = "approval_submit",
    session_id: str = "session-1",
    payload: dict[str, object] | None = None,
    payload_digest: str = "digest-1",
    human_summary: str = "send the email",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    notification_status: ApprovalNotificationStatus = ApprovalNotificationStatus.DISABLED,
    notification_target_ids: list[str] | None = None,
) -> ApprovalRequest:
    return ApprovalRequest.model_validate(
        {
            "request_id": request_id,
            "session_id": session_id,
            "action_type": action_type,
            "payload_digest": payload_digest,
            "human_summary": human_summary,
            "payload": payload or {"body": "hello"},
            "directory_path": directory_path,
            "tool_name": tool_name,
            "status": status,
            "notification_status": notification_status,
            "notification_target_ids": notification_target_ids or [],
            "created_at": created_at,
            "expires_at": created_at + timedelta(hours=1),
        }
    )


def _provider(
    runtime_dir: Path,
    *,
    trigger_id: str = "approval-trigger",
    directory_path: str = "root",
    action_types: list[str] | None = None,
    tool_names: list[str] | None = None,
) -> ApprovalPendingCreatedTriggerProvider:
    trigger = ApprovalPendingCreatedTriggerDefinition.model_validate(
        {
            "id": trigger_id,
            "display_name": "Pending approval",
            "type": "trigger:approval:pending-created",
            "directory_path": directory_path,
            "action_types": action_types or [],
            "tool_names": tool_names or [],
        }
    )
    provider = ApprovalPendingCreatedTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(
            resolved_config=GatewayConfig(
                runtime_dir=runtime_dir,
                approval_store_path=runtime_dir / "approvals.json",
            ),
            metadata={},
        ),
    )
    return provider


def _poll_and_ack(provider: ApprovalPendingCreatedTriggerProvider):
    events = provider.poll()
    for event in events:
        provider.acknowledge_published_event(event)
    return events


def test_pending_created_trigger_suppresses_initial_history(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = _approval_store(runtime_dir)
    store.put(
        "apr-old",
        _approval(
            "apr-old",
            created_at=datetime.now(UTC) - timedelta(hours=1),
        ),
    )
    provider = _provider(runtime_dir)

    assert provider.poll() == []
    assert store.get("apr-old") is not None
    assert store.get("apr-old").status == ApprovalStatus.PENDING


def test_pending_created_trigger_retries_until_acknowledged(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = _approval_store(runtime_dir)
    provider = _provider(runtime_dir)
    assert provider.poll() == []
    created_at = datetime.now(UTC) + timedelta(seconds=1)

    store.put(
        "apr-new",
        _approval(
            "apr-new",
            created_at=created_at,
            payload={"body": "hello", "token": "secret-token"},
        ),
    )

    first = provider.poll()
    second = provider.poll()

    assert len(first) == 1
    assert len(second) == 1
    assert [event.event_id for event in second] == [first[0].event_id]
    assert first[0].payload["approval_request_id"] == "apr-new"
    assert first[0].payload["approval_request"]["payload"]["token"] == "secret-token"

    provider.acknowledge_published_event(first[0])
    assert provider.poll() == []


def test_pending_created_trigger_respects_directory_scope(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = _approval_store(runtime_dir)
    provider = _provider(runtime_dir, directory_path="root/team-a")
    assert provider.poll() == []
    base_created_at = datetime.now(UTC) + timedelta(seconds=1)

    store.put(
        "apr-a",
        _approval(
            "apr-a",
            created_at=base_created_at,
            directory_path="root/team-a",
        ),
    )
    store.put(
        "apr-a-child",
        _approval(
            "apr-a-child",
            created_at=base_created_at + timedelta(seconds=1),
            directory_path="root/team-a/project",
        ),
    )
    store.put(
        "apr-b",
        _approval(
            "apr-b",
            created_at=base_created_at + timedelta(seconds=2),
            directory_path="root/team-b",
        ),
    )

    events = _poll_and_ack(provider)

    assert [event.event_id for event in events] == [
        f"approval-trigger:apr-a:{base_created_at.astimezone(UTC).isoformat()}",
        f"approval-trigger:apr-a-child:{(base_created_at + timedelta(seconds=1)).astimezone(UTC).isoformat()}",
    ]


def test_pending_created_trigger_respects_action_and_tool_filters(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = _approval_store(runtime_dir)
    provider = _provider(
        runtime_dir,
        action_types=["email_send_approved"],
        tool_names=["email_send_approved"],
    )
    assert provider.poll() == []
    base_created_at = datetime.now(UTC) + timedelta(seconds=1)

    store.put(
        "apr-match",
        _approval(
            "apr-match",
            created_at=base_created_at,
            action_type="email_send_approved",
            tool_name=None,
        ),
    )
    store.put(
        "apr-wrong-action",
        _approval(
            "apr-wrong-action",
            created_at=base_created_at + timedelta(seconds=1),
            action_type="email_send_denied",
            tool_name=None,
        ),
    )
    store.put(
        "apr-wrong-tool",
        _approval(
            "apr-wrong-tool",
            created_at=base_created_at + timedelta(seconds=2),
            action_type="email_send_approved",
            tool_name="different_tool",
        ),
    )

    events = _poll_and_ack(provider)

    assert [event.event_id for event in events] == [
        f"approval-trigger:apr-match:{base_created_at.astimezone(UTC).isoformat()}",
    ]
    assert events[0].payload["approval_request"]["tool_name"] is None
    assert store.get("apr-match").status == ApprovalStatus.PENDING
    assert store.get("apr-wrong-action").status == ApprovalStatus.PENDING
    assert store.get("apr-wrong-tool").status == ApprovalStatus.PENDING


def test_pending_created_trigger_redacts_payload_before_emitting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = _runtime(
        tmp_path,
        redacted_keys=["authorization", "body", "token"],
    )
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    provider = _provider(tmp_path / "runtime")
    assert provider.poll() == []

    approval = runtime.policy.create_approval(
        "session-1",
        "email_send_approved",
        {
            "body": "super secret body",
            "authorization": "Bearer secret",
            "nested": {"token": "nested-secret", "safe": "ok"},
        },
        "send the email",
    )

    events = provider.poll()

    assert len(events) == 1
    payload = events[0].payload
    assert payload["approval_request_id"] == approval.request_id
    assert payload["payload"]["body"] == "<redacted>"
    assert payload["payload"]["authorization"] == "<redacted>"
    assert payload["payload"]["nested"]["token"] == "<redacted>"
    assert payload["payload"]["nested"]["safe"] == "ok"
    serialized = json.dumps(payload, sort_keys=True)
    assert "super secret body" not in serialized
    assert "Bearer secret" not in serialized
    assert "nested-secret" not in serialized


def test_pending_created_trigger_emits_recreated_pending_request_with_new_identity(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime = _runtime(tmp_path)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    provider = _provider(runtime_dir)
    assert provider.poll() == []

    first = runtime.policy.create_approval(
        "session-1",
        "email_send_approved",
        {"body": "first"},
        "send the email",
    )
    first_event = provider.poll()
    assert [event.event_id for event in first_event] == [
        f"approval-trigger:{first.request_id}:{first.created_at.astimezone(UTC).isoformat()}"
    ]
    provider.acknowledge_published_event(first_event[0])

    stored = runtime.approvals.get(first.request_id)
    assert stored is not None
    stored.status = ApprovalStatus.DENIED
    runtime.approvals.put(stored.request_id, stored)

    recreated = runtime.policy.create_approval(
        "session-1",
        "email_send_approved",
        {"body": "first"},
        "send the email",
    )
    assert recreated.request_id == first.request_id
    assert recreated.created_at > first.created_at

    second_event = provider.poll()

    assert [event.event_id for event in second_event] == [
        f"approval-trigger:{recreated.request_id}:{recreated.created_at.astimezone(UTC).isoformat()}"
    ]
    assert second_event[0].event_id != first_event[0].event_id
    assert (
        second_event[0].payload["approval_request"]["created_at"]
        != first_event[0].payload["approval_request"]["created_at"]
    )


def test_pending_created_trigger_reports_notification_state_without_resending_notifications(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = _runtime(tmp_path, approval_notification_target_ids=["ops-approvals"])
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-42",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    provider = _provider(tmp_path / "runtime")
    assert provider.poll() == []

    calls: list[dict[str, object]] = []

    def fake_send(self, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "success": True,
            "provider": "teams",
            "communication_target_id": kwargs["communication_target_id"],
            "channel_target": "team-a/channel-1",
            "message_id": "msg-123",
            "status": "stubbed",
        }

    with patch("evander.approval_notifications.NotificationSender.send", new=fake_send):
        approval = runtime.policy.create_approval(
            "session-1",
            "email_send_approved",
            {"body": "secret body", "token": "secret-token"},
            "send the email",
        )

    assert len(calls) == 1
    assert approval.notification_status == ApprovalNotificationStatus.DELIVERED

    events = provider.poll()

    assert len(events) == 1
    payload = events[0].payload
    assert payload["approval_request_id"] == approval.request_id
    assert payload["notification_status"] == "delivered"
    assert payload["notification_target_ids"] == ["ops-approvals"]
    assert payload["notification_deliveries"][0]["target_id"] == "ops-approvals"
    assert payload["notification_deliveries"][0]["provider"] == "teams"
    assert payload["notification_deliveries"][0]["message_id"] == "msg-123"
    assert len(calls) == 1
