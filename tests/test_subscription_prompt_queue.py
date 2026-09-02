from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook
from evander.messaging.agent_prompt_queue import AgentPromptQueueStore, QueuedAgentPromptRecord


def _record(
    *,
    queue_key: str,
    profile_id: str,
    created_at: datetime,
) -> QueuedAgentPromptRecord:
    return QueuedAgentPromptRecord(
        queue_key=queue_key,
        target=queue_key,
        vm_name=queue_key,
        session_id=queue_key,
        prompt=f"prompt for {queue_key}",
        effect_id=f"effect-{queue_key}",
        subscription_profile_id=profile_id,
        created_at=created_at,
        updated_at=created_at,
    )


def test_capacity_deferrals_do_not_consume_retry_ledger_and_use_reported_reset(
    tmp_path: Path,
) -> None:
    store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    reset_at = datetime.now(UTC) + timedelta(minutes=8)
    record = _record(
        queue_key="session-a",
        profile_id="team-a",
        created_at=datetime.now(UTC),
    )

    first = store.defer_capacity(
        record,
        queue_reason="subscription_rate_limited",
        retry_at=reset_at,
    )
    second = store.defer_capacity(
        first.model_copy(deep=True),
        queue_reason="subscription_unavailable",
        retry_at=None,
    )

    assert first.retry_count == 0
    assert first.retry_key is None
    assert first.max_retries is None
    assert first.retry_not_before == reset_at
    assert second.capacity_deferral_count == 2
    assert second.queue_reason == "subscription_unavailable"
    assert second.retry_not_before is not None
    assert second.retry_not_before <= datetime.now(UTC) + timedelta(minutes=15)
    assert json.loads((tmp_path / "agent-prompt-retry-ledger.json").read_text()) == {}


def test_capacity_deferral_ignores_stale_reported_reset_and_recovery_can_wake_profile(
    tmp_path: Path,
) -> None:
    store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    record = _record(
        queue_key="session-a",
        profile_id="team-a",
        created_at=datetime.now(UTC),
    )

    deferred = store.defer_capacity(
        record,
        queue_reason="subscription_rate_limited",
        retry_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    assert deferred.retry_not_before is not None
    assert deferred.retry_not_before > datetime.now(UTC)
    assert store.make_subscription_profile_ready("team-b") == 0
    assert store.make_subscription_profile_ready("team-a") == 1
    assert store.get("session-a").retry_not_before is None


def _fifo_worker(*, sessions: dict[str, object], worker_urls: dict[str, str]) -> object:
    return SimpleNamespace(
        provider_config_resolver=SimpleNamespace(
            resolve_for_session_id=lambda session_id: SimpleNamespace(
                container_worker_base_url=worker_urls[session_id]
            )
        ),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(
                    session_repository=SimpleNamespace(
                        get_session=lambda session_id: sessions.get(session_id)
                    )
                )
            )
        ),
    )


def _fifo_session(session_id: str, *, expired: bool = False) -> object:
    return SimpleNamespace(
        session_id=session_id,
        directory_path="root",
        vm_provider="container_worker_vm",
        metadata={},
        expires_at=datetime.now(UTC) + (timedelta(minutes=-5) if expired else timedelta(hours=1)),
    )


def test_subscription_queue_is_fifo_per_profile_and_effective_worker() -> None:
    now = datetime.now(UTC)
    records = {
        "older-a": _record(
            queue_key="older-a", profile_id="team-a", created_at=now - timedelta(seconds=2)
        ),
        "newer-a": _record(queue_key="newer-a", profile_id="team-a", created_at=now),
        "newer-b": _record(queue_key="newer-b", profile_id="team-b", created_at=now),
    }
    worker = _fifo_worker(
        sessions={key: _fifo_session(key) for key in records},
        worker_urls={key: "http://worker-a.test" for key in records},
    )

    hook = AgentEnqueuePromptQueueDispatchHook()
    assert hook._has_earlier_subscription_profile_record(
        worker=worker,
        records=records,
        queue_key="newer-a",
    )
    assert not hook._has_earlier_subscription_profile_record(
        worker=worker,
        records=records,
        queue_key="newer-b",
    )


def test_subscription_queue_fifo_ignores_other_workers_and_unrunnable_sessions() -> None:
    now = datetime.now(UTC)
    records = {
        "other-worker": _record(
            queue_key="other-worker",
            profile_id="default",
            created_at=now - timedelta(seconds=3),
        ),
        "missing-session": _record(
            queue_key="missing-session",
            profile_id="default",
            created_at=now - timedelta(seconds=2),
        ),
        "expired-session": _record(
            queue_key="expired-session",
            profile_id="default",
            created_at=now - timedelta(seconds=1),
        ),
        "ready": _record(queue_key="ready", profile_id="default", created_at=now),
    }
    worker = _fifo_worker(
        sessions={
            "other-worker": _fifo_session("other-worker"),
            "expired-session": _fifo_session("expired-session", expired=True),
            "ready": _fifo_session("ready"),
        },
        worker_urls={
            "other-worker": "http://worker-b.test",
            "expired-session": "http://worker-a.test",
            "ready": "http://worker-a.test",
        },
    )

    assert not AgentEnqueuePromptQueueDispatchHook()._has_earlier_subscription_profile_record(
        worker=worker,
        records=records,
        queue_key="ready",
    )


def test_subscription_queue_has_no_expiry_and_obeys_retry_not_before() -> None:
    record = _record(
        queue_key="session-a",
        profile_id="team-a",
        created_at=datetime.now(UTC) - timedelta(days=365),
    )
    assert not hasattr(record, "expires_at")

    record.retry_not_before = datetime.now(UTC) + timedelta(minutes=1)
    assert not AgentEnqueuePromptQueueDispatchHook._retry_not_before_ready(record)
    record.retry_not_before = datetime.now(UTC) - timedelta(seconds=1)
    assert AgentEnqueuePromptQueueDispatchHook._retry_not_before_ready(record)
