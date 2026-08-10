from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evander.model_usage.models import (
    ModelInferenceUsageEvent,
    ModelUsageCorrelationContext,
)
from evander.model_usage.store import ModelUsageStore, model_usage_store_db_path


def _store(tmp_path: Path) -> ModelUsageStore:
    return ModelUsageStore(tmp_path / "runtime" / "model-usage.json")


def test_model_usage_store_uses_sqlite_and_indexes(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.path == tmp_path / "runtime" / "model-usage.db"
    assert model_usage_store_db_path(tmp_path / "runtime" / "model-usage.json") == store.path

    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' ORDER BY name"
            )
        }

    assert {"model_usage_events", "model_usage_correlation_contexts"} <= tables
    assert {
        "idx_model_usage_events_occurred_at",
        "idx_model_usage_events_session_id",
        "idx_model_usage_events_workflow_run_id",
        "idx_model_usage_events_directory_path",
        "idx_model_usage_events_provider",
        "idx_model_usage_events_model",
        "idx_model_usage_events_agent_harness",
        "idx_model_usage_events_dispatch_id",
    } <= indexes


def test_model_usage_store_upserts_queries_and_filters_events(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event_time = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    second_time = event_time + timedelta(minutes=5)
    shared_kwargs = dict(
        provider="openai",
        provider_endpoint_identifier="responses",
        model="gpt-5.2-codex",
        request_status="success",
        normalized_usage_status="available",
        session_id="session-1",
        workflow_run_id="workflow-1",
        effect_id="effect-1",
        node_id="node-1",
        dispatch_id="dispatch-1",
        agent_harness="codex",
        execution_backend="libvirt_vm",
        directory_path="root/team-a",
        trace_id="trace-1",
    )

    store.upsert_event(
        ModelInferenceUsageEvent(
            event_id="event-1",
            occurred_at=event_time,
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            duration_ms=250,
            raw_usage_metadata={"provider": "openai", "usage": {"input_tokens": 10}},
            request_body_bytes=1024,
            request_body_sha256="abc123",
            response_body_bytes=2048,
            response_body_sha256="def456",
            request_id="req-1",
            **shared_kwargs,
        )
    )
    store.upsert_event(
        ModelInferenceUsageEvent(
            event_id="event-1",
            occurred_at=second_time,
            input_tokens=11,
            output_tokens=21,
            total_tokens=32,
            duration_ms=300,
            raw_usage_metadata={"provider": "openai", "usage": {"input_tokens": 11}},
            request_body_bytes=1024,
            request_body_sha256="abc123",
            response_body_bytes=2048,
            response_body_sha256="def456",
            request_id="req-1",
            **shared_kwargs,
        )
    )
    store.upsert_event(
        ModelInferenceUsageEvent(
            event_id="event-2",
            occurred_at=second_time,
            provider="anthropic",
            provider_endpoint_identifier="messages",
            model="claude-3-7-sonnet",
            request_status="failed",
            normalized_usage_status="usage_unavailable",
            raw_usage_metadata={"provider": "anthropic", "usage": {}},
            session_id="session-1",
            workflow_run_id="workflow-1",
            effect_id="effect-2",
            node_id="node-2",
            dispatch_id="dispatch-2",
            agent_harness="python",
            execution_backend="container_worker_vm",
            directory_path="root/team-a/subteam",
            trace_id="trace-2",
        )
    )

    all_events = store.list_events(session_id="session-1")
    assert [event.event_id for event in all_events] == ["event-1", "event-2"]
    assert all_events[0].occurred_at == second_time
    assert all_events[0].input_tokens == 11
    assert store.get_event("event-1") is not None
    assert store.get_event("event-1").occurred_at == second_time
    assert store.get_event("event-1").input_tokens == 11

    filtered = store.list_events(
        session_id="session-1",
        provider="openai",
        model="gpt-5.2-codex",
        occurred_after=second_time - timedelta(seconds=1),
        occurred_before=second_time + timedelta(seconds=1),
    )
    assert [event.event_id for event in filtered] == ["event-1"]
    assert filtered[0].request_body_sha256 == "abc123"
    assert filtered[0].raw_usage_metadata == {"provider": "openai", "usage": {"input_tokens": 11}}

    descendant_events = store.list_events(
        directory_path="root/team-a",
        include_directory_descendants=True,
    )
    assert [event.event_id for event in descendant_events] == ["event-1", "event-2"]
    assert store.list_events(directory_path="root/team-a") == [store.get_event("event-1")]
    assert store.list_events(limit=1)[0].event_id == "event-1"


def test_model_usage_store_sum_input_tokens_preserves_unknown_usage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    boundary = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)

    def _record(
        *,
        event_id: str,
        session_id: str,
        occurred_at: datetime,
        input_tokens: int | None,
    ) -> None:
        store.upsert_event(
            ModelInferenceUsageEvent(
                event_id=event_id,
                occurred_at=occurred_at,
                provider="openai",
                provider_endpoint_identifier="responses",
                input_tokens=input_tokens,
                request_status="success",
                normalized_usage_status=(
                    "available" if input_tokens is not None else "usage_unavailable"
                ),
                raw_usage_metadata={},
                session_id=session_id,
            )
        )

    assert store.sum_input_tokens(session_id="empty", occurred_after=boundary) == 0
    _record(
        event_id="known",
        session_id="known",
        occurred_at=boundary + timedelta(seconds=1),
        input_tokens=7,
    )
    _record(
        event_id="mixed-known",
        session_id="mixed",
        occurred_at=boundary + timedelta(seconds=1),
        input_tokens=11,
    )
    _record(
        event_id="mixed-unknown",
        session_id="mixed",
        occurred_at=boundary + timedelta(seconds=2),
        input_tokens=None,
    )
    _record(
        event_id="unknown",
        session_id="unknown",
        occurred_at=boundary + timedelta(seconds=1),
        input_tokens=None,
    )

    assert store.sum_input_tokens(session_id="known", occurred_after=boundary) == 7
    assert store.sum_input_tokens(session_id="mixed", occurred_after=boundary) is None
    assert store.sum_input_tokens(session_id="unknown", occurred_after=boundary) is None


