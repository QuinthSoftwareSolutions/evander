from __future__ import annotations

import subprocess
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from agent_sandbox.agent_control import AgentControlStore
from agent_sandbox.config import GatewayConfig, HostHeartbeatWorkerGlobalConfig
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.host_heartbeat_worker import (
    FeedbackStream,
    HostHeartbeatWorker,
    _is_agent_busy,
    _probe_agent_dispatch_state,
    _is_session_in_cooldown,
    _compute_next_sleep_seconds,
    _set_session_cooldown,
    _single_instance_lock,
    build_host_heartbeat_worker,
)
from agent_sandbox.models import SessionManifest, ToolName, WakeScheduleDefinition
from agent_sandbox.pr_feedback_worker import FeedbackEnvelope, PrFeedbackStore
from agent_sandbox.services import PrFeedbackService
from agent_sandbox.wake_store import WakeRequestStore
from agent_sandbox.worker_directory_context import ShadowDomainContext, WorkerDirectoryContext

UTC = timezone.utc


class _DummyFeedbackWorker:
    store = SimpleNamespace(has_unread_feedback=lambda **kwargs: False)

    def poll_once(self, **kwargs):  # noqa: ANN003
        return []


class _DummyAudit:
    def log_decision(self, **kwargs):  # noqa: ANN003
        return None


class _CapturingAudit:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def log_decision(self, **kwargs):  # noqa: ANN003
        self.events.append(kwargs)
        return None


def _build_manifest(session_id: str, *, expires_at: datetime, metadata: dict) -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        vm_id="vm-1",
        allowed_tools=[ToolName.APPROVAL_CHECK],
        expires_at=expires_at,
        metadata=metadata,
    )


def _build_directory_scoped_gateway_config(tmp_path: Path) -> GatewayConfig:
    runtime_dir = tmp_path / "runtime"
    return GatewayConfig.model_validate(
        {
            "runtime_dir": str(runtime_dir),
            "enable_directory_scoped_config": True,
            "host_heartbeat_http_timeout_seconds": 0.1,
            "host_heartbeat_poll_interval_seconds": 30.0,
            "host_heartbeat_jitter_seconds": 0.0,
            "host_heartbeat_cooldown_seconds": 10.0,
            "host_heartbeat_stream_conflict_policy": "fanout",
        }
    )


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


def test_collect_streams_dedupes_and_skips_expired_or_invalid() -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={
                "pr_feedback_streams": [
                    {"provider": "github", "repo": "org/repo", "pr_id": "17"},
                    {"provider": "github", "repo": "org/repo", "pr_id": "17"},
                    {"provider": "", "repo": "org/repo", "pr_id": "18"},
                ]
            },
        ),
        "active-b": _build_manifest(
            "active-b",
            expires_at=now + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [
                    {"provider": "azure_devops", "repo": "repo-a", "pr_id": "99"},
                ]
            },
        ),
        "expired": _build_manifest(
            "expired",
            expires_at=now - timedelta(minutes=1),
            metadata={
                "pr_feedback_streams": [
                    {"provider": "github", "repo": "org/repo", "pr_id": "100"},
                ]
            },
        ),
    }
    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions), audit=_DummyAudit())
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(pr_map_path=Path("missing-pr-map.json")),
        runtime=runtime,
        feedback_worker=_DummyFeedbackWorker(),
        adapters={"github": object(), "azure_devops": object()},
    )

    streams = worker._collect_streams()

    assert streams == [
        FeedbackStream(provider="azure_devops", repo="repo-a", pr_id="99"),
        FeedbackStream(provider="github", repo="org/repo", pr_id="17"),
    ]


def test_collect_stream_mappings_falls_back_to_pr_map_when_manifest_missing(tmp_path) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": []},
        ),
    }
    pr_map_path = tmp_path / "runtime" / "pr-map.json"
    pr_map_path.parent.mkdir(parents=True, exist_ok=True)
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "github:org/repo:88": {
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "88",
                        "session_id": "active-a",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        directories=DirectoryStore(tmp_path / "directories.json"),
        audit=_DummyAudit(),
    )
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(pr_map_path=pr_map_path),
        runtime=runtime,
        feedback_worker=_DummyFeedbackWorker(),
        adapters={"github": object()},
    )

    mappings = worker._collect_stream_mappings()

    stream = FeedbackStream(provider="github", repo="org/repo", pr_id="88")
    assert stream in mappings
    assert mappings[stream][0].session_id == "active-a"
    assert mappings[stream][0].vm_id == "vm-1"
    assert mappings[stream][0].source == "pr_map"


