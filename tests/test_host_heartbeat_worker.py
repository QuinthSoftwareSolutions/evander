from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agent_sandbox.host_heartbeat_worker import (
    FeedbackStream,
    HostHeartbeatWorker,
    _compute_next_sleep_seconds,
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
