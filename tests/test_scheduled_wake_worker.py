from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.execution import ExecutionBackendKind
from evander.daemons.effect_orchestrator import SqliteExecutionStore
from evander.effects.providers.lifecycle_hooks import (
    AgentEnqueuePromptQueueDispatchHook,
)
from evander.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    QueuedAgentPromptRecord,
)
from evander.models import (
    SessionManifest,
    ToolName,
    WakeDispatchRecord,
    WakeResumePromptPayload,
    WakeScheduleDefinition,
)
from evander.messaging.scheduled_wake import (
    _build_scheduled_resume_prompt,
    _load_scheduled_wake_worker_global_config,
    ScheduledWakeWorkerGlobalConfigReloader,
    ScheduledWakeCycleResult,
    build_scheduled_wake_worker,
)
from evander.orchestration.host_polling import HostFileLockManager
from evander.runtime import build_runtime


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


def test_build_scheduled_resume_prompt_defaults_to_requested_prompt() -> None:
    prompt = _build_scheduled_resume_prompt(GatewayConfig(), " Resume work. ")

    assert prompt == "Resume work."
    assert "list_unread_feedback" not in prompt


def test_build_scheduled_resume_prompt_applies_custom_prefix() -> None:
    config = GatewayConfig.model_validate({"scheduled_wake_prefix_text": "Scheduled wake:"})

    assert _build_scheduled_resume_prompt(config, "Resume work.") == (
        "Scheduled wake: Resume work."
    )


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
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    worker.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id=session_id,
            vm_id=None,
            vm_provider=vm_provider,
            vm_provider_id=vm_provider_id,
            directory_path="root/team-a",
            model=model,
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
                "completion_confirmation_timeout_seconds": 120.0,
                "completion_confirmation_retry_max_attempts": 4,
                "completion_confirmation_retry_window_seconds": 7200.0,
                "completion_confirmation_retry_base_backoff_seconds": 75.0,
                "completion_confirmation_retry_max_backoff_seconds": 900.0,
                "scheduled_wake_prefix_text": "Scheduled wake:",
            }
        ),
        encoding="utf-8",
    )

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_scheduled_wake_worker(config, project_dir=project_dir)

    assert worker.worker_config.directories == ["root/team-a", "root/team-b"]
    assert str(worker.worker_config.lock_path) == str(tmp_path / "runtime" / "scheduled-wake.lock")
    assert worker.config.wake_schedule_late_threshold_seconds == 90.0
    assert worker.config.wake_completion_confirmation_timeout_seconds == 120.0
    assert worker.worker_config.completion_confirmation_retry_max_attempts == 4
    assert worker.worker_config.completion_confirmation_retry_window_seconds == 7200.0
    assert worker.worker_config.completion_confirmation_retry_base_backoff_seconds == 75.0
    assert worker.worker_config.completion_confirmation_retry_max_backoff_seconds == 900.0
    assert worker.worker_config.scheduled_wake_prefix_text == "Scheduled wake:"
    assert worker.config.scheduled_wake_prefix_text == "Scheduled wake:"
    assert worker.config.wake_completion_confirmation_retry_max_attempts == 4
    assert worker.config.wake_completion_confirmation_retry_window_seconds == 7200.0
    assert worker.config.wake_completion_confirmation_retry_base_backoff_seconds == 75.0
    assert worker.config.wake_completion_confirmation_retry_max_backoff_seconds == 900.0


def test_build_scheduled_wake_worker_defaults_prefix_to_empty_string(tmp_path: Path) -> None:
    worker = _build_worker(tmp_path)

    assert worker.worker_config.scheduled_wake_prefix_text == ""
    assert worker.config.scheduled_wake_prefix_text == ""


