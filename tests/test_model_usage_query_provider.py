from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest

from agent_sandbox.events.provider_models import ModelUsageListQueryDefinition
from agent_sandbox.model_usage.models import ModelInferenceUsageEvent
from agent_sandbox.model_usage.store import ModelUsageStore
from agent_sandbox.provider_runtime.adapters import AdminRuntimeProviderAdapter
from agent_sandbox.queries.providers.base import QueryRuntimeContext
from agent_sandbox.queries.providers.model_usage_list import ModelUsageListQueryProvider


def _event(
    event_id: str,
    *,
    occurred_at: datetime,
    directory_path: str,
    provider: str = "openai",
    model: str = "gpt-5.2-codex",
    session_id: str = "session-1",
    workflow_run_id: str = "workflow-1",
    effect_id: str = "effect-1",
    dispatch_id: str = "dispatch-1",
    agent_harness: str = "codex",
    execution_backend: str = "libvirt_vm",
    request_status: str = "success",
    normalized_usage_status: str = "available",
    input_tokens: int | None = 10,
    output_tokens: int | None = 20,
    total_tokens: int | None = 30,
    cached_input_tokens: int | None = None,
) -> ModelInferenceUsageEvent:
    usage: dict[str, Any] = {"input_tokens": input_tokens}
    if cached_input_tokens is not None:
        usage["input_tokens_details"] = {"cached_tokens": cached_input_tokens}
    return ModelInferenceUsageEvent(
        event_id=event_id,
        occurred_at=occurred_at,
        provider=provider,
        provider_endpoint_identifier="responses",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        request_status=request_status,
        duration_ms=100,
        normalized_usage_status=normalized_usage_status,
        raw_usage_metadata={"usage": usage},
        session_id=session_id,
        workflow_run_id=workflow_run_id,
        effect_id=effect_id,
        node_id=effect_id,
        dispatch_id=dispatch_id,
        agent_harness=agent_harness,
        execution_backend=execution_backend,
        directory_path=directory_path,
        trace_id=f"trace-{event_id}",
        request_id=f"request-{event_id}",
    )


@dataclass
class _ModelUsageAdminServiceStub:
    store: ModelUsageStore
    calls: list[dict[str, Any]] = field(default_factory=list)

    def list_model_usage_events(self, **kwargs: Any) -> list[ModelInferenceUsageEvent]:
        self.calls.append(dict(kwargs))
        return self.store.list_events(**kwargs)