def test_collect_stream_mappings_attaches_directory_shadow_context(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "88"}]},
        ),
    }
    sessions["active-a"].directory_path = "root/team-a"

    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        directories=DirectoryStore(tmp_path / "directories.json"),
        audit=_DummyAudit(),
    )
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(pr_map_path=tmp_path / "missing-pr-map.json"),
        runtime=runtime,
        feedback_worker=_DummyFeedbackWorker(),
        adapters={"github": object()},
    )

    class StubResolver:
        def resolve_for_session(self, session_id: str | None, *, fallback_directory_path: str | None = None, source: str):
            return WorkerDirectoryContext(
                directory_path=fallback_directory_path or "root",
                source=source,
                session_id=session_id,
                domains=(ShadowDomainContext(domain="workers", diff_paths=("pool.max",)),),
            )

    monkeypatch.setattr(worker, "directory_context_resolver", StubResolver())

    mappings = worker._collect_stream_mappings()

    stream = FeedbackStream(provider="github", repo="org/repo", pr_id="88")
    assert mappings[stream][0].directory_path == "root/team-a"
    assert mappings[stream][0].directory_context == {
        "directory_path": "root/team-a",
        "source": "host_heartbeat:session_manifest",
        "session_id": "active-a",
        "domains": [{"domain": "workers", "diff_paths": ["pool.max"], "validation_errors": []}],
    }


def test_collect_stream_mappings_leaves_directory_context_unset_when_feature_disabled(tmp_path) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "88"}]},
        ),
    }
    sessions["active-a"].directory_path = "root/team-a"

    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        directories=DirectoryStore(tmp_path / "directories.json"),
        audit=_DummyAudit(),
    )
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(pr_map_path=tmp_path / "missing-pr-map.json", enable_directory_scoped_config=False),
        runtime=runtime,
        feedback_worker=_DummyFeedbackWorker(),
        adapters={"github": object()},
    )

    mappings = worker._collect_stream_mappings()

    stream = FeedbackStream(provider="github", repo="org/repo", pr_id="88")
    assert mappings[stream][0].directory_path == "root/team-a"
    assert mappings[stream][0].directory_context is None


def test_run_once_rejects_conflicting_stream_when_policy_is_reject(tmp_path) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
        "active-b": _build_manifest(
            "active-b",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }

    class TrackingFeedbackWorker:
        def __init__(self) -> None:
            self.calls = 0

        def poll_once(self, **kwargs):  # noqa: ANN003
            self.calls += 1
            return []

    feedback_worker = TrackingFeedbackWorker()
    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions), audit=_DummyAudit())
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="reject",
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=feedback_worker,
        adapters={"github": object()},
    )

    result = worker.run_once()

    assert result.streams_polled == 0
    assert feedback_worker.calls == 0


def test_run_once_uses_directory_scoped_provider_adapter(tmp_path) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }
    sessions["active-a"].directory_path = "root/team-a"

    class TrackingFeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: False)

        def __init__(self) -> None:
            self.tokens: list[str | None] = []

        def poll_once(self, **kwargs):  # noqa: ANN003
            adapter = kwargs["adapter"]
            self.tokens.append(getattr(adapter, "token", None))
            return []

    class StubProviderConfigResolver:
        def resolve_for_session_id(self, session_id: str):
            assert session_id == "active-a"
            return SimpleNamespace(github_token_env="TEAM_GITHUB_TOKEN")

        def resolve_for_directory(self, directory_path: str | None):
            raise AssertionError(f"unexpected fallback for {directory_path!r}")

    feedback_worker = TrackingFeedbackWorker()
    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions), audit=_DummyAudit())
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            pr_map_path=tmp_path / "missing-pr-map.json",
            enable_directory_scoped_config=False,
        ),
        runtime=runtime,
        feedback_worker=feedback_worker,
        adapters={"github": object()},
        provider_config_resolver=StubProviderConfigResolver(),
    )

    import os

    os.environ["TEAM_GITHUB_TOKEN"] = "team-token"
    result = worker.run_once()

    assert result.streams_polled == 1
    assert feedback_worker.tokens == ["team-token"]