def test_reload_uses_last_known_good_config_when_new_payload_is_invalid(
    tmp_path: Path, caplog
) -> None:
    project_dir = tmp_path / "project"
    path = project_dir / "config" / "scheduled-wake" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"scheduled_wake_prefix_text": "Initial:"}), encoding="utf-8")
    base_config = GatewayConfig(runtime_dir=tmp_path / "runtime")
    initial_config = _load_scheduled_wake_worker_global_config(project_dir, base_config=base_config)
    reloader = ScheduledWakeWorkerGlobalConfigReloader(
        project_dir=project_dir,
        initial_config=initial_config,
        base_config=base_config,
    )

    path.write_text("{not-json", encoding="utf-8")

    latest, changed = reloader.get_latest()

    assert changed is False
    assert latest.scheduled_wake_prefix_text == "Initial:"
    assert "keeping last-known-good" in caplog.text


def test_reload_now_applies_new_valid_payload(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    path = project_dir / "config" / "scheduled-wake" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"scheduled_wake_prefix_text": "Initial:"}), encoding="utf-8")
    base_config = GatewayConfig(runtime_dir=tmp_path / "runtime")
    initial_config = _load_scheduled_wake_worker_global_config(project_dir, base_config=base_config)
    reloader = ScheduledWakeWorkerGlobalConfigReloader(
        project_dir=project_dir,
        initial_config=initial_config,
        base_config=base_config,
    )

    path.write_text(json.dumps({"scheduled_wake_prefix_text": "Updated:"}), encoding="utf-8")

    latest, changed = reloader.reload_now()

    assert changed is True
    assert latest.scheduled_wake_prefix_text == "Updated:"


def test_scheduled_wake_worker_reloads_prefix_text_between_cycles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(
        tmp_path,
        config_payload={
            "cooldown_seconds": 0.0,
            "jitter_seconds": 0.0,
            "scheduled_wake_prefix_text": "Initial:",
        },
    )

    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state",
        lambda **kwargs: "idle",
    )

    dispatch_prompts: list[str] = []

    def resume_binding(self, binding, **kwargs):
        dispatch_prompts.append(kwargs["prompt"])
        record = WakeDispatchRecord(
            dispatch_id=f"dispatch-{len(dispatch_prompts)}",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=datetime.now(UTC),
            source=kwargs["dispatch_source"],
            metadata={
                "vm_id": binding.vm_id,
                "source": binding.source,
                **kwargs["wake_metadata"],
            },
        )
        worker.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.HostWakeDispatcher.resume_binding",
        resume_binding,
    )

    first_cycle = worker.run_once()
    assert first_cycle.requests_due == 0
    assert first_cycle.wakes_dispatched == 0

    worker_config_path = tmp_path / "project" / "config" / "scheduled-wake" / "config.json"
    worker_config_path.write_text(
        json.dumps(
            {
                "directories": ["root/team-a"],
                "cooldown_seconds": 0.0,
                "jitter_seconds": 0.0,
                "scheduled_wake_prefix_text": "Updated:",
            }
        ),
        encoding="utf-8",
    )

    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker,
        request_id="wake-reload",
        session_id="session-a",
        due_at=due_at,
    )

    second_cycle = worker.run_once()

    assert second_cycle.requests_due == 1
    assert second_cycle.wakes_dispatched == 1
    assert dispatch_prompts == ["Updated: Resume work."]
    assert worker.worker_config.scheduled_wake_prefix_text == "Updated:"
    assert worker.config.scheduled_wake_prefix_text == "Updated:"
    assert worker.dispatcher.config.scheduled_wake_prefix_text == "Updated:"


