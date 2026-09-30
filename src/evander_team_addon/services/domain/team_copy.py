from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, TypedDict

from ...models import (
    AgentProjectAssignment,
    AgentStatus,
    OutcomeKindDefinition,
    PersistentAgent,
    Project,
    ProjectStatus,
    ReviewProfile,
    Team,
    TeamRole,
    TeamState,
)
from .common import _require_present, _required_text


class TeamCopySourceConfiguration(TypedDict):
    team: Team
    projects: dict[str, Project]
    ordered_project_ids: list[str]
    roles: dict[str, TeamRole]
    agents: dict[str, PersistentAgent]
    outcome_kinds: dict[str, OutcomeKindDefinition]
    review_profiles: dict[str, ReviewProfile]
    assignments: dict[str, AgentProjectAssignment]


def _team_copy_source_configuration(
    state: TeamState,
    source_team_id: str,
) -> TeamCopySourceConfiguration:
    source_team = _require_present(state.teams, source_team_id, "source team")
    if source_team.archived:
        raise ValueError("archived source teams cannot be copied")
    included_projects = {
        project.id: project
        for project in state.projects.values()
        if project.team_id == source_team.id
        and project.status in {ProjectStatus.ACTIVE, ProjectStatus.BLOCKED}
    }
    ordered_included_project_ids = [
        project_id for project_id in source_team.project_ids if project_id in included_projects
    ]
    ordered_included_project_ids.extend(
        sorted(set(included_projects) - set(ordered_included_project_ids))
    )
    included_roles = {
        role.id: role
        for role in state.roles.values()
        if role.team_id == source_team.id
        and not role.archived
        and (role.project_id is None or role.project_id in included_projects)
    }
    included_agents = {
        agent.id: agent
        for agent in state.agents.values()
        if agent.team_id == source_team.id
        and agent.status != AgentStatus.ARCHIVED
        and agent.role_id in included_roles
    }
    included_outcome_kinds = {
        definition.id: definition
        for definition in state.outcome_kind_definitions.values()
        if definition.team_id == source_team.id
        and not definition.archived
        and (definition.project_id is None or definition.project_id in included_projects)
    }
    included_review_profiles = {
        profile.id: profile
        for profile in state.review_profiles.values()
        if profile.team_id == source_team.id
        and not profile.archived
        and (profile.project_id is None or profile.project_id in included_projects)
    }

    def require_included(
        record_id: str | None,
        collection: Mapping[str, Any],
        label: str,
    ) -> None:
        if record_id is not None and record_id not in collection:
            raise ValueError(
                f"source team current configuration depends on excluded {label} {record_id}"
            )

    require_included(source_team.manager_agent_id, included_agents, "agent")
    for project in included_projects.values():
        require_included(project.lead_agent_id, included_agents, "agent")
        if project.release_policy is not None:
            for requirement in project.release_policy.review_requirements:
                require_included(requirement.reviewer_agent_id, included_agents, "agent")
                require_included(
                    requirement.review_profile_id,
                    included_review_profiles,
                    "review profile",
                )
    for agent in included_agents.values():
        require_included(agent.role_id, included_roles, "role")
        require_included(agent.reports_to_agent_id, included_agents, "superior agent")
    for profile in included_review_profiles.values():
        for role_id in profile.applicable_role_ids:
            require_included(role_id, included_roles, "role")

    included_assignments = {
        assignment.id: assignment
        for assignment in state.project_assignments.values()
        if assignment.team_id == source_team.id
        and assignment.project_id in included_projects
        and assignment.agent_id in included_agents
        and (assignment.role_id is None or assignment.role_id in included_roles)
    }
    for assignment in included_assignments.values():
        require_included(assignment.agent_id, included_agents, "agent")
        require_included(assignment.role_id, included_roles, "role")

    return {
        "team": source_team,
        "projects": included_projects,
        "ordered_project_ids": ordered_included_project_ids,
        "roles": included_roles,
        "agents": included_agents,
        "outcome_kinds": included_outcome_kinds,
        "review_profiles": included_review_profiles,
        "assignments": included_assignments,
    }