def test_model_usage_store_manages_expiring_correlation_contexts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    active = ModelUsageCorrelationContext(
        session_id="session-1",
        dispatch_id="dispatch-1",
        workflow_run_id="workflow-1",
        effect_id="effect-1",
        node_id="node-1",
        agent_harness="codex",
        execution_backend="libvirt_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
        team_id="team-a",
        project_id="project-a",
        agent_id="agent-a",
        trace_id="trace-1",
        started_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    expired = ModelUsageCorrelationContext(
        session_id="session-1",
        dispatch_id="dispatch-2",
        workflow_run_id="workflow-1",
        effect_id="effect-2",
        node_id="node-2",
        agent_harness="codex",
        execution_backend="libvirt_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
        trace_id="trace-2",
        started_at=now - timedelta(minutes=30),
        expires_at=now - timedelta(minutes=1),
    )

    store.upsert_correlation_context(active)
    store.upsert_correlation_context(expired)

    lookup = store.get_active_correlation_context(
        session_id="session-1",
        agent_harness="codex",
        execution_backend="libvirt_vm",
        now_utc=now,
    )
    assert lookup is not None
    assert lookup.dispatch_id == "dispatch-1"
    assert lookup.model == "gpt-5.2-codex"
    assert lookup.team_id == "team-a"
    assert lookup.project_id == "project-a"
    assert lookup.agent_id == "agent-a"
    direct_lookup = store.get_correlation_context("dispatch-1")
    assert direct_lookup is not None
    assert direct_lookup.team_id == "team-a"

    assert (
        store.get_active_correlation_context(
            session_id="session-1",
            agent_harness="codex",
            execution_backend="libvirt_vm",
            dispatch_id="dispatch-2",
            now_utc=now,
        )
        is None
    )

    cleared = store.clear_correlation_context(dispatch_id="dispatch-1")
    assert cleared == 1
    assert (
        store.get_active_correlation_context(
            session_id="session-1",
            agent_harness="codex",
            execution_backend="libvirt_vm",
            now_utc=now,
        )
        is None
    )

    assert store.prune_expired_correlation_contexts(now_utc=now) == 1


def test_model_usage_store_upsert_preserves_existing_team_attribution(tmp_path: Path) -> None:
    store = _store(tmp_path)
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    store.upsert_correlation_context(
        ModelUsageCorrelationContext(
            session_id="session-1",
            dispatch_id="team-dispatch-1",
            workflow_run_id="workflow-1",
            effect_id="team-wake",
            node_id="team-wake",
            agent_harness="codex",
            team_id="team-a",
            project_id="project-a",
            agent_id="agent-a",
            started_at=now,
            expires_at=now + timedelta(minutes=15),
        )
    )

    store.upsert_correlation_context(
        ModelUsageCorrelationContext(
            session_id="session-1",
            dispatch_id="team-dispatch-1",
            workflow_run_id="workflow-1",
            effect_id="generic-agent-prompt",
            node_id="generic-agent-prompt",
            agent_harness="codex",
            execution_backend="libvirt_vm",
            model="gpt-5.2-codex",
            started_at=now + timedelta(seconds=1),
            expires_at=now + timedelta(minutes=16),
        )
    )

    merged = store.get_correlation_context("team-dispatch-1")
    assert merged is not None
    assert merged.effect_id == "generic-agent-prompt"
    assert merged.execution_backend == "libvirt_vm"
    assert merged.model == "gpt-5.2-codex"
    assert merged.team_id == "team-a"
    assert merged.project_id == "project-a"
    assert merged.agent_id == "agent-a"