def test_build_host_heartbeat_worker_uses_global_config_file_for_conflict_policy(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
        "active-b": _build_manifest(
            "active-b",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }
    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        directories=DirectoryStore(tmp_path / "directories.json"),
        audit=_DummyAudit(),
    )

    class TrackingFeedbackWorker:
        def __init__(self) -> None:
            self.calls = 0

        def poll_once(self, **kwargs):  # noqa: ANN003
            self.calls += 1
            return []

    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "host-heartbeat" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps(
            {
                "host_heartbeat_directory_paths": ["root"],
                "host_heartbeat_stream_conflict_policy": "reject",
                "host_heartbeat_vm_user": "team-agent",
            }
        ),
        encoding="utf-8",
    )
    config = _build_directory_scoped_gateway_config(tmp_path)

    class _ProviderConfig:
        github_token_env = "GITHUB_TOKEN"
        azure_devops_pr_pat_env = "AZURE_DEVOPS_PR_PAT"
        azure_devops_organization_url = "https://dev.azure.com"
        azure_devops_project = "default"

    class _ProviderResolver:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def resolve_for_directory(self, directory_path):  # noqa: ANN001
            return _ProviderConfig()

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.build_runtime", lambda cfg: runtime)
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.ProviderConfigResolver", _ProviderResolver)
    monkeypatch.setattr(
        "agent_sandbox.host_heartbeat_worker.build_pr_feedback_worker",
        lambda runtime_dir, adapters, gateway_config: TrackingFeedbackWorker(),
    )

    worker = build_host_heartbeat_worker(config, project_dir=project_dir)

    result = worker.run_once()

    assert result.streams_polled == 0
    assert worker.config.host_heartbeat_stream_conflict_policy == "reject"
    assert worker.config.host_heartbeat_vm_user == "team-agent"
    assert worker.feedback_worker.calls == 0


def test_run_once_skips_polling_when_no_heartbeat_directories_are_configured(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }

    class _FeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: True)

        def poll_once(self, **kwargs):  # noqa: ANN003
            raise AssertionError("poll_once should not be called when no directories are configured")

    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions), audit=_DummyAudit())
    config = _build_directory_scoped_gateway_config(tmp_path)
    worker_config = HostHeartbeatWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "host_heartbeat_directory_paths": [],
        }
    )
    worker = HostHeartbeatWorker(
        config=config,
        worker_config=worker_config,
        runtime=runtime,
        feedback_worker=_FeedbackWorker(),
        adapters={"github": object()},
    )

    result = worker.run_once()

    assert result.streams_polled == 0
    assert result.discovered_feedback == 0


def test_run_once_processes_each_configured_directory_with_directory_resume_prompt(
    tmp_path, monkeypatch
) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo-a", "pr_id": "17"}]},
        ),
        "active-b": _build_manifest(
            "active-b",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo-b", "pr_id": "18"}]},
        ),
    }
    sessions["active-a"].directory_path = "root/team-a"
    sessions["active-b"].directory_path = "root/team-b"

    class _FeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: True)

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def poll_once(self, **kwargs):  # noqa: ANN003
            self.calls.append(kwargs)
            return [object()]

    captured: list[list[str]] = []

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        directories=DirectoryStore(tmp_path / "directories.json"),
        audit=_DummyAudit(),
    )
    directory_service = DirectoryService(runtime.directories)
    config_store = DirectoryConfigStore(runtime.directories, runtime_dir=tmp_path / "runtime")
    config_store.ensure_root()
    team_a = directory_service.create(parent="root", name="team-a")
    team_b = directory_service.create(parent="root", name="team-b")
    config_store.write(
        team_a.path,
        domain="heartbeat",
        payload={"host_heartbeat_pr_resume_prompt": "prompt-a"},
    )
    config_store.write(
        team_b.path,
        domain="heartbeat",
        payload={"host_heartbeat_pr_resume_prompt": "prompt-b"},
    )

    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)

    config = _build_directory_scoped_gateway_config(tmp_path)
    config.host_resume_script_path = resume_script
    config.host_busy_check_script_path = tmp_path / "check_busy.sh"
    config.host_heartbeat_session_cooldown_path = tmp_path / "cooldowns.json"
    config.host_heartbeat_wake_state_path = tmp_path / "wake-state.json"
    config.host_heartbeat_vm_user = "teamagent"
    config.host_heartbeat_resume_timeout_seconds = 12.0
    config.host_heartbeat_cooldown_seconds = 10.0
    config.directories_store_path = tmp_path / "directories.json"

    worker_config = HostHeartbeatWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "host_heartbeat_directory_paths": ["root/team-a", "root/team-b"],
        }
    )
    feedback_worker = _FeedbackWorker()
    worker = HostHeartbeatWorker(
        config=config,
        worker_config=worker_config,
        runtime=runtime,
        feedback_worker=feedback_worker,
        adapters={"github": object()},
    )

    result = worker.run_once()

    assert result.streams_polled == 2
    assert len(feedback_worker.calls) == 2
    assert len(captured) == 2
    assert any("--prompt" in " ".join(cmd) and "prompt-a" in " ".join(cmd) for cmd in captured)
    assert any("--prompt" in " ".join(cmd) and "prompt-b" in " ".join(cmd) for cmd in captured)


