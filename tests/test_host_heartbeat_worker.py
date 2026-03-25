from __future__ import annotations

import subprocess
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

from agent_sandbox.host_heartbeat_worker import (
    FeedbackStream,
    HostHeartbeatWorker,
    _is_agent_busy,
    _probe_agent_dispatch_state,
    _is_session_in_cooldown,
    _compute_next_sleep_seconds,
    _set_session_cooldown,
    _single_instance_lock,
)
from agent_sandbox.models import SessionManifest, ToolName

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
    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions), audit=_DummyAudit())
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
            host_heartbeat_resume_prompt="wake up now",
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
    assert cmd[0] == str(resume_script)
    assert "--worker-mode" in cmd
    assert "--session-id" in cmd and "active-a" in cmd
    assert "--vm-name" in cmd and "vm-1" in cmd
    assert "--host-state-file" in cmd


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
            host_heartbeat_resume_prompt="wake up now",
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
            host_heartbeat_resume_prompt="wake up now",
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
            host_heartbeat_resume_prompt="wake up now",
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
            host_heartbeat_resume_prompt="wake up now",
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
