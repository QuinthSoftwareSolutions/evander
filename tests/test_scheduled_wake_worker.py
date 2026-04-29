from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from agent_sandbox.config import GatewayConfig
from agent_sandbox.models import SessionManifest, ToolName, WakeScheduleDefinition
from agent_sandbox.messaging.scheduled_wake import build_scheduled_wake_worker


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
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "scheduled-wake" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps({"directories": ["root/team-a"], "jitter_seconds": 0.0}), encoding="utf-8"
    )

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_scheduled_wake_worker(config, project_dir=project_dir)
    worker.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            vm_id="vm-a",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    worker.runtime.wake_store.schedule_resume_request(
        session_id="session-a",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=datetime.now(UTC) - timedelta(minutes=1)),
        request_id="wake-1",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    dispatched: list[str] = []
    monkeypatch.setattr(
        "agent_sandbox.messaging.scheduled_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    monkeypatch.setattr(
        worker.dispatcher,
        "resume_binding",
        lambda binding, **kwargs: (
            dispatched.append(binding.session_id) or SimpleNamespace(status="dispatched")
        ),
    )

    result = worker.run_once()

    assert result.requests_due == 1
    assert result.wakes_dispatched == 1
    assert dispatched == ["session-a"]
