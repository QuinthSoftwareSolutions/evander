"""Provider queries."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from typing import (
    Any,
)

from evander.events.models import (
    WorkflowNodeEnvelope,
)
from evander.queries.providers.base import (
    QueryProviderBase,
)

from .models import (
    AgentStatus,
)
from .provider_events import (
    _materialize,
)
from .provider_projection import (
    _filtered_team_state_payload,
)
from .provider_query_projection import (
    compact_team_state_payload,
    query_projection_collections,
    validate_record_fields,
)
from .provider_runtime import (
    _session_records_by_id,
    _session_repository_from_context,
)
from .provider_schemas import (
    TeamSessionRoutingQueryDefinition,
    TeamStateQueryDefinition,
)
from .provider_values import (
    _definition_payload,
    _optional_text,
    _required_text,
    _runtime_dir,
)
from .repository import (
    TeamStateRepository,
)


class TeamStateQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(TeamStateQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.query_definition,
            "team_id",
            "project_id",
            "agent_id",
            "include_records",
            "record_fields",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        team_id = _optional_text(payload.get("team_id"))
        project_id = _optional_text(payload.get("project_id"))
        agent_id = _optional_text(payload.get("agent_id"))
        record_fields = validate_record_fields(payload.get("record_fields"))
        repository = TeamStateRepository(_runtime_dir(self.runtime_context))
        if record_fields is None:
            state = repository.read()
            session_records = _session_records_by_id(
                _session_repository_from_context(self.runtime_context)
            )
            filtered = _filtered_team_state_payload(
                state, team_id=team_id, project_id=project_id, agent_id=agent_id,
                session_records=session_records,
            )
        else:
            state = repository.read_projection(
                collections=sorted(query_projection_collections(record_fields, agent_id=agent_id))
            )
            filtered = compact_team_state_payload(
                state, record_fields=record_fields, team_id=team_id,
                project_id=project_id, agent_id=agent_id,
            )
        return {
            "success": True,
            "revision": state.revision,
            "state": filtered,
            "teams": filtered.get("teams", {}),
            "projects": filtered.get("projects", {}),
            "agents": filtered.get("agents", {}),
            "cases": filtered.get("cases", {}),
            "tasks": filtered.get("tasks", {}),
            "external_source_evidence_snapshots": filtered.get("external_source_evidence_snapshots", {}),
        }


class TeamSessionRoutingQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(TeamSessionRoutingQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(self.query_definition, "session_id", "dispatch_id")
        if resolved_config:
            payload.update(dict(resolved_config))
        session_id = _required_text(payload.get("session_id"), "session_id")
        dispatch_id = _optional_text(payload.get("dispatch_id"))
        state = TeamStateRepository(_runtime_dir(self.runtime_context)).read()
        for agent in state.agents.values():
            if agent.session_id == session_id:
                runtime = state.agent_runtimes.get(agent.id)
                expected_dispatch_id = (
                    _optional_text(runtime.active_dispatch_id) if runtime is not None else None
                )
                if agent.status == AgentStatus.ARCHIVED or agent.team_id is None:
                    return {
                        "success": True,
                        "session_id": session_id,
                        "dispatch_id": dispatch_id,
                        "kind": "ignored",
                        "agent_id": agent.id,
                        "assignment_id": None,
                        "persistent": False,
                        "ephemeral": False,
                        "ignored": True,
                        "reason": "agent is archived or retired from its team",
                        "expected_dispatch_id": expected_dispatch_id,
                    }
                dispatch_mismatch = (
                    expected_dispatch_id is not None and dispatch_id != expected_dispatch_id
                ) or (expected_dispatch_id is None and dispatch_id is not None)
                if dispatch_mismatch:
                    return {
                        "success": True,
                        "session_id": session_id,
                        "dispatch_id": dispatch_id,
                        "kind": "ignored",
                        "agent_id": agent.id,
                        "assignment_id": None,
                        "persistent": False,
                        "ephemeral": False,
                        "ignored": True,
                        "reason": "dispatch_id does not match active team runtime lease",
                        "expected_dispatch_id": expected_dispatch_id,
                    }
                return {
                    "success": True,
                    "session_id": session_id,
                    "dispatch_id": dispatch_id,
                    "kind": "persistent",
                    "agent_id": agent.id,
                    "assignment_id": None,
                    "persistent": True,
                    "ephemeral": False,
                    "ignored": False,
                    "reason": None,
                    "expected_dispatch_id": expected_dispatch_id,
                }
        for assignment in state.ephemeral_assignments.values():
            if assignment.session_id == session_id:
                return {
                    "success": True,
                    "session_id": session_id,
                    "dispatch_id": dispatch_id,
                    "kind": "ephemeral",
                    "agent_id": None,
                    "assignment_id": assignment.id,
                    "persistent": False,
                    "ephemeral": True,
                    "ignored": False,
                    "reason": None,
                    "expected_dispatch_id": None,
                }
        return {
            "success": False,
            "session_id": session_id,
            "dispatch_id": dispatch_id,
            "kind": "unknown",
            "agent_id": None,
            "assignment_id": None,
            "persistent": False,
            "ephemeral": False,
            "ignored": False,
            "reason": None,
            "expected_dispatch_id": None,
        }
