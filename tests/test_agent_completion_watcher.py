from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import evander.daemons.agent_completion_watcher as watcher
from evander.events.models import TriggerEvent
from evander.messaging.wake_store import WakeRequestStore
from evander.models import (
    VmSummary,
    WakeDispatchRecord,
    WakeResumePromptPayload,
    WakeScheduleDefinition,
)


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


def test_mark_wake_completed_from_event_prefers_wake_dispatch_id(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    wake_store = WakeRequestStore(runtime_dir / "wake-requests-store.json")
    completed_at = datetime.now(UTC).replace(microsecond=0)
    scheduled_for = completed_at - timedelta(minutes=5)
    wake_store.schedule_resume_request(
        session_id="session-14724",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=scheduled_for),
        request_id="wake-1",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="wake-1",
            request_id="wake-1",
            session_id="session-14724",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            resume_mode="same_context",
            scheduled_for=scheduled_for,
            dispatched_at=scheduled_for + timedelta(seconds=5),
            source="scheduled_once",
        )
    )
    summary = VmSummary(
        vm_name="vm-issue-14724",
        session_ids=["session-14724"],
        labels={"vm-type": "issue"},
        directory_paths=["root/example-industry/app"],
        libvirt_state="running",
        ip_address="192.0.2.10",
        agent_state="idle",
        agent_repo_names=["ExampleIndustryAPP"],
        last_state_update_at=completed_at,
        dispatch_id="effect-dispatch-123",
    )
    event = watcher.build_agent_run_completed_event(
        trigger_id="__await-agent-completion__",
        dispatch_id="effect-dispatch-123",
        wake_dispatch_id="wake-1",
        vm_user="agent",
        summary=summary,
        session_id="session-14724",
        directory_path="root/example-industry/app",
    )
    admin_service = SimpleNamespace(runtime=SimpleNamespace(wake_store=wake_store))

    watcher._mark_wake_completed_from_event(admin_service=admin_service, event=event)

    completed = wake_store.get_request("wake-1")
    assert completed is not None
    assert completed.completed_at == event.occurred_at
    assert completed.completion_evidence["dispatch_id"] == "wake-1"
    assert completed.completion_evidence["trigger_event_id"] == event.event_id
    assert completed.completion_evidence["trigger_type"] == "trigger:agent:run:completed"


def test_await_agent_completion_publishes_when_dispatch_settles(
    monkeypatch, tmp_path: Path
) -> None:
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
            self.vm_agent_admin = self

        def list_vms(self, **kwargs):
            return responses.pop(0)

    class _QueueStub:
        def __init__(self, path: Path) -> None:
            self.path = path

        def publish_result(self, event: TriggerEvent, *, idempotency_key: str | None = None):
            published["event"] = event
            published["idempotency_key"] = idempotency_key
            return SimpleNamespace(
                record=SimpleNamespace(event_id=event.event_id),
                created=True,
            )

    monkeypatch.setattr(
        watcher, "build_admin_runtime", lambda runtime_dir: _AdminServiceStub(runtime_dir)
    )
    monkeypatch.setattr(watcher, "TriggerEventQueue", _QueueStub)
    monkeypatch.setattr(watcher.time, "sleep", lambda _: None)
    wake_paths: list[Path] = []
    monkeypatch.setattr(
        watcher,
        "notify_wake_socket_for_lock",
        lambda lock_path: wake_paths.append(Path(lock_path)) or True,
    )

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
    assert wake_paths == [tmp_path / "runtime" / "effect-orchestrator.lock"]
    assert published["idempotency_key"] == "__await-agent-completion__:dispatch-123"
    event = published["event"]
    assert isinstance(event, TriggerEvent)
    assert event.payload["agent_state"] == "idle"
    assert event.payload["dispatch_id"] == "dispatch-123"


