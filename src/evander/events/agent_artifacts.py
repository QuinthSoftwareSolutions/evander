"""Stable internal-event identifiers for agent-staged artifacts."""

from __future__ import annotations

from evander.events.internal import InternalEvent

AGENT_ARTIFACT_STAGED_EVENT_TYPE = "agent.artifact.staged"
AGENT_ARTIFACT_STAGED_EVENT_SOURCE_TYPE = "gateway.agent_artifact_stage"


def is_agent_artifact_staged_event(event: InternalEvent) -> bool:
    """Return whether an event was produced by the trusted artifact-stage tool path."""

    return (
        event.event_type == AGENT_ARTIFACT_STAGED_EVENT_TYPE
        and event.source is not None
        and event.source.source_type == AGENT_ARTIFACT_STAGED_EVENT_SOURCE_TYPE
    )
