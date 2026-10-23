from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.codex_auth import CodexAuthMode
from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook
from evander.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    ModelCapacityRetryExhausted,
    QueuedAgentPromptRecord,
    agent_prompt_replay_queue_key,
    queued_agent_prompt_dispatch_id,
)


def _record(
    *,
    queue_key: str,
    profile_id: str | None,
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


def test_model_capacity_budget_and_backoff_survive_dispatched_queue_removal(tmp_path, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("evander.messaging.agent_prompt_queue.utc_now", lambda: now)
    monkeypatch.setattr("evander.messaging.agent_prompt_queue.random.uniform", lambda a, b: a)
    record = _record(queue_key="model-job", profile_id="team-a", created_at=now)
    record.dispatch_id = "dispatch-model-job"
    first_failure = now
    for attempt, base_seconds in enumerate([15, 30, 60, 120, 240, 480, 900, 900], start=1):
        store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
        record.metadata["model_capacity_failed_execution_id"] = f"worker-{attempt}"
        deferred = store.defer_capacity(record, queue_reason="model_capacity", retry_at=None)
        assert deferred.retry_not_before == now + timedelta(seconds=base_seconds * 0.8)
        assert deferred.metadata["model_capacity_retry_count"] == str(attempt)
        assert datetime.fromisoformat(deferred.metadata["model_capacity_retry_started_at"]) == first_failure
        # Reconciliation after a crash must not charge the same failed worker twice.
        duplicate = store.defer_capacity(record, queue_reason="model_capacity", retry_at=None)
        assert duplicate == deferred
        store.delete_if_dispatch_id(record.queue_key, record.dispatch_id)
        record = deferred.model_copy(deep=True)
        now = deferred.retry_not_before

    record.metadata["model_capacity_failed_execution_id"] = "worker-9"
    with pytest.raises(ModelCapacityRetryExhausted) as exc:
        AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").defer_capacity(
            record, queue_reason="model_capacity", retry_at=None
        )
    assert exc.value.retry_count == 8
    assert exc.value.deadline == first_failure + timedelta(hours=1)
    assert json.loads((tmp_path / "agent-prompt-retry-ledger.json").read_text()) == {}


def test_model_capacity_deadline_caps_reported_retry_and_survives_profile_recovery(tmp_path, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr("evander.messaging.agent_prompt_queue.utc_now", lambda: now)
    store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    record = _record(queue_key="model-job", profile_id="team-a", created_at=now)
    deferred = store.defer_capacity(
        record, queue_reason="model_capacity", retry_at=now + timedelta(days=1)
    )
    assert deferred.retry_not_before == now + timedelta(hours=1)
    assert store.make_subscription_profile_ready("team-a") == 0
    now += timedelta(hours=1)
    with pytest.raises(ModelCapacityRetryExhausted):
        store.defer_capacity(deferred, queue_reason="model_capacity", retry_at=None)


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


def test_auth_route_change_releases_only_that_sessions_subscription_deferrals(
    tmp_path: Path,
) -> None:
    store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    now = datetime.now(UTC)
    for queue_key, session_id in (("first", "session-a"), ("second", "session-b")):
        record = _record(queue_key=queue_key, profile_id="team-a", created_at=now)
        record.session_id = session_id
        store.defer_capacity(
            record,
            queue_reason="subscription_rate_limited",
            retry_at=now + timedelta(hours=1),
        )
    ordinary = _record(queue_key="ordinary", profile_id="team-a", created_at=now)
    ordinary.session_id = "session-a"
    store.put(ordinary)

    assert store.reroute_session("session-a", subscription_profile_id="team-b") == 2

    rerouted = store.get("first")
    assert rerouted is not None
    assert rerouted.subscription_profile_id == "team-b"
    assert rerouted.queue_reason is None
    assert rerouted.retry_not_before is None
    assert rerouted.capacity_deferral_count == 0

    ordinary_rerouted = store.get("ordinary")
    assert ordinary_rerouted is not None
    assert ordinary_rerouted.subscription_profile_id == "team-b"
    assert ordinary_rerouted.queue_reason is None

    unchanged = store.get("second")
    assert unchanged is not None
    assert unchanged.subscription_profile_id == "team-a"
    assert unchanged.queue_reason == "subscription_rate_limited"
    assert unchanged.retry_not_before is not None


def test_stale_capacity_deferral_cannot_overwrite_rerouted_claim(tmp_path: Path) -> None:
    store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    record = _record(
        queue_key="queued",
        profile_id="team-a",
        created_at=datetime.now(UTC),
    )
    record.session_id = "session-a"
    store.put(record)
    dispatch_id = queued_agent_prompt_dispatch_id(record)
    claimed = store.claim_for_replay(
        record.queue_key,
        dispatch_id,
        claimed_queue_key=agent_prompt_replay_queue_key(record),
    )
    assert claimed is not None

    assert store.reroute_session("session-a", subscription_profile_id="team-b") == 1
    claimed.subscription_profile_id = "team-a"
    persisted = store.defer_capacity(
        claimed,
        queue_reason="subscription_rate_limited",
        retry_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert persisted.subscription_profile_id == "team-b"
    assert persisted.queue_reason is None
    assert persisted.retry_not_before is None
    assert store.get(claimed.queue_key) == persisted


def test_capacity_deferral_assigns_profile_to_unprofiled_claim(tmp_path: Path) -> None:
    store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    record = _record(
        queue_key="queued",
        profile_id=None,
        created_at=datetime.now(UTC),
    )
    store.put(record)
    dispatch_id = queued_agent_prompt_dispatch_id(record)
    claimed = store.claim_for_replay(
        record.queue_key,
        dispatch_id,
        claimed_queue_key=agent_prompt_replay_queue_key(record),
    )
    assert claimed is not None

    claimed.subscription_profile_id = "team-a"
    deferred = store.defer_capacity(
        claimed,
        queue_reason="subscription_rate_limited",
        retry_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert deferred.subscription_profile_id == "team-a"
    assert deferred.queue_reason == "subscription_rate_limited"
    assert store.get(claimed.queue_key) == deferred


def test_retrying_same_route_does_not_release_current_route_deferral(tmp_path: Path) -> None:
    store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    record = _record(
        queue_key="queued",
        profile_id="team-b",
        created_at=datetime.now(UTC),
    )
    record.session_id = "session-a"
    deferred = store.defer_capacity(
        record,
        queue_reason="subscription_rate_limited",
        retry_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert store.reroute_session("session-a", subscription_profile_id="team-b") == 0
    persisted = store.get("queued")
    assert persisted is not None
    assert persisted.queue_reason == "subscription_rate_limited"
    assert persisted.retry_not_before == deferred.retry_not_before


def _fifo_worker(
    *,
    sessions: dict[str, object],
    worker_urls: dict[str, str],
    model_provider_modes: dict[str, str] | None = None,
) -> object:
    provider_modes = model_provider_modes or {}
    return SimpleNamespace(
        provider_config_resolver=SimpleNamespace(
            resolve_for_session_id=lambda session_id: SimpleNamespace(
                container_worker_base_url=worker_urls[session_id],
                container_worker_codex_auth_mode="metered_api",
                model_provider_mode=provider_modes.get(session_id, "openai_public"),
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


def _fifo_session(
    session_id: str,
    *,
    expired: bool = False,
    profile_id: str | None = None,
    subscription: bool = False,
    agent_provider: str = "codex",
) -> object:
    return SimpleNamespace(
        session_id=session_id,
        directory_path="root",
        vm_provider="container_worker_vm",
        model="gpt-5.4",
        codex_auth_mode=(
            CodexAuthMode.CHATGPT_SUBSCRIPTION if subscription else CodexAuthMode.METERED_API
        ),
        subscription_profile_id=profile_id,
        metadata={"agent_provider": agent_provider},
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
        sessions={
            key: _fifo_session(
                key,
                profile_id=records[key].subscription_profile_id,
                subscription=True,
            )
            for key in records
        },
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


def test_model_capacity_queue_does_not_block_other_models_on_the_same_subscription():
    now = datetime.now(UTC)
    records = {
        "older": _record(queue_key="older", profile_id="team-a", created_at=now),
        "newer": _record(queue_key="newer", profile_id="team-a", created_at=now + timedelta(seconds=1)),
    }
    records["older"].model = "model-a"
    records["older"].queue_reason = "model_capacity"
    records["newer"].model = "model-b"
    worker = _fifo_worker(
        sessions={key: _fifo_session(key, profile_id="team-a", subscription=True) for key in records},
        worker_urls={key: "http://worker-a.test" for key in records},
    )
    hook = AgentEnqueuePromptQueueDispatchHook()
    assert not hook._has_earlier_subscription_profile_record(worker=worker, records=records, queue_key="newer")
    records["older"].queue_reason = "subscription_rate_limited"
    assert hook._has_earlier_subscription_profile_record(worker=worker, records=records, queue_key="newer")


def test_subscription_queue_derives_profile_for_prompts_queued_before_worker_preflight() -> None:
    now = datetime.now(UTC)
    records = {
        "older": _record(queue_key="older", profile_id=None, created_at=now),
        "newer": _record(
            queue_key="newer",
            profile_id="team-a",
            created_at=now + timedelta(seconds=1),
        ),
    }
    worker = _fifo_worker(
        sessions={
            "older": _fifo_session(
                "older",
                profile_id="team-a",
                subscription=True,
            ),
            "newer": _fifo_session("newer", profile_id="team-a", subscription=True),
        },
        worker_urls={key: "http://worker-a.test" for key in records},
    )

    hook = AgentEnqueuePromptQueueDispatchHook()
    assert hook._runnable_subscription_profile_context(
        worker=worker,
        record=records["older"],
    ) == ("team-a", "http://worker-a.test")
    assert hook._has_earlier_subscription_profile_record(
        worker=worker,
        records=records,
        queue_key="newer",
    )


def test_subscription_queue_derives_legacy_default_but_excludes_private_routes() -> None:
    now = datetime.now(UTC)
    records = {
        "legacy": _record(queue_key="legacy", profile_id=None, created_at=now),
        "local": _record(
            queue_key="local",
            profile_id="team-a",
            created_at=now + timedelta(seconds=1),
        ),
        "azure": _record(
            queue_key="azure",
            profile_id="team-a",
            created_at=now + timedelta(seconds=1),
        ),
        "claude": _record(
            queue_key="claude",
            profile_id="team-a",
            created_at=now + timedelta(seconds=1),
        ),
        "newer": _record(
            queue_key="newer",
            profile_id="default",
            created_at=now + timedelta(seconds=2),
        ),
    }
    worker = _fifo_worker(
        sessions={
            "legacy": _fifo_session("legacy", subscription=True),
            "local": _fifo_session("local", profile_id="team-a", subscription=True),
            "azure": _fifo_session("azure", profile_id="team-a", subscription=True),
            "claude": _fifo_session("claude", agent_provider="claude"),
            "newer": _fifo_session("newer", subscription=True),
        },
        worker_urls={key: "http://worker-a.test" for key in records},
        model_provider_modes={"local": "local_openai", "azure": "azure_openai"},
    )
    hook = AgentEnqueuePromptQueueDispatchHook()

    assert hook._runnable_subscription_profile_context(
        worker=worker,
        record=records["legacy"],
    ) == ("default", "http://worker-a.test")
    assert (
        hook._runnable_subscription_profile_context(
            worker=worker,
            record=records["local"],
        )
        is None
    )
    assert (
        hook._runnable_subscription_profile_context(
            worker=worker,
            record=records["azure"],
        )
        is None
    )
    assert (
        hook._runnable_subscription_profile_context(
            worker=worker,
            record=records["claude"],
        )
        is None
    )
    assert hook._has_earlier_subscription_profile_record(
        worker=worker,
        records=records,
        queue_key="newer",
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
            "other-worker": _fifo_session("other-worker", subscription=True),
            "expired-session": _fifo_session("expired-session", expired=True, subscription=True),
            "ready": _fifo_session("ready", subscription=True),
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