def test_compute_next_sleep_applies_jitter_and_feedback_cooldown(monkeypatch) -> None:
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.random.uniform", lambda start, end: 2.5)

    no_feedback = _compute_next_sleep_seconds(
        poll_interval_seconds=30.0,
        jitter_seconds=5.0,
        cooldown_seconds=60.0,
        discovered_feedback=0,
    )
    with_feedback = _compute_next_sleep_seconds(
        poll_interval_seconds=30.0,
        jitter_seconds=5.0,
        cooldown_seconds=60.0,
        discovered_feedback=2,
    )

    assert no_feedback == 32.5
    assert with_feedback == 60.0


def test_single_instance_lock_rejects_second_holder(tmp_path) -> None:
    lock_path = tmp_path / "host-heartbeat.lock"

    with _single_instance_lock(lock_path):
        try:
            with _single_instance_lock(lock_path):
                raise AssertionError("second lock acquisition should fail")
        except RuntimeError as exc:
            assert "already running" in str(exc)


def test_run_once_with_lock_rejects_when_lock_held(tmp_path, monkeypatch) -> None:
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_lock_path=tmp_path / "host-heartbeat.lock",
        ),
        runtime=SimpleNamespace(),
        feedback_worker=SimpleNamespace(),
        adapters={},
    )
    monkeypatch.setattr(
        "agent_sandbox.host_heartbeat_worker.HostHeartbeatWorker.run_once",
        lambda self: SimpleNamespace(streams_polled=0, discovered_feedback=0, next_sleep_seconds=0.0),
    )

    with _single_instance_lock(worker.config.host_heartbeat_lock_path):
        try:
            worker.run_once_with_lock()
            raise AssertionError("run_once_with_lock should fail when lock is already held")
        except RuntimeError as exc:
            assert "already running" in str(exc)


def test_busy_check_reports_busy_on_zero_exit(tmp_path) -> None:
    script = tmp_path / "check_session_vm_busy.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)

    assert _is_agent_busy(session_id="session-1", vm_id="vm-1", script_path=script) is True


def test_busy_check_reports_unavailable_on_disabled_exit(tmp_path) -> None:
    script = tmp_path / "check_session_vm_busy.sh"
    script.write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)

    assert _probe_agent_dispatch_state(session_id="session-1", vm_id="vm-1", script_path=script) == "unavailable"


def test_session_cooldown_state_is_persisted(tmp_path, monkeypatch) -> None:
    cooldown_path = tmp_path / "cooldowns.json"
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.time.time", lambda: 1000.0)

    _set_session_cooldown(path=cooldown_path, session_id="session-1", cooldown_seconds=30.0)
    assert _is_session_in_cooldown(path=cooldown_path, session_id="session-1") is True

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.time.time", lambda: 1035.0)
    assert _is_session_in_cooldown(path=cooldown_path, session_id="session-1") is False


def test_wake_bindings_invokes_resume_script_when_idle(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }

    class _FeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: True)

        def poll_once(self, **kwargs):  # noqa: ANN003
            return [object()]

    captured: list[list[str]] = []

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions))
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=tmp_path / "cooldowns.json",
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=resume_script,
            host_heartbeat_pr_resume_prompt="wake up now",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_FeedbackWorker(),
        adapters={"github": object()},
    )

    worker.run_once()

    assert len(captured) == 1
    cmd = captured[0]
    prompt = cmd[cmd.index("--prompt") + 1]
    assert cmd[0] == str(resume_script)
    assert "--worker-mode" in cmd
    assert "--session-id" in cmd and "active-a" in cmd
    assert "--vm-name" in cmd and "vm-1" in cmd
    assert "--host-state-file" in cmd
    assert prompt == "wake up now"