def team_copy_configuration_token(state: TeamState, source_team_id: str) -> str:
    """Fingerprint only the stable source graph that a clean Team copy consumes."""

    configuration = _team_copy_source_configuration(state, source_team_id)
    source_team: Team = configuration["team"]
    team_payload = source_team.model_dump(
        mode="json",
        exclude={
            "cost_budget_windows",
            "created_at",
            "updated_at",
            "escalate_to_governance",
            "project_ids",
        },
    )
    project_payloads: list[dict[str, Any]] = []
    for project in sorted(configuration["projects"].values(), key=lambda item: item.id):
        project_payload = project.model_dump(
            mode="json",
            exclude={"created_at", "updated_at", "goals", "status"},
        )
        project_payload["goals"] = [{"description": goal.description} for goal in project.goals]
        project_payloads.append(project_payload)

    def stable_records(collection: str) -> list[dict[str, Any]]:
        return [
            record.model_dump(
                mode="json",
                exclude={"created_at", "updated_at", "archived"},
            )
            for record in sorted(
                configuration[collection].values(),
                key=lambda item: item.id,
            )
        ]

    included_agent_ids = set(configuration["agents"])
    agent_payloads: list[dict[str, Any]] = []
    for agent in sorted(configuration["agents"].values(), key=lambda item: item.id):
        agent_payload = agent.model_dump(
            mode="json",
            exclude={"status", "retired_team_id", "subordinate_agent_ids"},
        )
        agent_payload["subordinate_agent_ids"] = [
            subordinate_id
            for subordinate_id in agent.subordinate_agent_ids
            if subordinate_id in included_agent_ids
        ]
        agent_payloads.append(agent_payload)
    assignment_payloads = [
        assignment.model_dump(
            mode="json",
            exclude={"id", "created_at", "updated_at"},
        )
        for assignment in sorted(
            configuration["assignments"].values(),
            key=lambda item: item.id,
        )
    ]
    payload = {
        "team": team_payload,
        "ordered_project_ids": configuration["ordered_project_ids"],
        "projects": project_payloads,
        "roles": stable_records("roles"),
        "agents": agent_payloads,
        "assignments": assignment_payloads,
        "review_profiles": stable_records("review_profiles"),
        "outcome_kinds": stable_records("outcome_kinds"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _validated_team_copy_session_ids(
    agent_session_ids: Mapping[str, object],
    included_agents: Mapping[str, PersistentAgent],
    current_state: TeamState,
) -> dict[str, str]:
    """Require one new, distinct session for every agent in a team copy."""
    normalized_session_ids = {
        _required_text(source_agent_id, "agent_session_ids key"): _required_text(
            session_id,
            f"agent_session_ids.{source_agent_id}",
        )
        for source_agent_id, session_id in agent_session_ids.items()
    }
    expected_agent_ids = set(included_agents)
    supplied_agent_ids = set(normalized_session_ids)
    missing_agent_ids = sorted(expected_agent_ids - supplied_agent_ids)
    extra_agent_ids = sorted(supplied_agent_ids - expected_agent_ids)
    if missing_agent_ids or extra_agent_ids:
        details: list[str] = []
        if missing_agent_ids:
            details.append("missing: " + ", ".join(missing_agent_ids))
        if extra_agent_ids:
            details.append("unexpected: " + ", ".join(extra_agent_ids))
        raise ValueError(
            "agent_session_ids must exactly match copied agents (" + "; ".join(details) + ")"
        )
    session_values = list(normalized_session_ids.values())
    if len(session_values) != len(set(session_values)):
        raise ValueError("agent_session_ids must assign a distinct session to every copied agent")
    bound_sessions = {
        agent.session_id: agent.id for agent in current_state.agents.values() if agent.session_id
    }
    reused_sessions = sorted(set(session_values) & set(bound_sessions))
    if reused_sessions:
        bindings = ", ".join(
            f"{session_id} ({bound_sessions[session_id]})" for session_id in reused_sessions
        )
        raise ValueError(f"agent sessions are already bound to Team agents: {bindings}")
    return normalized_session_ids
