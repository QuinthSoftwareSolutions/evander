from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agent_sandbox.host_heartbeat_worker import (
    FeedbackStream,
    HostHeartbeatWorker,
    _is_agent_busy,
    _is_session_in_cooldown,
    _compute_next_sleep_seconds,
    _set_session_cooldown,
    _single_instance_lock,
)
from agent_sandbox.models import SessionManifest, ToolName

UTC = timezone.utc


class _DummyFeedbackWorker:
    def poll_once(self, **kwargs):  # noqa: ANN003
        return []


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
    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions))
    worker = HostHeartbeatWorker(
        config=SimpleNamespace(),
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
    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions))
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
    runtime = SimpleNamespace(sessions=SimpleNamespace(read_all=lambda: sessions))
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


def test_busy_check_reports_busy_on_zero_exit(tmp_path) -> None:
    script = tmp_path / "check_session_vm_busy.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)

    assert _is_agent_busy(session_id="session-1", vm_id="vm-1", script_path=script) is True


def test_session_cooldown_state_is_persisted(tmp_path, monkeypatch) -> None:
    cooldown_path = tmp_path / "cooldowns.json"
    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.time.time", lambda: 1000.0)

    _set_session_cooldown(path=cooldown_path, session_id="session-1", cooldown_seconds=30.0)
    assert _is_session_in_cooldown(path=cooldown_path, session_id="session-1") is True

    monkeypatch.setattr("agent_sandbox.host_heartbeat_worker.time.time", lambda: 1035.0)
    assert _is_session_in_cooldown(path=cooldown_path, session_id="session-1") is False
