from __future__ import annotations

from pathlib import Path

import pytest

from evander.config import GatewayConfig
from evander.daemons.trigger_orchestrator import TriggerOrchestratorWorker
from evander.events.agent_artifacts import (
    AGENT_ARTIFACT_STAGED_EVENT_SOURCE_TYPE,
    AGENT_ARTIFACT_STAGED_EVENT_TYPE,
)
from evander.events.internal import InternalEventBus, InternalEventSource
from evander.events.provider_models import AgentArtifactStagedTriggerDefinition
from evander.triggers.providers.agent_artifact_staged import AgentArtifactStagedTriggerProvider
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.queue import SqliteTriggerEventQueue

from config_helpers import canonical_worker_config


def _trigger(*, artifact_path: str | None = None, path_prefix: str | None = None):
    return AgentArtifactStagedTriggerDefinition(
        id="artifact-staged",
        display_name="Artifact staged",
        type="trigger:agent:artifact-staged",
        artifact_path=artifact_path,
        path_prefix=path_prefix,
    )


def _publish_staged_event(bus: InternalEventBus, *, artifact_path: str) -> None:
    bus.publish(
        event_type=AGENT_ARTIFACT_STAGED_EVENT_TYPE,
        payload={"session_id": "session-1", "artifact_path": artifact_path, "byte_count": 42},
        correlation_key="session-1",
        source=InternalEventSource(
            source_type=AGENT_ARTIFACT_STAGED_EVENT_SOURCE_TYPE,
            source_name="stage_artifact",
        ),
    )


def test_agent_artifact_staged_trigger_accepts_only_trusted_exact_path(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    bus = InternalEventBus(runtime_dir / "internal-events.json")
    _publish_staged_event(bus, artifact_path="handoffs/support-ticket.json")
    _publish_staged_event(bus, artifact_path="handoffs/diagnostics.json")
    bus.publish(
        event_type=AGENT_ARTIFACT_STAGED_EVENT_TYPE,
        payload={"session_id": "session-untrusted", "artifact_path": "handoffs/support-ticket.json"},
        source=InternalEventSource(source_type="workflow", source_name="event-emit"),
    )

    provider = AgentArtifactStagedTriggerProvider()
    provider.initialize(
        _trigger(artifact_path=" handoffs/support-ticket.json "),
        TriggerRuntimeContext(resolved_config=GatewayConfig(runtime_dir=runtime_dir), env={}),
    )

    events = provider.poll()

    assert len(events) == 1
    assert events[0].trigger_type == "trigger:agent:artifact-staged"
    assert events[0].payload["payload"]["session_id"] == "session-1"
    assert events[0].payload["payload"]["artifact_path"] == "handoffs/support-ticket.json"
    provider.acknowledge_published_event(events[0])
    assert provider.poll() == []


def test_agent_artifact_staged_trigger_matches_path_prefix_boundary(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    bus = InternalEventBus(runtime_dir / "internal-events.json")
    _publish_staged_event(bus, artifact_path="handoffs/a.json")
    _publish_staged_event(bus, artifact_path="handoffs-archive/a.json")

    provider = AgentArtifactStagedTriggerProvider()
    provider.initialize(
        _trigger(path_prefix="handoffs"),
        TriggerRuntimeContext(resolved_config=GatewayConfig(runtime_dir=runtime_dir), env={}),
    )

    events = provider.poll()

    assert [event.payload["payload"]["artifact_path"] for event in events] == ["handoffs/a.json"]


def test_agent_artifact_staged_trigger_rejects_unsafe_or_ambiguous_path_filter() -> None:
    with pytest.raises(ValueError, match="artifact path must not contain"):
        _trigger(artifact_path="../support-ticket.json")
    with pytest.raises(ValueError, match="mutually exclusive"):
        _trigger(artifact_path="handoffs/ticket.json", path_prefix="handoffs")


def test_agent_artifact_staged_trigger_publishes_through_orchestrator(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "ticket-handoff",
                    "display_name": "Ticket handoff staged",
                    "type": "trigger:agent:artifact-staged",
                    "artifact_path": "handoffs/support-ticket.json",
                }
            ],
        }
    )
    bus = InternalEventBus(worker_config.internal_event_store_path)
    _publish_staged_event(bus, artifact_path="handoffs/support-ticket.json")
    worker = TriggerOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]

    result = worker.run_once()
    queue = SqliteTriggerEventQueue(worker_config.orchestration_store_path)
    records = queue.list_pending()

    assert result.events_published == 1
    assert len(records) == 1
    assert records[0].trigger_type == "trigger:agent:artifact-staged"
    assert records[0].payload["payload"]["artifact_path"] == "handoffs/support-ticket.json"