def test_await_agent_completion_marks_matching_wake_request_completed(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    wake_store = WakeRequestStore(runtime_dir / "wake-requests-store.json")
    completed_at = datetime.now(UTC).replace(microsecond=0)
    scheduled_for = completed_at - timedelta(minutes=5)
    wake_store.schedule_resume_request(
        session_id="session-14724",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=scheduled_for),
        request_id="wake-1",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-123",
            request_id="wake-1",
            session_id="session-14724",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            resume_mode="same_context",
            scheduled_for=scheduled_for,
            dispatched_at=scheduled_for + timedelta(seconds=5),
            source="scheduled_once",
        )
    )
    published: dict[str, object] = {}

    class _AdminServiceStub:
        def __init__(self, runtime_dir: Path) -> None:
            self.runtime_dir = runtime_dir
            self.vm_agent_admin = self
            self.runtime = SimpleNamespace(wake_store=wake_store)

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
                    last_state_update_at=completed_at,
                    dispatch_id="dispatch-123",
                )
            ]

    class _QueueStub:
        def __init__(self, path: Path) -> None:
            self.path = path

        def publish_result(self, event: TriggerEvent, *, idempotency_key: str | None = None):
            published["event"] = event
            published["idempotency_key"] = idempotency_key
            return SimpleNamespace(
                record=SimpleNamespace(event_id=event.event_id),
                created=True,
            )

    monkeypatch.setattr(
        watcher, "build_admin_runtime", lambda runtime_dir: _AdminServiceStub(runtime_dir)
    )
    monkeypatch.setattr(watcher, "TriggerEventQueue", _QueueStub)
    monkeypatch.setattr(watcher, "notify_wake_socket_for_lock", lambda lock_path: True)

    result = watcher.await_agent_completion_and_publish(
        runtime_dir=runtime_dir,
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
    completed = wake_store.get_request("wake-1")
    assert completed is not None
    assert completed.status == "completed_or_acknowledged"
    assert completed.completed_at == completed_at
    assert completed.last_dispatch_id == "dispatch-123"
    assert completed.last_dispatch_status == "dispatched"
    assert completed.completion_evidence["trigger_type"] == "trigger:agent:run:completed"
    assert completed.completion_evidence["dispatch_id"] == "dispatch-123"
    assert completed.completion_evidence["vm_name"] == "vm-issue-14724"
    assert completed.completion_evidence["session_id"] == "session-14724"


def test_await_agent_completion_uses_configured_event_queue_path(
    monkeypatch, tmp_path: Path
) -> None:
    published: dict[str, object] = {}
    queue_path = tmp_path / "runtime" / "custom-events.json"
    effect_lock_path = tmp_path / "locks" / "custom-effect.lock"

    class _AdminServiceStub:
        def __init__(self, runtime_dir: Path) -> None:
            self.runtime_dir = runtime_dir
            self.vm_agent_admin = self

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

        def publish_result(self, event: TriggerEvent, *, idempotency_key: str | None = None):
            published["event"] = event
            published["idempotency_key"] = idempotency_key
            return SimpleNamespace(
                record=SimpleNamespace(event_id=event.event_id),
                created=True,
            )

    monkeypatch.setattr(
        watcher, "build_admin_runtime", lambda runtime_dir: _AdminServiceStub(runtime_dir)
    )
    monkeypatch.setattr(watcher, "TriggerEventQueue", _QueueStub)
    wake_paths: list[Path] = []
    monkeypatch.setattr(
        watcher,
        "notify_wake_socket_for_lock",
        lambda lock_path: wake_paths.append(Path(lock_path)) or True,
    )

    result = watcher.await_agent_completion_and_publish(
        runtime_dir=tmp_path / "runtime",
        vm_name="vm-issue-14724",
        dispatch_id="dispatch-123",
        event_queue_store_path=queue_path,
        effect_lock_path=effect_lock_path,
        poll_interval_seconds=0.01,
        timeout_seconds=1.0,
    )

    assert result is True
    assert published["path"] == queue_path
    assert wake_paths == [effect_lock_path]
