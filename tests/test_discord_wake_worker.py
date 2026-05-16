from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.messaging.discord_wake import (
    DiscordWakeCycleResult,
    build_discord_wake_worker,
)
from agent_sandbox.orchestration.host_polling import HostFileLockManager
from agent_sandbox.models import SessionManifest, ToolName


class _PolicyConfigStub:
    def __init__(self, routes_by_session: dict[str, list[str]]) -> None:
        self.routes_by_session = routes_by_session

    def discord_inbox_channel_ids_for_session_id(self, session_id: str) -> list[str]:
        return list(self.routes_by_session.get(session_id, []))


class _DiscordInboxServiceStub:
    def __init__(self, pending_by_session_and_channel: dict[tuple[str, str], bool]) -> None:
        self.pending_by_session_and_channel = pending_by_session_and_channel

    def has_pending_discord_messages(self, *, session_id: str, streams: list[dict[str, str]]):  # noqa: ANN003
        pending = any(
            self.pending_by_session_and_channel.get(
                (session_id, str(stream.get("channel_id", ""))), False
            )
            for stream in streams
        )
        return SimpleNamespace(pending=pending)


def _build_worker(
    tmp_path: Path,
    *,
    config_payload: dict[str, object] | None = None,
) -> object:
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "discord-wake" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "directories": [" root/team-a ", "root/team-b", "  "],
        "poll_interval_seconds": 12.5,
        "jitter_seconds": 1.5,
        "cooldown_seconds": 45.0,
    }
    if config_payload is not None:
        payload.update(config_payload)
    worker_config_path.write_text(json.dumps(payload), encoding="utf-8")

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    return build_discord_wake_worker(config, project_dir=project_dir)


def test_build_discord_wake_worker_uses_worker_config_and_normalizes_directories(
    tmp_path: Path,
) -> None:
    worker = _build_worker(tmp_path)

    assert worker.worker_config.directories == ["root/team-a", "root/team-b"]
    assert str(worker.worker_config.lock_path) == str(tmp_path / "runtime" / "discord-wake.lock")


def test_discord_wake_worker_run_once_with_lock_uses_configured_lock_path(
    tmp_path: Path,
) -> None:
    worker = _build_worker(tmp_path)

    assert isinstance(worker._kernel._lock_manager, HostFileLockManager)
    assert worker._kernel.config.lock_path == worker.worker_config.lock_path

    sentinel = object()
    worker._kernel.run_once_with_lock = lambda: sentinel  # type: ignore[method-assign]

    assert worker.run_once_with_lock() is sentinel


def test_discord_wake_worker_reports_busy_lock_message(tmp_path: Path) -> None:
    worker = _build_worker(tmp_path)

    with worker._kernel._lock_manager.lock(worker.worker_config.lock_path):
        with pytest.raises(RuntimeError) as exc:
            worker.run_once_with_lock()

    assert "Discord wake worker already running" in str(exc.value)
    assert str(worker.worker_config.lock_path) in str(exc.value)


def test_discord_wake_worker_backoffs_after_cycle_exception(tmp_path: Path) -> None:
    worker = _build_worker(tmp_path, config_payload={"backoff_seconds": 12.5})

    result = DiscordWakeCycleResult(
        channels_polled=1,
        pending_channels=1,
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


def test_discord_wake_worker_run_once_with_lock_propagates_cycle_exceptions(
    tmp_path: Path,
) -> None:
    worker = _build_worker(tmp_path)

    def run_cycle():
        raise RuntimeError("boom")

    worker._kernel._run_cycle = run_cycle  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        worker.run_once_with_lock()


def test_discord_wake_worker_dispatches_resume_for_pending_channel(
    tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "discord-wake" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps({"directories": ["root/team-a"], "jitter_seconds": 0.0}), encoding="utf-8"
    )

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_discord_wake_worker(config, project_dir=project_dir)
    worker.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            vm_id="vm-a",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    worker.runtime.policy_config = _PolicyConfigStub({"session-a": ["channel-1"]})
    worker.runtime.discord_inbox_service = _DiscordInboxServiceStub(
        {("session-a", "channel-1"): True}
    )

    dispatched: list[str] = []
    monkeypatch.setattr(
        "agent_sandbox.messaging.discord_wake.probe_agent_dispatch_state", lambda **kwargs: "idle"
    )
    monkeypatch.setattr(
        worker.dispatcher,
        "resume_binding",
        lambda binding, **kwargs: (
            dispatched.append(binding.session_id) or SimpleNamespace(status="dispatched")
        ),
    )

    result = worker.run_once()

    assert result.channels_polled == 1
    assert result.pending_channels == 1
    assert dispatched == ["session-a"]
