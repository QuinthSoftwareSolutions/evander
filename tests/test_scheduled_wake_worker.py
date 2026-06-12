from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.execution import ExecutionBackendKind
from agent_sandbox.messaging.agent_prompt_queue import AgentPromptQueueStore
from agent_sandbox.models import (
    SessionManifest,
    ToolName,
    WakeDispatchRecord,
    WakeResumePromptPayload,
    WakeScheduleDefinition,
)
from agent_sandbox.messaging.scheduled_wake import (
    ScheduledWakeCycleResult,
    build_scheduled_wake_worker,
)
from agent_sandbox.orchestration.host_polling import HostFileLockManager


def _build_worker(
    tmp_path: Path,
    *,
    config_payload: dict[str, object] | None = None,
):
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "scheduled-wake" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"directories": ["root/team-a"], "jitter_seconds": 0.0}
    if config_payload is not None:
        payload.update(config_payload)
    worker_config_path.write_text(json.dumps(payload), encoding="utf-8")

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    return build_scheduled_wake_worker(config, project_dir=project_dir)


def _register_session(
    worker,
    *,
    session_id: str = "session-a",
    expires_at: datetime | None = None,
) -> None:
    worker.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id=session_id,
            vm_id="vm-a",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
        ),
    )


def _register_container_session(
    worker,
    *,
    session_id: str = "session-a",
    vm_provider: str | None = "container_worker_vm",
    vm_provider_id: str | None = "truman",
    metadata: dict[str, object] | None = None,
) -> None:
    worker.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id=session_id,
            vm_id=None,
            vm_provider=vm_provider,
            vm_provider_id=vm_provider_id,
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata=metadata or {},
        ),
    )


class _ContainerBackendStub:
    def __init__(self, *, active: bool) -> None:
        self.active = active
        self.prepared_specs: list[object] = []
        self.started_specs: list[object] = []
        self.repository = SimpleNamespace(list_active_by_session=self.list_active_by_session)

    def list_active_by_session(self, session_id: str, active_statuses: set[str]) -> list[object]:
        del active_statuses
        if not self.active:
            return []
        return [
            SimpleNamespace(
                session_id=session_id,
                execution_id=f"execution:{session_id}",
                last_status="running",
            )
        ]

    def prepare(self, spec):
        self.prepared_specs.append(spec)
        return spec

    def start(self, spec):
        self.started_specs.append(spec)
        return SimpleNamespace(execution_id=f"execution:{spec.target_name}")

    def inspect(self, execution_id: str):
        del execution_id
        return SimpleNamespace(status="running")


def _schedule_one_time_request(worker, *, request_id: str, session_id: str, due_at: datetime):
    return worker.runtime.wake_store.schedule_resume_request(
        session_id=session_id,
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=due_at),
        request_id=request_id,
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )


def _schedule_cron_request(worker, *, request_id: str, session_id: str, due_at: datetime):
    request = worker.runtime.wake_store.schedule_resume_request(
        session_id=session_id,
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(cron="* * * * *"),
        request_id=request_id,
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    request.next_run_at = due_at
    worker.runtime.wake_store.upsert_request(request)
    return request


def test_build_scheduled_wake_worker_uses_worker_config_and_normalizes_directories(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "scheduled-wake" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps(
            {
                "directories": [" root/team-a ", "root/team-b", "  "],
                "poll_interval_seconds": 12.5,
                "jitter_seconds": 1.5,
                "cooldown_seconds": 45.0,
                "late_threshold_seconds": 90.0,
            }
        ),
        encoding="utf-8",
    )

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_scheduled_wake_worker(config, project_dir=project_dir)

    assert worker.worker_config.directories == ["root/team-a", "root/team-b"]
    assert str(worker.worker_config.lock_path) == str(tmp_path / "runtime" / "scheduled-wake.lock")
    assert worker.config.wake_schedule_late_threshold_seconds == 90.0


def test_scheduled_wake_worker_run_once_with_lock_uses_configured_lock_path(
    tmp_path: Path,
) -> None:
    worker = _build_worker(tmp_path)

    assert isinstance(worker._kernel._lock_manager, HostFileLockManager)
    assert worker._kernel.config.lock_path == worker.worker_config.lock_path

    sentinel = object()
    worker._kernel.run_once_with_lock = lambda: sentinel  # type: ignore[method-assign]

    assert worker.run_once_with_lock() is sentinel


def test_scheduled_wake_worker_reports_busy_lock_message(tmp_path: Path) -> None:
    worker = _build_worker(tmp_path)

    with worker._kernel._lock_manager.lock(worker.worker_config.lock_path):
        with pytest.raises(RuntimeError, match="Scheduled wake worker already running"):
            worker.run_once_with_lock()
        with pytest.raises(RuntimeError, match=str(worker.worker_config.lock_path)):
            worker.run_once_with_lock()


def test_scheduled_wake_worker_backoffs_after_cycle_exception(
    tmp_path: Path,
) -> None:
    worker = _build_worker(tmp_path, config_payload={"backoff_seconds": 12.5})
    _register_session(worker)

    result = ScheduledWakeCycleResult(
        requests_due=1,
        wakes_dispatched=1,
        next_sleep_seconds=3.25,
    )
    calls = {"count": 0}

    def run_cycle():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("cycle failed")
        return result

    class _RecordingSleeper:
        def __init__(self) -> None:
            self.calls: list[float] = []

        def sleep(self, seconds: float) -> None:
            self.calls.append(seconds)
            if len(self.calls) == 2:
                raise StopIteration

    sleeper = _RecordingSleeper()
    worker._kernel._run_cycle = run_cycle  # type: ignore[method-assign]
    worker._kernel._sleeper = sleeper  # type: ignore[assignment]

    with pytest.raises(StopIteration):
        worker.run_forever()

    assert sleeper.calls == [12.5, 3.25]


def test_scheduled_wake_worker_run_once_with_lock_propagates_cycle_exceptions(
    tmp_path: Path,
) -> None:
    worker = _build_worker(tmp_path)

    def run_cycle():
        raise RuntimeError("boom")

    worker._kernel._run_cycle = run_cycle  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        worker.run_once_with_lock()


def test_scheduled_wake_worker_dispatches_due_request(tmp_path: Path, monkeypatch) -> None:
    worker = _build_worker(tmp_path, config_payload={"late_threshold_seconds": 60.0})
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=2)
    _schedule_one_time_request(worker, request_id="wake-1", session_id="session-a", due_at=due_at)

    dispatched: list[str] = []
    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    dispatch_at = datetime.now(UTC)

    def resume_binding(binding, **kwargs):
        dispatched.append(binding.session_id)
        assert kwargs["wake_metadata"]["was_late"] is True
        assert kwargs["wake_metadata"]["late_seconds"] > 0
        record = WakeDispatchRecord(
            dispatch_id="dispatch-one-time-success",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=dispatch_at,
            source=kwargs["dispatch_source"],
            metadata={
                "vm_id": binding.vm_id,
                "source": binding.source,
                **kwargs["wake_metadata"],
            },
        )
        worker.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(worker.dispatcher, "resume_binding", resume_binding)

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 1
    assert dispatched == ["session-a"]
    refreshed = worker.runtime.wake_store.get_request("wake-1")
    assert refreshed is not None
    assert refreshed.enabled is False
    assert refreshed.next_run_at is None
    cooldown_payload = json.loads(worker.worker_config.session_cooldown_path.read_text())
    assert "session-a" in cooldown_payload
    latest = worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-1"]
    assert latest.metadata["was_late"] is True
    assert latest.metadata["late_seconds"] > 0


def test_scheduled_wake_worker_marks_late_missing_session_request(
    tmp_path: Path,
) -> None:
    worker = _build_worker(tmp_path, config_payload={"late_threshold_seconds": 60.0})
    due_at = datetime.now(UTC) - timedelta(minutes=2)
    _schedule_one_time_request(worker, request_id="wake-missing", session_id="session-missing", due_at=due_at)

    result = worker.run_once()

    refreshed = worker.runtime.wake_store.get_request("wake-missing")
    assert result.requests_due == 0
    assert result.wakes_dispatched == 0
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.status == "missed_or_late"
    assert refreshed.status_reason is not None
    assert "session session-missing" in refreshed.status_reason


def test_scheduled_wake_worker_records_failed_dispatch_for_expired_due_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker, expires_at=datetime.now(UTC) - timedelta(minutes=5))
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_cron_request(
        worker, request_id="wake-expired", session_id="session-a", due_at=due_at
    )
    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    monkeypatch.setattr(
        "agent_sandbox.messaging.wake_dispatch.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("resume script must not run for expired wake sessions")
        ),
    )

    result = worker.run_once()

    refreshed = worker.runtime.wake_store.get_request("wake-expired")
    assert result.requests_due == 1
    assert result.wakes_dispatched == 0
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.next_run_at == due_at
    assert refreshed.last_dispatched_at is None
    latest = worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-expired"]
    assert latest.status == "failed"
    assert latest.error is not None
    assert "session expired: session_id=session-a expires_at=" in latest.error
    assert not worker.worker_config.session_cooldown_path.exists()