def test_provider_runtime_adapter_list_model_usage_events_delegates_to_model_usage_admin(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    occurred_at = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    store.upsert_event(_event("event-1", occurred_at=occurred_at, directory_path="root/team-a"))
    store.upsert_event(
        _event(
            "event-2",
            occurred_at=occurred_at + timedelta(minutes=1),
            directory_path="root/team-a/subteam",
        )
    )

    model_usage_admin = _ModelUsageAdminServiceStub(store)
    runtime_stub = SimpleNamespace(model_usage_admin=model_usage_admin)
    adapter = AdminRuntimeProviderAdapter(runtime_stub)
    events = adapter.list_model_usage_events(
        directory_path="root/team-a",
        include_directory_descendants=True,
        provider="openai",
        session_id="session-1",
        limit=1,
    )

    assert [event.event_id for event in events] == ["event-1"]
    assert events[0].directory_path == "root/team-a"
    assert model_usage_admin.calls == [
        {
            "session_id": "session-1",
            "directory_path": "root/team-a",
            "include_directory_descendants": True,
            "provider": "openai",
            "limit": 1,
        }
    ]


def test_model_usage_list_query_provider_filters_and_serializes_events(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    occurred_at = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    store.upsert_event(_event("event-1", occurred_at=occurred_at, directory_path="root/team-a"))
    store.upsert_event(
        _event(
            "event-2",
            occurred_at=occurred_at + timedelta(minutes=1),
            directory_path="root/team-a/subteam",
        )
    )
    store.upsert_event(
        _event(
            "event-3",
            occurred_at=occurred_at + timedelta(minutes=2),
            directory_path="root/team-b",
            provider="anthropic",
            model="claude-3-7-sonnet",
            request_status="failed",
            normalized_usage_status="usage_unavailable",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
        )
    )

    admin_service = _ModelUsageAdminServiceStub(store)
    provider = ModelUsageListQueryProvider()
    provider.initialize(
        ModelUsageListQueryDefinition(
            id="query-model-usage",
            display_name="Model usage",
            type="query:model-usage:list",
            session_id="session-1",
            workflow_run_id="workflow-1",
            provider="openai",
            model="gpt-5.2-codex",
            directory_path="root/team-a",
            include_directory_descendants=True,
            normalized_usage_status="available",
            request_status="success",
            occurred_after=(occurred_at - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            occurred_before=(occurred_at + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
            limit=2,
        ),
        QueryRuntimeContext(provider_config={}, admin_service=admin_service),
    )

    result = provider.execute()

    assert result["count"] == 2
    assert [item["event_id"] for item in result["items"]] == ["event-1", "event-2"]
    assert result["items"][0]["raw_usage_metadata"] == {"usage": {"input_tokens": 10}}
    assert result["items"][0]["session_id"] == "session-1"
    assert result["items"][0]["workflow_run_id"] == "workflow-1"
    assert result["items"][0]["directory_path"] == "root/team-a"
    assert result["items"][0]["provider"] == "openai"
    assert result["items"][0]["model"] == "gpt-5.2-codex"
    assert admin_service.calls == [
        {
            "session_id": "session-1",
            "workflow_run_id": "workflow-1",
            "effect_id": None,
            "dispatch_id": None,
            "directory_path": "root/team-a",
            "include_directory_descendants": True,
            "provider": "openai",
            "model": "gpt-5.2-codex",
            "agent_harness": None,
            "execution_backend": None,
            "normalized_usage_status": "available",
            "request_status": "success",
            "occurred_after": occurred_at - timedelta(minutes=1),
            "occurred_before": occurred_at + timedelta(minutes=2),
            "limit": 2,
        }
    ]


def test_model_usage_list_query_provider_respects_limit(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    occurred_at = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    store.upsert_event(_event("event-1", occurred_at=occurred_at, directory_path="root"))
    store.upsert_event(
        _event("event-2", occurred_at=occurred_at + timedelta(minutes=1), directory_path="root")
    )

    admin_service = _ModelUsageAdminServiceStub(store)
    provider = ModelUsageListQueryProvider()
    provider.initialize(
        ModelUsageListQueryDefinition(
            id="query-model-usage-limit",
            display_name="Model usage limit",
            type="query:model-usage:list",
            limit=1,
        ),
        QueryRuntimeContext(provider_config={}, admin_service=admin_service),
    )

    result = provider.execute()

    assert result["count"] == 1
    assert [item["event_id"] for item in result["items"]] == ["event-1"]


def test_model_usage_list_query_provider_adds_cost_estimates_and_totals(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ModelUsageStore(runtime_dir / "model-usage.db")
    occurred_at = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    store.upsert_event(
        _event(
            "event-1",
            occurred_at=occurred_at,
            directory_path="root",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            cached_input_tokens=200,
        )
    )
    store.upsert_event(
        _event(
            "event-2",
            occurred_at=occurred_at + timedelta(minutes=1),
            directory_path="root",
            provider="anthropic",
            model="claude-3-7-sonnet",
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
        )
    )

    admin_service = _ModelUsageAdminServiceStub(store)
    provider = ModelUsageListQueryProvider()
    provider.initialize(
        ModelUsageListQueryDefinition(
            id="query-model-usage-costs",
            display_name="Model usage costs",
            type="query:model-usage:list",
        ),
        QueryRuntimeContext(provider_config={}, admin_service=admin_service),
    )

    result = provider.execute()

    assert result["count"] == 2
    assert result["total_input_tokens"] == 1010
    assert result["total_cached_input_tokens"] == 200
    assert result["total_cache_write_input_tokens"] == 0
    assert result["total_output_tokens"] == 520
    assert result["total_tokens"] == 1530
    assert result["costs_estimated_count"] == 1
    assert result["costs_unavailable_count"] == 1
    assert result["total_estimated_cost"] == pytest.approx(0.008435)
    assert result["items"][0]["estimated_cost"] == pytest.approx(0.008435)
    assert result["items"][0]["estimated_input_cost"] == pytest.approx(0.0014)
    assert result["items"][0]["estimated_cached_input_cost"] == pytest.approx(0.000035)
    assert result["items"][0]["estimated_cache_write_cost"] == pytest.approx(0)
    assert result["items"][0]["estimated_output_cost"] == pytest.approx(0.007)
    assert result["items"][0]["cached_input_tokens"] == 200
    assert result["items"][0]["cache_write_input_tokens"] == 0
    assert result["items"][0]["cost_status"] == "estimated"
    assert result["items"][0]["pricing_rule_id"] == "openai:gpt-5.2-codex:text:2025-12-11"
    assert result["items"][0]["pricing_version"]
    assert result["items"][1]["cost_status"] == "pricing_unavailable"
