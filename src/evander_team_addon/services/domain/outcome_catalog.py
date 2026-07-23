from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...models import (
    DeliverableOutcome,
    OutcomeKindDefinition,
    OutcomeKindSnapshot,
    PersistentAgent,
    ReviewProfile,
    RolePermission,
    TaskOutcomeContract,
    TeamState,
    effective_outcome_kind_definitions,
    effective_role_permissions,
    outcome_kind_snapshots,
    resolve_outcome_kind_definition,
)
from .agent_access import _is_project_lead_for, _is_team_manager


def outcome_kind_definition_payload(
    definition: OutcomeKindDefinition,
    *,
    inherited: bool = False,
) -> dict[str, Any]:
    payload = definition.model_dump(mode="json")
    payload["inherited"] = inherited
    return payload


def effective_outcome_kind_payloads(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    return [
        outcome_kind_definition_payload(
            definition,
            inherited=(
                project_id is not None and definition.project_id is None
            ),
        )
        for definition in sorted(
            effective_outcome_kind_definitions(
                state,
                team_id=team_id,
                project_id=project_id,
                include_archived=include_archived,
            ).values(),
            key=lambda item: (item.kind, item.id),
        )
    ]


def snapshot_task_outcome_contract(
    state: TeamState,
    *,
    project_id: str,
    contract: Mapping[str, Any] | TaskOutcomeContract,
) -> TaskOutcomeContract:
    project = state.projects.get(project_id)
    if project is None:
        raise ValueError(f"project {project_id} does not exist")
    parsed = (
        contract
        if isinstance(contract, TaskOutcomeContract)
        else TaskOutcomeContract.model_validate(contract)
    )
    kinds = parsed.required_outcome_kinds + parsed.forbidden_outcome_kinds
    return parsed.model_copy(
        update={
            "outcome_kind_definitions": outcome_kind_snapshots(
                state,
                team_id=project.team_id,
                project_id=project.id,
                kinds=kinds,
            )
        },
        deep=True,
    )


def snapshot_deliverable_outcomes(
    state: TeamState,
    *,
    project_id: str,
    outcomes: Sequence[DeliverableOutcome],
) -> list[DeliverableOutcome]:
    project = state.projects.get(project_id)
    if project is None:
        raise ValueError(f"project {project_id} does not exist")
    snapshotted: list[DeliverableOutcome] = []
    for outcome in outcomes:
        definition = resolve_outcome_kind_definition(
            state,
            team_id=project.team_id,
            project_id=project.id,
            kind=outcome.kind,
        )
        if definition is None:
            raise ValueError(
                f"unknown or archived outcome kind {outcome.kind} for project "
                f"{project.id}"
            )
        snapshotted.append(
            outcome.model_copy(
                update={"definition_snapshot": definition.snapshot()},
                deep=True,
            )
        )
    return snapshotted


def snapshot_review_profile_outcomes(
    state: TeamState,
    *,
    profile: ReviewProfile | None,
) -> list[OutcomeKindSnapshot]:
    if profile is None:
        return []
    return outcome_kind_snapshots(
        state,
        team_id=profile.team_id,
        project_id=profile.project_id,
        kinds=profile.required_outcome_kinds,
        include_archived=profile.archived,
    )


def agent_can_manage_outcome_kinds(
    state: TeamState,
    *,
    actor: PersistentAgent,
    team_id: str,
    project_id: str | None,
) -> bool:
    if actor.team_id != team_id:
        return False
    if project_id is None:
        return (
            _is_team_manager(state, actor)
            and RolePermission.MANAGE_OUTCOME_KINDS
            in effective_role_permissions(state, actor)
        )
    if not (
        _is_team_manager(state, actor)
        or _is_project_lead_for(state, actor.id, project_id)
    ):
        return False
    return (
        RolePermission.MANAGE_OUTCOME_KINDS
        in effective_role_permissions(state, actor, project_id=project_id)
    )


def active_outcome_kind_references(
    state: TeamState,
    *,
    definition: OutcomeKindDefinition,
) -> list[str]:
    references: list[str] = []
    for profile in state.review_profiles.values():
        if profile.archived or definition.kind not in profile.required_outcome_kinds:
            continue
        if profile.team_id != definition.team_id:
            continue
        effective = resolve_outcome_kind_definition(
            state,
            team_id=profile.team_id,
            project_id=profile.project_id,
            kind=definition.kind,
        )
        if effective is not None and effective.id == definition.id:
            references.append(f"review profile {profile.id}")
    return references


__all__ = [
    "active_outcome_kind_references",
    "agent_can_manage_outcome_kinds",
    "effective_outcome_kind_payloads",
    "outcome_kind_definition_payload",
    "snapshot_deliverable_outcomes",
    "snapshot_review_profile_outcomes",
    "snapshot_task_outcome_contract",
]
