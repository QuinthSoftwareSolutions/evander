from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from agent_sandbox.config import GatewayConfig
from agent_sandbox.discord_wake_worker import build_discord_wake_worker
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
            self.pending_by_session_and_channel.get((session_id, str(stream.get("channel_id", ""))), False)
            for stream in streams
        )
        return SimpleNamespace(pending=pending)


def test_build_discord_wake_worker_uses_worker_config_and_normalizes_directories(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "discord-wake" / "config.json"
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
    worker = build_discord_wake_worker(config, project_dir=project_dir)

    assert worker.worker_config.directories == ["root/team-a", "root/team-b"]
    assert str(worker.worker_config.lock_path) == str(tmp_path / "runtime" / "discord-wake.lock")


def test_discord_wake_worker_dispatches_resume_for_pending_channel(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "discord-wake" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(json.dumps({"directories": ["root/team-a"], "jitter_seconds": 0.0}), encoding="utf-8")

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
    worker.runtime.discord_inbox_service = _DiscordInboxServiceStub({("session-a", "channel-1"): True})

    dispatched: list[str] = []
    monkeypatch.setattr("agent_sandbox.discord_wake_worker.probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr(
        worker.dispatcher,
        "resume_binding",
        lambda binding, **kwargs: dispatched.append(binding.session_id) or SimpleNamespace(status="dispatched"),
    )

    result = worker.run_once()

    assert result.channels_polled == 1
    assert result.pending_channels == 1
    assert dispatched == ["session-a"]