def test_wake_bindings_uses_directory_workers_override_for_resume_command(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }
    sessions["active-a"].directory_path = "root/team-a"

    class _FeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: True)

        def poll_once(self, **kwargs):  # noqa: ANN003
            return [object()]

    captured: list[list[str]] = []

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions), audit=_DummyAudit())
    config = _build_directory_scoped_gateway_config(tmp_path)
    config.host_heartbeat_vm_user = "teamagent"
    config.host_heartbeat_pr_resume_prompt = "global prompt"
    config.host_heartbeat_discord_resume_prompt = "global discord prompt"
    config.host_heartbeat_wake_state_path = tmp_path / "wake-state-root.json"
    config.host_heartbeat_resume_timeout_seconds = 12.0
    config.host_busy_check_script_path = tmp_path / "check_busy_root.sh"
    config.host_heartbeat_session_cooldown_path = tmp_path / "cooldowns-root.json"
    config.host_heartbeat_cooldown_seconds = 10.0
    directory_store = DirectoryStore(config.directories_store_path)
    directory_service = DirectoryService(directory_store)
    team_a = directory_service.create(parent="root", name="team-a")
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.ensure_root()
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    config.host_resume_script_path = resume_script
    config_store.write(
        team_a.path,
        domain="heartbeat",
        payload={
            "host_heartbeat_pr_resume_prompt": "team prompt",
        },
    )

    worker = HostHeartbeatWorker(
        config=config,
        runtime=runtime,
        feedback_worker=_FeedbackWorker(),
        adapters={"github": object()},
    )

    worker.run_once()

    assert len(captured) == 1
    cmd = captured[0]
    prompt = cmd[cmd.index("--prompt") + 1]
    assert prompt == "team prompt"
    assert "--vm-user" in cmd and "teamagent" in cmd
    assert "--host-state-file" in cmd and str(tmp_path / "wake-state-root.json") in cmd


def test_wake_bindings_invokes_resume_script_with_discord_prompt_when_discord_is_pending(
    tmp_path, monkeypatch
) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={},
        ),
    }

    captured: list[list[str]] = []

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        policy_config=_PolicyConfigStub({"active-a": ["channel-1"]}),
        discord_inbox_service=_DiscordInboxServiceStub({("active-a", "channel-1"): True}),
    )
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=tmp_path / "cooldowns.json",
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=resume_script,
            host_heartbeat_pr_resume_prompt="wake up now",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_DummyFeedbackWorker(),
        adapters={"github": object()},
    )

    worker.run_once()

    assert len(captured) == 1
    cmd = captured[0]
    prompt = cmd[cmd.index("--prompt") + 1]
    assert prompt == "discord wake"


def test_wake_bindings_invokes_resume_script_with_combined_prompt_when_pr_and_discord_are_pending(
    tmp_path, monkeypatch
) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }

    class _FeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: True)

        def poll_once(self, **kwargs):  # noqa: ANN003
            return [object()]

    captured: list[list[str]] = []

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        policy_config=_PolicyConfigStub({"active-a": ["channel-1"]}),
        discord_inbox_service=_DiscordInboxServiceStub({("active-a", "channel-1"): True}),
    )
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=tmp_path / "cooldowns.json",
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=resume_script,
            host_heartbeat_pr_resume_prompt="wake up now",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_FeedbackWorker(),
        adapters={"github": object()},
    )

    worker.run_once()

    assert len(captured) == 1
    cmd = captured[0]
    prompt = cmd[cmd.index("--prompt") + 1]
    assert prompt == "wake up now discord wake"


def test_wake_bindings_skips_resume_when_busy(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }

    class _FeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: True)

        def poll_once(self, **kwargs):  # noqa: ANN003
            return [object()]

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        raise AssertionError(f"resume should not run, got {cmd!r}")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "busy")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions))
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    cooldown_path = tmp_path / "cooldowns.json"
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=cooldown_path,
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=resume_script,
            host_heartbeat_pr_resume_prompt="wake up now",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_FeedbackWorker(),
        adapters={"github": object()},
    )

    worker.run_once()

    payload = json.loads(cooldown_path.read_text(encoding="utf-8"))
    assert "active-a" in payload