def test_scheduled_wake_worker_rebuilds_runtime_when_runtime_dir_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(
        tmp_path,
        config_payload={
            "cooldown_seconds": 0.0,
            "jitter_seconds": 0.0,
            "scheduled_wake_prefix_text": "Initial:",
        },
    )
    old_runtime = worker.runtime
    _register_session(worker, session_id="session-old")
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker,
        request_id="wake-old",
        session_id="session-old",
        due_at=due_at,
    )

    new_runtime_dir = tmp_path / "runtime-new"
    new_runtime = build_runtime(GatewayConfig(runtime_dir=new_runtime_dir))
    new_runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-new",
            vm_id="vm-new",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    new_runtime.wake_store.schedule_resume_request(
        session_id="session-new",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=due_at),
        request_id="wake-new",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state",
        lambda **kwargs: "idle",
    )

    dispatches: list[tuple[str, Path]] = []

    def resume_binding(self, binding, **kwargs):
        dispatches.append((kwargs["request_id"], self.runtime.config.runtime_dir))
        record = WakeDispatchRecord(
            dispatch_id=f"dispatch-{kwargs['request_id']}",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=datetime.now(UTC),
            source=kwargs["dispatch_source"],
            metadata={
                "vm_id": binding.vm_id,
                "source": binding.source,
                **kwargs["wake_metadata"],
            },
        )
        self.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.HostWakeDispatcher.resume_binding",
        resume_binding,
    )

    worker_config_path = tmp_path / "project" / "config" / "scheduled-wake" / "config.json"
    worker_config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(new_runtime_dir),
                "directories": ["root/team-a"],
                "cooldown_seconds": 0.0,
                "jitter_seconds": 0.0,
                "scheduled_wake_prefix_text": "Updated:",
            }
        ),
        encoding="utf-8",
    )

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 1
    assert dispatches == [("wake-new", new_runtime_dir)]
    assert worker.runtime is not old_runtime
    assert worker.runtime.config.runtime_dir == new_runtime_dir
    assert worker.runtime.config.session_store_path == new_runtime_dir / "sessions.json"
    assert worker.config.runtime_dir == new_runtime_dir
    assert worker.config.session_store_path == new_runtime_dir / "sessions.json"
    assert worker.dispatcher.runtime is worker.runtime
    assert worker.dispatcher.config.runtime_dir == new_runtime_dir
    assert worker.runtime.wake_store.get_request("wake-new").status == "dispatched"
    assert old_runtime.wake_store.get_request("wake-old").status == "pending"
    assert old_runtime.wake_store.latest_dispatch_by_request_id() == {}


def test_scheduled_wake_worker_keeps_last_known_good_prefix_when_reload_payload_is_invalid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(
        tmp_path,
        config_payload={
            "cooldown_seconds": 0.0,
            "jitter_seconds": 0.0,
            "scheduled_wake_prefix_text": "Initial:",
        },
    )

    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state",
        lambda **kwargs: "idle",
    )

    dispatch_prompts: list[str] = []

    def resume_binding(self, binding, **kwargs):
        dispatch_prompts.append(kwargs["prompt"])
        record = WakeDispatchRecord(
            dispatch_id=f"dispatch-{len(dispatch_prompts)}",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=datetime.now(UTC),
            source=kwargs["dispatch_source"],
            metadata={
                "vm_id": binding.vm_id,
                "source": binding.source,
                **kwargs["wake_metadata"],
            },
        )
        worker.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.HostWakeDispatcher.resume_binding",
        resume_binding,
    )

    first_cycle = worker.run_once()
    assert first_cycle.requests_due == 0
    assert first_cycle.wakes_dispatched == 0

    worker_config_path = tmp_path / "project" / "config" / "scheduled-wake" / "config.json"
    worker_config_path.write_text("{not-json", encoding="utf-8")

    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker,
        request_id="wake-invalid",
        session_id="session-a",
        due_at=due_at,
    )

    second_cycle = worker.run_once()

    assert second_cycle.requests_due == 1
    assert second_cycle.wakes_dispatched == 1
    assert dispatch_prompts == ["Initial: Resume work."]
    assert worker.worker_config.scheduled_wake_prefix_text == "Initial:"
    assert worker.config.scheduled_wake_prefix_text == "Initial:"
    assert worker.dispatcher.config.scheduled_wake_prefix_text == "Initial:"


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
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
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
    assert refreshed.enabled is True
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > due_at
    assert refreshed.status == "dispatched"
    assert refreshed.completed_at is None
    assert refreshed.last_dispatch_id == "dispatch-one-time-success"
    assert refreshed.last_dispatch_status == "dispatched"
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
    _schedule_one_time_request(
        worker, request_id="wake-missing", session_id="session-missing", due_at=due_at
    )

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
    _schedule_cron_request(worker, request_id="wake-expired", session_id="session-a", due_at=due_at)
    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.subprocess.run",
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


