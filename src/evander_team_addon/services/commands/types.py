from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..domain_helpers import (
    AgentRuntime,
    GovernanceRuntime,
    PersistentAgent,
    TeamOutboxRecord,
)


@dataclass(frozen=True)
class TeamCommandContext:
    ambient_session_id: str
    tool_name: str
    request_id: str | None = None
    caller_agent_id: str | None = None
    governance_group_id: str | None = None
    assignment_id: str | None = None
    active_dispatch_id: str | None = None
    wake_session_class: str = "team"
    correlation_id: str | None = None
    causation_id: str | None = None
    progress_classification: str = "none"


@dataclass(frozen=True)
class _ResolvedTeamCommandContext:
    context: TeamCommandContext
    caller_identity: str
    scope_kind: str
    scope_id: str
    agent: PersistentAgent | None = None
    runtime: AgentRuntime | GovernanceRuntime | None = None
    governance_group_id: str | None = None
    assignment_id: str | None = None
    dispatch_id: str | None = None


@dataclass(frozen=True)
class TeamCommandApplyResult:
    response_payload: dict[str, Any]
    created_domain_ids: list[str] = field(default_factory=list)
    created_event_ids: list[str] = field(default_factory=list)
    outbox_records: list[TeamOutboxRecord | Mapping[str, Any]] = field(default_factory=list)
    progress_classification: str | None = None


TeamDeliverableArtifactPublisher = Callable[..., Mapping[str, Any]]


class TeamCommandValidationError(ValueError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        correction_context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.correction_context = dict(correction_context or {})


__all__ = [
    "TeamCommandApplyResult",
    "TeamCommandContext",
    "TeamCommandValidationError",
    "TeamDeliverableArtifactPublisher",
    "_ResolvedTeamCommandContext",
]