def test_wake_bindings_skips_resume_when_agent_unavailable(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }

    class _FeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: True)

        def poll_once(self, **kwargs):  # noqa: ANN003
            return [object()]

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        raise AssertionError(f"resume should not run, got {cmd!r}")

    audit = _CapturingAudit()
    monkeypatch.setattr(
        "agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state",
        lambda **kwargs: "unavailable",
    )
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions), audit=audit)
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    cooldown_path = tmp_path / "cooldowns.json"
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=cooldown_path,
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=resume_script,
            host_heartbeat_pr_resume_prompt="wake up now",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_FeedbackWorker(),
        adapters={"github": object()},
    )

    worker.run_once()

    assert not cooldown_path.exists()
    assert any(event.get("reason") == "agent-unavailable" for event in audit.events)


def test_wake_bindings_skips_resume_when_agent_disabled_in_host_store(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }

    class _FeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: True)

        def poll_once(self, **kwargs):  # noqa: ANN003
            return [object()]

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        raise AssertionError(f"resume should not run, got {cmd!r}")

    audit = _CapturingAudit()
    monkeypatch.setattr(
        "agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("busy probe should not run")),
    )
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions), audit=audit)
    control_store = AgentControlStore(tmp_path / "host-agent-control-state.json")
    control_store.set_status(vm_name="vm-1", status="disabled", phase="operator-disable")
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=tmp_path / "cooldowns.json",
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=tmp_path / "resume_issue_agent.sh",
            host_heartbeat_pr_resume_prompt="wake up now",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_FeedbackWorker(),
        adapters={"github": object()},
        agent_control_store=control_store,
    )

    worker.run_once()

    assert any(event.get("reason") == "agent-disabled" for event in audit.events)


def test_run_once_continues_when_one_stream_poll_fails(tmp_path) -> None:
    now = datetime.now(UTC)
    sessions = {
        "session-a": _build_manifest(
            "session-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo-a", "pr_id": "17"}]},
        ),
        "session-b": _build_manifest(
            "session-b",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo-b", "pr_id": "18"}]},
        ),
    }

    class _FlakyFeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: False)

        def poll_once(self, **kwargs):  # noqa: ANN003
            if kwargs["repo"] == "org/repo-a":
                raise httpx.ConnectError("provider unavailable")
            return [object()]

    audit = _CapturingAudit()
    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions), audit=audit)
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=tmp_path / "cooldowns.json",
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=tmp_path / "resume.sh",
            host_heartbeat_pr_resume_prompt="wake up now",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_FlakyFeedbackWorker(),
        adapters={"github": object()},
    )

    result = worker.run_once()

    assert result.streams_polled == 2
    assert result.discovered_feedback == 1
    assert any(event.get("reason") == "cycle-finished" for event in audit.events)


def test_wake_bindings_invokes_resume_script_for_persisted_unread_feedback(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }

    class _FeedbackWorker:
        store = SimpleNamespace(has_unread_feedback=lambda **kwargs: True)

        def poll_once(self, **kwargs):  # noqa: ANN003
            return []

    captured: list[list[str]] = []

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions))
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=tmp_path / "cooldowns.json",
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=resume_script,
            host_heartbeat_pr_resume_prompt="wake up now",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_FeedbackWorker(),
        adapters={"github": object()},
    )

    worker.run_once()

    assert len(captured) == 1


def test_wake_bindings_skips_resume_for_ignored_feedback(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest(
            "active-a",
            expires_at=now + timedelta(hours=2),
            metadata={"pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]},
        ),
    }

    class _FeedbackWorker:
        def __init__(self, store) -> None:  # noqa: ANN001
            self.store = store

        def poll_once(self, **kwargs):  # noqa: ANN003
            return []

    captured: list[list[str]] = []

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    store_path = tmp_path / "pr-feedback-store.json"
    store = PrFeedbackStore(store_path)
    assert store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="github:review_comment:42",
            provider="github",
            repo="org/repo",
            pr_id="17",
            thread_id="11",
            comment_id="42",
            author="reviewer",
            body="Already handled",
            created_at=datetime(2026, 1, 11, 12, 0, 0, tzinfo=timezone.utc),
        )
    )
    ignored_path = tmp_path / "ignored-feedback-ids.json"
    ignored_path.write_text(
        json.dumps(
            {
                "feedback_ids": [
                    {"feedback_id": "github:review_comment:42", "ignored_at": "2026-01-11T12:05:00Z"}
                ]
            }
        ),
        encoding="utf-8",
    )
    pr_feedback_service = PrFeedbackService(store_path, ignored_feedback_store_path=ignored_path)
    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        pr_feedback_service=pr_feedback_service,
    )
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=tmp_path / "cooldowns.json",
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=resume_script,
            host_heartbeat_pr_resume_prompt="wake up now",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_FeedbackWorker(store),
        adapters={"github": object()},
    )

    worker.run_once()

    assert captured == []


