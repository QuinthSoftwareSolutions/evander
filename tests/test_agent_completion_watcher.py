from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import agent_sandbox.agent_completion_watcher as watcher
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.models import VmSummary


def test_build_agent_run_completed_event_uses_dispatch_scope() -> None:
    summary = VmSummary(
        vm_name="vm-issue-14724",
        session_ids=["session-14724"],
        labels={"vm-type": "issue"},
        directory_paths=["root/example-industry/app"],
        libvirt_state="running",
        ip_address="192.0.2.10",
        agent_state="idle",
        agent_repo_names=["ExampleIndustryAPP"],
        last_state_update_at=datetime(2026, 4, 7, 14, 0, 50, tzinfo=UTC),
        dispatch_id="dispatch-123",
    )

    event = watcher.build_agent_run_completed_event(
        trigger_id="__await-agent-completion__",
        dispatch_id="dispatch-123",
        vm_user="agent",
        summary=summary,
        session_id="session-14724",
        directory_path="root/example-industry/app",
    )

    assert isinstance(event, TriggerEvent)
    assert event.trigger_type == "trigger:agent:run:completed"
    assert event.payload["dispatch_id"] == "dispatch-123"
    assert event.payload["previous_agent_state"] == "running"
    assert event.payload["transition"] == {"from": "running", "to": "idle"}
    assert event.payload["scope"] == {
        "directory_path": "root/example-industry/app",
        "vm_name": "vm-issue-14724",
        "session_id": "session-14724",
        "vm_user": "agent",
    }


def test_await_agent_completion_publishes_when_dispatch_settles(monkeypatch, tmp_path: Path) -> None:
    responses = [
        [
            VmSummary(
                vm_name="vm-issue-14724",
                session_ids=["session-14724"],
                labels={"vm-type": "issue"},
                directory_paths=["root/example-industry/app"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="running",
                agent_repo_names=["ExampleIndustryAPP"],
                last_state_update_at=datetime(2026, 4, 7, 14, 0, 0, tzinfo=UTC),
                dispatch_id="dispatch-123",
            )
        ],
        [
            VmSummary(
                vm_name="vm-issue-14724",
                session_ids=["session-14724"],
                labels={"vm-type": "issue"},
                directory_paths=["root/example-industry/app"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="idle",
                agent_repo_names=[],
                last_state_update_at=datetime(2026, 4, 7, 14, 0, 50, tzinfo=UTC),
                dispatch_id="dispatch-123",
            )
        ],
    ]
    published: dict[str, object] = {}

    class _AdminServiceStub:
        def __init__(self, runtime_dir: Path) -> None:
            self.runtime_dir = runtime_dir

        def list_vms(self, **kwargs):
            return responses.pop(0)

    class _QueueStub:
        def __init__(self, path: Path) -> None:
            self.path = path

        def publish(self, event: TriggerEvent, *, idempotency_key: str | None = None):
            published["event"] = event
            published["idempotency_key"] = idempotency_key
            return None

    monkeypatch.setattr(watcher, "AdminService", _AdminServiceStub)
    monkeypatch.setattr(watcher, "TriggerEventQueue", _QueueStub)
    monkeypatch.setattr(watcher.time, "sleep", lambda _: None)

    result = watcher.await_agent_completion_and_publish(
        runtime_dir=tmp_path / "runtime",
        vm_name="vm-issue-14724",
        dispatch_id="dispatch-123",
        vm_user="agent",
        session_id="session-14724",
        directory_path="root/example-industry/app",
        poll_interval_seconds=0.01,
        timeout_seconds=1.0,
    )

    assert result is True
    assert published["idempotency_key"] == "__await-agent-completion__:dispatch-123"
    event = published["event"]
    assert isinstance(event, TriggerEvent)
    assert event.payload["agent_state"] == "idle"
    assert event.payload["dispatch_id"] == "dispatch-123"


def test_await_agent_completion_uses_configured_event_queue_path(monkeypatch, tmp_path: Path) -> None:
    published: dict[str, object] = {}
    queue_path = tmp_path / "runtime" / "custom-events.json"

    class _AdminServiceStub:
        def __init__(self, runtime_dir: Path) -> None:
            self.runtime_dir = runtime_dir

        def list_vms(self, **kwargs):
            return [
                VmSummary(
                    vm_name="vm-issue-14724",
                    session_ids=["session-14724"],
                    labels={"vm-type": "issue"},
                    directory_paths=["root/example-industry/app"],
                    libvirt_state="running",
                    ip_address="192.0.2.10",
                    agent_state="idle",
                    agent_repo_names=[],
                    last_state_update_at=datetime(2026, 4, 7, 14, 0, 50, tzinfo=UTC),
                    dispatch_id="dispatch-123",
                )
            ]

    class _QueueStub:
        def __init__(self, path: Path) -> None:
            published["path"] = path

        def publish(self, event: TriggerEvent, *, idempotency_key: str | None = None):
            published["event"] = event
            published["idempotency_key"] = idempotency_key
            return None

    monkeypatch.setattr(watcher, "AdminService", _AdminServiceStub)
    monkeypatch.setattr(watcher, "TriggerEventQueue", _QueueStub)

    result = watcher.await_agent_completion_and_publish(
        runtime_dir=tmp_path / "runtime",
        vm_name="vm-issue-14724",
        dispatch_id="dispatch-123",
        event_queue_store_path=queue_path,
        poll_interval_seconds=0.01,
        timeout_seconds=1.0,
    )

    assert result is True
    assert published["path"] == queue_path