def test_scheduled_wake_worker_retries_failed_one_time_request_with_backoff_and_exhausts_at_cap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(
        tmp_path,
        config_payload={
            "completion_confirmation_retry_max_attempts": 2,
            "completion_confirmation_retry_window_seconds": 3600.0,
            "completion_confirmation_retry_base_backoff_seconds": 1.0,
            "completion_confirmation_retry_max_backoff_seconds": 1.0,
        },
    )
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker, request_id="wake-failed", session_id="session-a", due_at=due_at
    )

    dispatch_calls: list[str] = []
    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )

    def resume_binding(binding, **kwargs):
        dispatch_calls.append(binding.session_id)
        record = WakeDispatchRecord(
            dispatch_id=f"dispatch-failed-{len(dispatch_calls)}",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=datetime.now(UTC),
            source=kwargs["dispatch_source"],
            metadata={"vm_id": binding.vm_id, "source": binding.source},
            error="resume failed",
        )
        worker.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(worker.dispatcher, "resume_binding", resume_binding)

    first_cycle = worker.run_once()
    assert first_cycle.requests_due == 1
    assert first_cycle.wakes_dispatched == 0
    assert dispatch_calls == ["session-a"]

    refreshed = worker.runtime.wake_store.get_request("wake-failed")
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.enabled is True
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > datetime.now(UTC)
    assert refreshed.auto_dispatch_attempts == 1
    assert refreshed.auto_retry_started_at is not None
    assert refreshed.auto_retry_last_backoff_seconds == 1.0
    assert refreshed.auto_retry_exhausted_at is None

    second_cycle = worker.run_once()
    assert second_cycle.requests_due == 0
    assert second_cycle.wakes_dispatched == 0
    assert dispatch_calls == ["session-a"]

    retry_ready = worker.runtime.wake_store.get_request("wake-failed")
    assert retry_ready is not None
    retry_ready.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
    worker.runtime.wake_store.upsert_request(retry_ready)

    third_cycle = worker.run_once()
    assert third_cycle.requests_due == 1
    assert third_cycle.wakes_dispatched == 0
    assert dispatch_calls == ["session-a", "session-a"]

    exhausted = worker.runtime.wake_store.get_request("wake-failed")
    assert exhausted is not None
    assert exhausted.enabled is False
    assert exhausted.next_run_at is None
    assert exhausted.status == "failed"
    assert exhausted.auto_dispatch_attempts == 2
    assert exhausted.auto_retry_exhausted_at is not None
    assert exhausted.last_error is not None
    assert "retry limit" in exhausted.last_error.casefold()


def test_scheduled_wake_worker_failed_retry_backoff_uses_dispatch_completion_time(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(
        tmp_path,
        config_payload={
            "completion_confirmation_retry_max_attempts": 3,
            "completion_confirmation_retry_window_seconds": 3600.0,
            "completion_confirmation_retry_base_backoff_seconds": 60.0,
            "completion_confirmation_retry_max_backoff_seconds": 60.0,
        },
    )
    _register_session(worker)
    due_at = datetime(2026, 4, 1, 9, 59, tzinfo=UTC)
    _schedule_one_time_request(
        worker,
        request_id="wake-slow-failed",
        session_id="session-a",
        due_at=due_at,
    )
    cycle_started_at = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
    dispatch_completed_at = datetime(2026, 4, 1, 10, 5, tzinfo=UTC)

    class _FakeDateTime:
        calls = 0

        @classmethod
        def now(cls, tz=None):
            del tz
            cls.calls += 1
            if cls.calls == 1:
                return cycle_started_at
            return dispatch_completed_at

    monkeypatch.setattr("evander.messaging.scheduled_wake.datetime", _FakeDateTime)
    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state",
        lambda **kwargs: "idle",
    )

    def resume_binding(binding, **kwargs):
        record = WakeDispatchRecord(
            dispatch_id="dispatch-slow-failed",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=dispatch_completed_at,
            source=kwargs["dispatch_source"],
            metadata={"vm_id": binding.vm_id, "source": binding.source},
            error="resume failed after timeout",
        )
        worker.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(worker.dispatcher, "resume_binding", resume_binding)

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 0
    refreshed = worker.runtime.wake_store.get_request("wake-slow-failed")
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.next_run_at == dispatch_completed_at + timedelta(seconds=60)
    assert refreshed.next_run_at > cycle_started_at + timedelta(seconds=60)