def test_scheduled_wake_worker_dispatches_due_container_request_without_vm_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(tmp_path)
    _register_container_session(worker, session_id="session-container")
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker, request_id="wake-container", session_id="session-container", due_at=due_at
    )
    backend = _ContainerBackendStub(active=False)

    monkeypatch.setattr(
        "agent_sandbox.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: backend,
    )

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 1
    assert len(backend.prepared_specs) == 1
    assert len(backend.started_specs) == 1
    spec = backend.started_specs[0]
    assert spec.backend == ExecutionBackendKind.CONTAINER_WORKER_VM
    assert spec.session_id == "session-container"
    assert "codex exec resume --last" in str(spec.command)
    assert spec.metadata["source"] == "scheduled_once"
    assert spec.metadata["resume_mode"] == "same_context"
    assert worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-container"].status == (
        "dispatched"
    )
    refreshed = worker.runtime.wake_store.get_request("wake-container")
    assert refreshed is not None
    assert refreshed.enabled is False
    assert refreshed.next_run_at is None
    assert not (tmp_path / "runtime" / "queued-agent-prompts.json").exists()


def test_scheduled_wake_worker_dispatches_due_container_request_by_metadata_and_advances_cron(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(tmp_path)
    _register_container_session(
        worker,
        session_id="session-container",
        vm_provider=None,
        vm_provider_id="metadata-target",
        metadata={
            "execution_backend": "container_worker_vm",
            "target_name": "metadata-target",
        },
    )
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_cron_request(
        worker, request_id="wake-container-cron", session_id="session-container", due_at=due_at
    )
    backend = _ContainerBackendStub(active=True)

    monkeypatch.setattr(
        "agent_sandbox.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: backend,
    )

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 1
    assert backend.prepared_specs == []
    assert backend.started_specs == []
    queue_store = AgentPromptQueueStore(tmp_path / "runtime" / "queued-agent-prompts.json")
    queued = queue_store.get("scheduled-wake:wake-container-cron")
    assert queued is not None
    assert queued.target == "session-container"
    assert queued.vm_name == "metadata-target"
    assert queued.metadata["resume_mode"] == "same_context"
    assert queued.metadata["source"] == "scheduled_cron"
    latest = worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-container-cron"]
    assert latest.status == "queued"
    refreshed = worker.runtime.wake_store.get_request("wake-container-cron")
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > due_at
    assert refreshed.last_dispatched_at is not None


def test_scheduled_wake_worker_leaves_plain_no_vm_session_due(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(tmp_path)
    worker.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-plain",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(worker, request_id="wake-plain", session_id="session-plain", due_at=due_at)

    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("busy-check should be skipped")),
    )

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 0
    assert worker.runtime.wake_store.latest_dispatch_by_request_id() == {}
    refreshed = worker.runtime.wake_store.get_request("wake-plain")
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.next_run_at == due_at
    assert refreshed.last_dispatched_at is None


