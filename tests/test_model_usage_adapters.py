from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from evander.model_usage.adapters import (
    MappingModelUsageExtractor,
    ModelUsageExtractionRequest,
)
from evander.model_usage.models import ModelUsageCorrelationContext


def test_mapping_model_usage_extractor_normalizes_provider_neutral_usage() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    context = ModelUsageCorrelationContext(
        session_id="session-1",
        dispatch_id="dispatch-1",
        workflow_run_id="workflow-1",
        effect_id="effect-1",
        node_id="node-1",
        agent_harness="codex",
        execution_backend="libvirt_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
        trace_id="trace-1",
        started_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    extractor = MappingModelUsageExtractor(provider_name="anthropic")
    request = ModelUsageExtractionRequest(
        provider="anthropic",
        provider_endpoint_identifier="messages",
        request_status="success",
        occurred_at=now,
        duration_ms=120,
        model="claude-3-7-sonnet",
        request_body_bytes=512,
        request_body_sha256="req-sha",
        response_body_bytes=2048,
        response_body_sha256="resp-sha",
        response_payload={
            "id": "resp-1",
            "type": "message",
            "model": "claude-3-7-sonnet",
            "prompt": "secret prompt text",
            "output_text": "secret completion text",
            "usage": {
                "input_tokens": 11,
                "output_tokens": 22,
                "total_tokens": 33,
            },
        },
        request_metadata={"body_bytes": 512},
        response_metadata={"status_code": 200},
        correlation_context=context,
        trace_id="override-trace",
        request_id="req-1",
    )

    result = extractor.extract(request)

    assert result.event.event_id == "resp-1"
    assert result.event.provider == "anthropic"
    assert result.event.provider_endpoint_identifier == "messages"
    assert result.event.model == "claude-3-7-sonnet"
    assert result.event.input_tokens == 11
    assert result.event.output_tokens == 22
    assert result.event.total_tokens == 33
    assert result.event.normalized_usage_status == "available"
    assert result.event.raw_usage_metadata == {
        "provider_response_kind": "message",
        "usage": {
            "input_tokens": 11,
            "output_tokens": 22,
            "total_tokens": 33,
        },
        "response_metadata": {"status_code": 200},
        "request_metadata": {"body_bytes": 512},
    }
    assert result.event.request_body_sha256 == "req-sha"
    assert result.event.response_body_sha256 == "resp-sha"
    assert result.event.session_id == "session-1"
    assert result.event.dispatch_id == "dispatch-1"
    assert result.event.workflow_run_id == "workflow-1"
    assert result.event.effect_id == "effect-1"
    assert result.event.node_id == "node-1"
    assert result.event.execution_backend == "libvirt_vm"
    assert result.event.directory_path == "root/team-a"
    assert result.event.trace_id == "override-trace"
    assert result.event.request_id == "req-1"
    assert result.event.model_dump(mode="json")["occurred_at"].endswith("Z")
    assert "secret prompt text" not in json.dumps(
        result.event.model_dump(mode="json"), sort_keys=True
    )
    assert "secret completion text" not in json.dumps(
        result.event.model_dump(mode="json"), sort_keys=True
    )


def test_mapping_model_usage_extractor_marks_missing_usage_as_unavailable() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    extractor = MappingModelUsageExtractor(provider_name="fake-provider")
    request = ModelUsageExtractionRequest(
        provider="fake-provider",
        provider_endpoint_identifier="completions",
        request_status="failed",
        occurred_at=now,
        duration_ms=5,
        response_payload={
            "id": "resp-2",
            "type": "completion",
            "model": "fake-model",
        },
    )

    result = extractor.extract(request)

    assert result.event.normalized_usage_status == "usage_unavailable"
    assert result.event.input_tokens is None
    assert result.event.output_tokens is None
    assert result.event.total_tokens is None
    assert result.event.raw_usage_metadata == {
        "provider_response_kind": "completion",
        "usage": {},
        "response_metadata": {},
        "request_metadata": {},
    }


def test_mapping_model_usage_extractor_persists_context_attribution() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    context = ModelUsageCorrelationContext(
        session_id="session-1",
        dispatch_id="dispatch-1",
        agent_harness="codex",
        team_id="team-a",
        project_id="project-a",
        agent_id="agent-a",
        started_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    extractor = MappingModelUsageExtractor(provider_name="openai")
    request = ModelUsageExtractionRequest(
        provider="openai",
        provider_endpoint_identifier="responses",
        request_status="success",
        occurred_at=now,
        response_payload={
            "id": "resp-attribution",
            "type": "response",
            "model": "gpt-5.2-codex",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
            },
        },
        request_metadata={"method": "POST"},
        correlation_context=context,
    )

    result = extractor.extract(request)

    assert result.event.raw_usage_metadata["request_metadata"] == {
        "method": "POST",
        "team_id": "team-a",
        "project_id": "project-a",
        "agent_id": "agent-a",
    }


def test_mapping_model_usage_extractor_uses_context_model_when_response_omits_model() -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    context = ModelUsageCorrelationContext(
        session_id="session-1",
        dispatch_id="dispatch-1",
        workflow_run_id="workflow-1",
        effect_id="effect-1",
        node_id="node-1",
        agent_harness="codex",
        execution_backend="container_worker_vm",
        directory_path="root/team-a",
        model="gpt-5.4-mini",
        trace_id="trace-1",
        started_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    extractor = MappingModelUsageExtractor(provider_name="openai")
    request = ModelUsageExtractionRequest(
        provider="openai",
        provider_endpoint_identifier="responses/compact",
        request_status="success",
        occurred_at=now,
        response_payload={
            "id": "resp-compact",
            "type": "response",
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        },
        correlation_context=context,
    )

    result = extractor.extract(request)

    assert result.event.model == "gpt-5.4-mini"
    assert result.event.provider_endpoint_identifier == "responses/compact"
    assert result.event.dispatch_id == "dispatch-1"
    assert result.event.total_tokens == 5