def test_scheduled_wake_worker_backs_off_unconfirmed_one_time_dispatch_before_retrying(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(
        tmp_path,
        config_payload={
            "completion_confirmation_retry_max_attempts": 2,
            "completion_confirmation_retry_window_seconds": 3600.0,
            "completion_confirmation_retry_base_backoff_seconds": 1.0,
            "completion_confirmation_retry_max_backoff_seconds": 1.0,
        },
    )
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker, request_id="wake-unconfirmed", session_id="session-a", due_at=due_at
    )

    dispatch_time = datetime.now(UTC) - timedelta(minutes=2)
    worker.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-unconfirmed",
            request_id="wake-unconfirmed",
            session_id="session-a",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            dispatched_at=dispatch_time,
        )
    )
    queue_store = AgentPromptQueueStore(tmp_path / "runtime" / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="scheduled-wake:wake-unconfirmed",
            target="session-a",
            vm_name="vm-a",
            session_id="session-a",
            prompt="Resume work.",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            resume_mode="same_context",
            effect_id="scheduled-wake:wake-unconfirmed",
            metadata={
                "request_id": "wake-unconfirmed",
                "wake_dispatch_id": "dispatch-unconfirmed",
            },
        )
    )
    pending = worker.runtime.wake_store.get_request("wake-unconfirmed")
    assert pending is not None
    pending.auto_dispatch_attempts = 1
    pending.auto_retry_started_at = dispatch_time
    pending.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
    worker.runtime.wake_store.upsert_request(pending)

    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )

    first_cycle = worker.run_once()
    assert first_cycle.requests_due == 0
    assert first_cycle.wakes_dispatched == 0

    refreshed = worker.runtime.wake_store.get_request("wake-unconfirmed")
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.enabled is True
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > datetime.now(UTC)
    assert refreshed.auto_dispatch_attempts == 1
    assert refreshed.auto_retry_last_backoff_seconds == 1.0
    assert refreshed.last_error is not None
    assert "confirmation window" in refreshed.last_error.casefold()

    retry_ready = worker.runtime.wake_store.get_request("wake-unconfirmed")
    assert retry_ready is not None
    retry_ready.next_run_at = datetime.now(UTC) - timedelta(seconds=1)
    worker.runtime.wake_store.upsert_request(retry_ready)

    dispatch_calls: list[str] = []

    def resume_binding(binding, **kwargs):
        dispatch_calls.append(binding.session_id)
        record = WakeDispatchRecord(
            dispatch_id=f"dispatch-unconfirmed-{len(dispatch_calls)}",
            request_id=kwargs["request_id"],
            session_id=binding.session_id,
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt=kwargs["prompt"]),
            resume_mode=kwargs["resume_mode"],
            scheduled_for=kwargs["scheduled_for"],
            dispatched_at=datetime.now(UTC),
            source=kwargs["dispatch_source"],
            metadata={"vm_id": binding.vm_id, "source": binding.source},
            error="resume failed",
        )
        worker.runtime.wake_store.record_dispatch(record)
        return record

    monkeypatch.setattr(worker.dispatcher, "resume_binding", resume_binding)

    second_cycle = worker.run_once()
    assert second_cycle.requests_due == 1
    assert second_cycle.wakes_dispatched == 0
    assert dispatch_calls == ["session-a"]

    exhausted = worker.runtime.wake_store.get_request("wake-unconfirmed")
    assert exhausted is not None
    assert exhausted.enabled is False
    assert exhausted.next_run_at is None
    assert exhausted.status == "failed"
    assert exhausted.auto_dispatch_attempts == 2
    assert exhausted.auto_retry_exhausted_at is not None
    assert exhausted.last_error is not None
    assert "retry limit" in exhausted.last_error.casefold()
    assert queue_store.get("scheduled-wake:wake-unconfirmed") is None