def test_scheduled_wake_worker_uses_find_due_requests_directly(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    request = _schedule_one_time_request(
        worker, request_id="wake-direct", session_id="session-a", due_at=due_at
    )

    class _FakeWakeStore:
        def __init__(self, due_request):
            self.due_request = due_request
            self.find_due_requests_called = False
            self.mark_request_due_or_missed_called = False
            self.upserted_requests: list[object] = []

        def read_raw(self):
            raise AssertionError("run_once should not call read_raw")

        def mark_request_due_or_missed(self, **kwargs):
            self.mark_request_due_or_missed_called = True
            self.mark_request_due_or_missed_args = kwargs
            return []

        def mark_request_dispatching(self, *args, **kwargs):
            self.mark_request_dispatching_called = True
            self.mark_request_dispatching_args = (args, kwargs)
            return self.due_request

        def find_due_requests(self, *, now_utc):
            self.find_due_requests_called = True
            self.now_utc = now_utc
            return [self.due_request]

        def upsert_request(self, request):
            self.upserted_requests.append(request)

    fake_store = _FakeWakeStore(request)
    worker.runtime.wake_store = fake_store

    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    dispatch_at = datetime.now(UTC)

    def resume_binding(binding, **kwargs):
        return WakeDispatchRecord(
            dispatch_id="dispatch-direct-success",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=dispatch_at,
            source=kwargs["dispatch_source"],
            metadata={"vm_id": binding.vm_id, "source": binding.source},
        )

    monkeypatch.setattr(worker.dispatcher, "resume_binding", resume_binding)

    result = worker.run_once()

    assert fake_store.mark_request_due_or_missed_called is True
    assert fake_store.mark_request_dispatching_called is True
    assert fake_store.find_due_requests_called is True
    assert result.requests_due == 1
    assert result.wakes_dispatched == 1
    assert len(fake_store.upserted_requests) == 1
    refreshed = fake_store.upserted_requests[0]
    assert refreshed.request_id == "wake-direct"
    assert refreshed.enabled is False
    assert refreshed.next_run_at is None


def test_scheduled_wake_worker_dispatches_due_cron_range_request(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_cron_request(worker, request_id="wake-cron", session_id="session-a", due_at=due_at)

    dispatched: list[str] = []
    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    dispatch_at = datetime.now(UTC)

    def resume_binding(binding, **kwargs):
        dispatched.append(binding.session_id)
        record = WakeDispatchRecord(
            dispatch_id="dispatch-cron-success",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=dispatch_at,
            source=kwargs["dispatch_source"],
            metadata={"vm_id": binding.vm_id, "source": binding.source},
        )
        worker.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(worker.dispatcher, "resume_binding", resume_binding)

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 1
    assert dispatched == ["session-a"]
    refreshed = worker.runtime.wake_store.get_request("wake-cron")
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > due_at
    assert refreshed.last_dispatched_at is not None
    assert worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-cron"].status == "dispatched"
    cooldown_payload = json.loads(worker.worker_config.session_cooldown_path.read_text())
    assert "session-a" in cooldown_payload


def test_scheduled_wake_worker_leaves_due_cron_request_available_after_failed_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_cron_request(worker, request_id="wake-cron-failed", session_id="session-a", due_at=due_at)

    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    dispatch_at = datetime.now(UTC)

    def resume_binding(binding, **kwargs):
        record = WakeDispatchRecord(
            dispatch_id="dispatch-cron-failed",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=dispatch_at,
            source=kwargs["dispatch_source"],
            metadata={"vm_id": binding.vm_id, "source": binding.source},
            error="resume failed",
        )
        worker.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(worker.dispatcher, "resume_binding", resume_binding)

    result = worker.run_once()

    refreshed = worker.runtime.wake_store.get_request("wake-cron-failed")
    assert result.requests_due == 1
    assert result.wakes_dispatched == 0
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.next_run_at == due_at
    assert refreshed.last_dispatched_at is None
    assert worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-cron-failed"].status == "failed"
    assert not worker.worker_config.session_cooldown_path.exists()


def test_scheduled_wake_worker_leaves_due_cron_request_available_after_timed_out_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_cron_request(
        worker, request_id="wake-cron-timed-out", session_id="session-a", due_at=due_at
    )

    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    dispatch_at = datetime.now(UTC)

    def resume_binding(binding, **kwargs):
        record = WakeDispatchRecord(
            dispatch_id="dispatch-cron-timed-out",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="timed_out",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=dispatch_at,
            source=kwargs["dispatch_source"],
            metadata={"vm_id": binding.vm_id, "source": binding.source, "timeout_seconds": 1.0},
            error="resume timed out",
        )
        worker.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(worker.dispatcher, "resume_binding", resume_binding)

    result = worker.run_once()

    refreshed = worker.runtime.wake_store.get_request("wake-cron-timed-out")
    assert result.requests_due == 1
    assert result.wakes_dispatched == 0
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.next_run_at == due_at
    assert refreshed.last_dispatched_at is None
    assert (
        worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-cron-timed-out"].status
        == "timed_out"
    )
    assert not worker.worker_config.session_cooldown_path.exists()


def test_scheduled_wake_worker_keeps_due_cron_request_unmodified_when_resume_script_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_cron_request(worker, request_id="wake-cron-missing", session_id="session-a", due_at=due_at)

    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    monkeypatch.setattr(worker.dispatcher, "resume_binding", lambda binding, **kwargs: None)

    result = worker.run_once()

    refreshed = worker.runtime.wake_store.get_request("wake-cron-missing")
    assert result.requests_due == 1
    assert result.wakes_dispatched == 0
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.next_run_at == due_at
    assert refreshed.last_dispatched_at is None
    assert "wake-cron-missing" not in worker.runtime.wake_store.latest_dispatch_by_request_id()
    assert not worker.worker_config.session_cooldown_path.exists()