def test_run_once_dispatches_due_scheduled_wake_and_consumes_one_time_request(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    sessions = {
        "active-a": _build_manifest("active-a", expires_at=now + timedelta(hours=1), metadata={}),
    }
    wake_store = WakeRequestStore(tmp_path / "wake-store.json")
    scheduled = wake_store.schedule_resume_request(
        session_id="active-a",
        prompt="User scheduled prompt",
        schedule=WakeScheduleDefinition(resume_at=now - timedelta(minutes=1), cron=None),
        request_id="wake-once-1",
        source="tool",
        resume_mode="same_context",
        enabled=True,
        metadata={"origin": "test"},
    )
    wake_store.upsert_request(scheduled)

    captured: list[list[str]] = []

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        wake_store=wake_store,
        audit=_CapturingAudit(),
    )
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=tmp_path / "cooldowns.json",
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=resume_script,
            host_heartbeat_pr_resume_prompt="Static preamble.",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_DummyFeedbackWorker(),
        adapters={"github": object()},
    )

    worker.run_once()

    assert len(captured) == 1
    prompt = captured[0][captured[0].index("--prompt") + 1]
    assert prompt == "Static preamble. User scheduled prompt"
    raw = wake_store.read_raw()
    assert raw["requests"]["wake-once-1"]["enabled"] is False
    assert raw["requests"]["wake-once-1"]["next_run_at"] is None
    dispatch_entries = list(raw["dispatches"].values())
    assert len(dispatch_entries) == 1
    assert dispatch_entries[0]["source"] == "scheduled_once"
    assert dispatch_entries[0]["status"] == "dispatched"


def test_run_once_dispatches_due_cron_wake_and_advances_next_run(tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    sessions = {
        "active-a": _build_manifest("active-a", expires_at=now + timedelta(hours=1), metadata={}),
    }
    wake_store = WakeRequestStore(tmp_path / "wake-store.json")
    scheduled = wake_store.schedule_resume_request(
        session_id="active-a",
        prompt="Scheduled cron prompt",
        schedule=WakeScheduleDefinition(resume_at=None, cron="*/5 * * * *"),
        request_id="wake-cron-1",
        source="tool",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    scheduled.next_run_at = now - timedelta(minutes=1)
    wake_store.upsert_request(scheduled)

    def _fake_run(cmd, check, capture_output, text, timeout):  # noqa: ANN001
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker._probe_agent_dispatch_state", lambda **kwargs: "idle")
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.subprocess.run", _fake_run)

    runtime = SimpleNamespace(
        sessions=SimpleNamespace(read_all=lambda: sessions),
        wake_store=wake_store,
    )
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(
            host_heartbeat_http_timeout_seconds=0.1,
            host_heartbeat_poll_interval_seconds=30.0,
            host_heartbeat_jitter_seconds=0.0,
            host_heartbeat_cooldown_seconds=10.0,
            host_heartbeat_stream_conflict_policy="fanout",
            host_heartbeat_session_cooldown_path=tmp_path / "cooldowns.json",
            host_busy_check_script_path=tmp_path / "check_busy.sh",
            host_resume_script_path=resume_script,
            host_heartbeat_pr_resume_prompt="Static preamble.",
            host_heartbeat_discord_resume_prompt="discord wake",
            host_heartbeat_vm_user="agent",
            host_heartbeat_wake_state_path=tmp_path / "wake-state.json",
            host_heartbeat_resume_timeout_seconds=12.0,
            pr_map_path=tmp_path / "missing-pr-map.json",
        ),
        runtime=runtime,
        feedback_worker=_DummyFeedbackWorker(),
        adapters={"github": object()},
    )

    worker.run_once()

    raw = wake_store.read_raw()
    request_entry = raw["requests"]["wake-cron-1"]
    assert request_entry["enabled"] is True
    assert request_entry["next_run_at"] is not None
    dispatch_entries = list(raw["dispatches"].values())
    assert len(dispatch_entries) == 1
    assert dispatch_entries[0]["source"] == "scheduled_cron"