def test_scheduled_wake_worker_dispatches_due_container_request_without_vm_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(tmp_path)
    _register_container_session(
        worker,
        session_id="session-container",
        metadata={"playwright": True},
    )
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker, request_id="wake-container", session_id="session-container", due_at=due_at
    )
    backend = _ContainerBackendStub(active=False)

    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.build_execution_backend",
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
    assert spec.resource_limits == {"pids_limit": 1024}
    assert "codex exec resume --last" in str(spec.command)
    assert spec.metadata["source"] == "scheduled_once"
    assert spec.metadata["resume_mode"] == "same_context"
    assert worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-container"].status == (
        "dispatched"
    )
    refreshed = worker.runtime.wake_store.get_request("wake-container")
    assert refreshed is not None
    assert refreshed.enabled is True
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > due_at
    assert refreshed.completed_at is None
    assert not (tmp_path / "runtime" / "queued-agent-prompts.json").exists()


def test_scheduled_wake_worker_dispatches_due_claude_container_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(tmp_path)
    _register_container_session(
        worker,
        session_id="session-container-claude",
        model=None,
        metadata={"agent_provider": "claude"},
    )
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker,
        request_id="wake-container-claude",
        session_id="session-container-claude",
        due_at=due_at,
    )
    backend = _ContainerBackendStub(active=False)

    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: backend,
    )

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 1
    assert len(backend.prepared_specs) == 1
    assert len(backend.started_specs) == 1
    spec = backend.started_specs[0]
    assert spec.backend == ExecutionBackendKind.CONTAINER_WORKER_VM
    assert spec.session_id == "session-container-claude"
    assert spec.resource_limits is None
    assert "claude --continue -p" in str(spec.command)
    assert "--model claude-sonnet-4-6" in str(spec.command)
    assert "codex exec" not in str(spec.command)
    assert spec.metadata["source"] == "scheduled_once"
    assert spec.metadata["resume_mode"] == "same_context"
    assert spec.metadata["agent_provider"] == "claude"
    latest = worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-container-claude"]
    assert latest.status == "dispatched"


def test_scheduled_wake_worker_preserves_claude_queue_metadata_for_later_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(tmp_path)
    _register_container_session(
        worker,
        session_id="session-container-claude",
        model=None,
        metadata={
            "agent_provider": "claude",
            "target_name": "metadata-target",
        },
        vm_provider_id="metadata-target",
    )
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker,
        request_id="wake-container-claude",
        session_id="session-container-claude",
        due_at=due_at,
    )
    dispatch_backend = _ContainerBackendStub(active=True)

    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: dispatch_backend,
    )

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 1
    queue_store = AgentPromptQueueStore(tmp_path / "runtime" / "queued-agent-prompts.json")
    queued = queue_store.get("scheduled-wake:wake-container-claude")
    assert queued is not None
    assert queued.model == "claude-sonnet-4-6"
    assert queued.metadata["agent_provider"] == "claude"
    latest = worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-container-claude"]
    assert queued.metadata["wake_dispatch_id"] == latest.dispatch_id

    replay_backend = _ContainerBackendStub(active=False)
    execution_store = SqliteExecutionStore(tmp_path / "runtime" / "orchestration.db")
    replay_config = GatewayConfig(
        runtime_dir=tmp_path / "runtime",
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda resolved_config, *, admin_service: replay_backend,
    )
    orchestrator_worker = SimpleNamespace(
        config=replay_config,
        execution_store=execution_store,
        queued_agent_prompt_store=queue_store,
        agent_prompt_dispatch_port=object(),
        provider_config_resolver=SimpleNamespace(
            resolve_for_session_id=lambda _session_id: replay_config
        ),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=worker.runtime.session_repository)
            )
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: "started",
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(
        worker=orchestrator_worker
    )

    assert dispatched == 1
    assert queue_store.read_all() == {}
    command = str(replay_backend.started_specs[0].command)
    assert "claude --continue -p" in command
    assert "--model claude-sonnet-4-6" in command
    assert "codex exec" not in command
    waiting_records = list(execution_store.read_all().values())
    assert waiting_records[0].resolved_config["agent_provider"] == "claude"
    assert waiting_records[0].resolved_config["model"] == "claude-sonnet-4-6"


