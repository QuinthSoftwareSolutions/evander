"""Operator-facing Team state payload assembly."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .activity_projections import (
    agent_activity_summaries_payload,
)
from .model_common import (
    RolePermission,
    TeamBlockerResolutionScope,
    TeamBlockerSourceKind,
    TeamBlockerState,
)
from .model_inbox_memory import (
    TeamMemoryRecord,
)
from .model_organization import (
    PersistentAgent,
)
from .model_queries import (
    _effective_stand_down_payloads,
    agent_lead_project_ids,
    agent_project_assignment_payloads,
    agent_project_ids,
    effective_role_permissions,
)
from .model_state import (
    TeamState,
)
from .model_tasks import (
    TaskAssessment,
    external_source_evidence_snapshot_payload,
)
from .release_projections import (
    release_package_payload_with_derived,
)
from .runtime_projections import (
    runtime_operations_payload,
)


def team_state_payload(
    state: TeamState,
    *,
    include_agent_avatars: bool = True,
    include_agent_voice_profiles: bool | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    task_assessments: Mapping[str, TaskAssessment] | None = None,
) -> dict[str, Any]:
    # Keep the model layer independent from domain services at import time while
    # still routing every task read through the canonical recursive evaluator.
    from .services.domain.task_assessment import (
        assess_tasks,
        task_payload_with_assessment,
    )

    if include_agent_voice_profiles is None:
        include_agent_voice_profiles = include_agent_avatars
    private_context_fields = {"notes", "case_context_views", "case_context_snapshots"}
    payload = state.model_dump(mode="json", exclude=private_context_fields)
    payload.update({field: {} for field in private_context_fields})
    payload["available_role_permissions"] = [permission.value for permission in RolePermission]
    payload["agents"] = {
        agent_id: _agent_payload_with_role(
            state,
            agent,
            include_avatar=include_agent_avatars,
            include_voice_profile=include_agent_voice_profiles,
        )
        for agent_id, agent in state.agents.items()
    }
    assessments = assess_tasks(state) if task_assessments is None else task_assessments
    payload["tasks"] = {
        task_id: task_payload_with_assessment(
            state,
            task,
            assessment=assessments[task_id],
            assessments=assessments,
        )
        for task_id, task in state.tasks.items()
    }
    payload["external_source_evidence_snapshots"] = {
        snapshot_id: external_source_evidence_snapshot_payload(snapshot, now=now)
        for snapshot_id, snapshot in state.external_source_evidence_snapshots.items()
    }
    payload["release_packages"] = {
        release_id: release_package_payload_with_derived(state, release)
        for release_id, release in state.release_packages.items()
    }
    payload["memories"] = {
        memory_id: _compact_memory_payload(memory) for memory_id, memory in state.memories.items()
    }
    for blocker in state.blockers.values():
        if blocker.state != TeamBlockerState.OPEN:
            continue
        collection_name = {
            TeamBlockerSourceKind.TASK: "tasks",
            TeamBlockerSourceKind.DELIVERABLE: "deliverables",
            TeamBlockerSourceKind.REVIEW_REQUEST: "review_requests",
            TeamBlockerSourceKind.RELEASE_PACKAGE: "release_packages",
            TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST: "human_review_requests",
            TeamBlockerSourceKind.AGENT_EXECUTION_STALL: "agents",
            TeamBlockerSourceKind.MCP_CAPABILITY: None,
            TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR: None,
            TeamBlockerSourceKind.EXPLICIT: None,
        }[blocker.source_kind]
        if collection_name is not None:
            source_payload = payload[collection_name].get(blocker.source_id)
            if isinstance(source_payload, dict):
                source_payload.setdefault("active_blocker_ids", [])
                if blocker.id not in source_payload["active_blocker_ids"]:
                    source_payload["active_blocker_ids"].append(blocker.id)
        if (
            blocker.task_id is not None
            and blocker.resolution_scope == TeamBlockerResolutionScope.TASK
        ):
            task_payload = payload["tasks"].get(blocker.task_id)
            if isinstance(task_payload, dict):
                task_payload.setdefault("active_blocker_ids", [])
                if blocker.id not in task_payload["active_blocker_ids"]:
                    task_payload["active_blocker_ids"].append(blocker.id)
    payload["runtime_operations"] = runtime_operations_payload(
        state,
        now=now,
        session_records=session_records,
        agent_execution_records=agent_execution_records,
    )
    payload["agent_activity_summaries"] = agent_activity_summaries_payload(
        state,
        runtime_operations=payload["runtime_operations"],
        now=now,
    )
    for agent_id, summary in payload["agent_activity_summaries"].items():
        agent_payload = payload["agents"].get(agent_id)
        if isinstance(agent_payload, dict):
            agent_payload["activity_summary"] = summary
    return payload


def _compact_memory_payload(memory: TeamMemoryRecord) -> dict[str, Any]:
    payload = memory.model_dump(mode="json")
    payload.pop("body", None)
    return payload


def _agent_payload_with_role(
    state: TeamState,
    agent: PersistentAgent,
    *,
    include_avatar: bool = True,
    include_voice_profile: bool = True,
) -> dict[str, Any]:
    exclude: set[str] = set()
    if not include_avatar:
        exclude.add("avatar")
    if not include_voice_profile:
        exclude.add("voice_profile")
    payload = agent.model_dump(mode="json", exclude=exclude)
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    effective_permissions = effective_role_permissions(state, agent)
    payload["role_name"] = role.name if role is not None else None
    payload["role_permissions"] = [permission.value for permission in effective_permissions]
    payload["effective_role_permissions"] = [
        permission.value for permission in effective_permissions
    ]
    payload["project_assignments"] = agent_project_assignment_payloads(state, agent.id)
    payload["project_ids"] = agent_project_ids(state, agent.id)
    payload["lead_project_ids"] = agent_lead_project_ids(state, agent.id)
    effective_stand_downs = _effective_stand_down_payloads(state, agent.id)
    payload["effective_stand_downs"] = effective_stand_downs
    payload["stand_down_active"] = bool(effective_stand_downs)
    return payload
