from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.models import (
    SessionManifest,
    ToolName,
    WakeDispatchRecord,
    WakeResumePromptPayload,
    WakeScheduleDefinition,
)
from agent_sandbox.messaging.scheduled_wake import build_scheduled_wake_worker


def _build_worker(tmp_path: Path):
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "scheduled-wake" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps({"directories": ["root/team-a"], "jitter_seconds": 0.0}), encoding="utf-8"
    )

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    return build_scheduled_wake_worker(config, project_dir=project_dir)


def _register_session(worker, *, session_id: str = "session-a") -> None:
    worker.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id=session_id,
            vm_id="vm-a",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )


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
            }
        ),
        encoding="utf-8",
    )

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_scheduled_wake_worker(config, project_dir=project_dir)

    assert worker.worker_config.directories == ["root/team-a", "root/team-b"]
    assert str(worker.worker_config.lock_path) == str(tmp_path / "runtime" / "scheduled-wake.lock")


def test_scheduled_wake_worker_dispatches_due_request(tmp_path: Path, monkeypatch) -> None:
    worker = _build_worker(tmp_path)
    _register_session(worker)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    _schedule_one_time_request(worker, request_id="wake-1", session_id="session-a", due_at=due_at)

    dispatched: list[str] = []
    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    dispatch_at = datetime.now(UTC)

    def resume_binding(binding, **kwargs):
        dispatched.append(binding.session_id)
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
            metadata={"vm_id": binding.vm_id, "source": binding.source},
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