def test_cancel_queued_container_wake_deletes_prompt_queue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _build_worker(tmp_path)
    _register_container_session(
        worker,
        session_id="session-container",
        vm_provider_id="metadata-target",
        metadata={
            "execution_backend": "container_worker_vm",
            "target_name": "metadata-target",
        },
    )
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(
        worker,
        request_id="wake-container-cancel",
        session_id="session-container",
        due_at=due_at,
    )
    backend = _ContainerBackendStub(active=True)
    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: backend,
    )

    result = worker.run_once()

    assert result.requests_due == 1
    queue_store = AgentPromptQueueStore(tmp_path / "runtime" / "queued-agent-prompts.json")
    assert queue_store.get("scheduled-wake:wake-container-cancel") is not None

    cancellation = worker.runtime.wake_store.cancel_request(
        "wake-container-cancel",
        caller_session_id="session-container",
        reason="obsolete wake",
        metadata={"source": "test"},
    )

    assert cancellation.status == "cancelled"
    assert queue_store.get("scheduled-wake:wake-container-cancel") is None


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
        "evander.messaging.wake_dispatch.build_execution_backend",
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
    assert refreshed.status == latest.status
    assert refreshed.last_dispatch_id == latest.dispatch_id
    assert refreshed.last_dispatch_status == latest.status
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
    _schedule_one_time_request(
        worker, request_id="wake-plain", session_id="session-plain", due_at=due_at
    )

    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state",
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


def test_scheduled_wake_worker_uses_find_due_requests_directly(tmp_path: Path, monkeypatch) -> None:
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
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
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
    assert refreshed.enabled is True
    assert refreshed.next_run_at is not None
    assert refreshed.next_run_at > due_at


def test_scheduled_wake_worker_ignores_cancelled_due_requests(
    tmp_path: Path,
) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_cron_request(
        worker, request_id="wake-cancelled", session_id="session-a", due_at=due_at
    )

    cancelled = worker.runtime.wake_store.cancel_request(
        "wake-cancelled",
        caller_session_id="session-a",
        reason="obsolete wake",
        metadata={"source": "test"},
        now_utc=datetime.now(UTC),
    )

    assert cancelled.status == "cancelled"
    assert worker.runtime.wake_store.find_due_requests(now_utc=datetime.now(UTC)) == []


def test_scheduled_wake_worker_dispatches_due_cron_range_request(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_cron_request(worker, request_id="wake-cron", session_id="session-a", due_at=due_at)

    dispatched: list[str] = []
    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
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
    assert refreshed.status == "dispatched"
    assert refreshed.last_dispatch_id == "dispatch-cron-success"
    assert refreshed.last_dispatch_status == "dispatched"
    assert refreshed.last_dispatched_at is not None
    assert (
        worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-cron"].status
        == "dispatched"
    )
    cooldown_payload = json.loads(worker.worker_config.session_cooldown_path.read_text())
    assert "session-a" in cooldown_payload


def test_scheduled_wake_worker_leaves_due_cron_request_available_after_failed_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_cron_request(
        worker, request_id="wake-cron-failed", session_id="session-a", due_at=due_at
    )

    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
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
    assert (
        worker.runtime.wake_store.latest_dispatch_by_request_id()["wake-cron-failed"].status
        == "failed"
    )
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
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
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
    _schedule_cron_request(
        worker, request_id="wake-cron-missing", session_id="session-a", due_at=due_at
    )

    monkeypatch.setattr(
        "evander.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
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
