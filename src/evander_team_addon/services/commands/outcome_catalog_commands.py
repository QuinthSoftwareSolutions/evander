from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from evander.models import utc_now

from ...models import (
    OutcomeKindDefinition,
    PersistentAgent,
    TeamState,
    _unique_text_list,
)
from ..domain.common import _generated_id, _optional_text, _require_present, _required_text
from ..domain.outcome_catalog import (
    active_outcome_kind_references,
    agent_can_manage_outcome_kinds,
)
from .executor import TeamCommandExecutor
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import _require_command_agent


class TeamOutcomeCatalogCommandService(TeamCommandExecutor):
    def create_outcome_kind_command(
        self,
        *,
        session_id: str,
        team_id: str,
        kind: str,
        name: str,
        request_id: str,
        project_id: str | None = None,
        definition_id: str | None = None,
        description: str = "",
        reference_requirements: str = "",
        evidence_requirements: str = "",
        handled_inbox_item_ids: Sequence[str] | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_project_id = _optional_text(project_id)
        normalized_kind = _required_text(kind, "kind")
        normalized_name = _required_text(name, "name")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        payload = {
            "team_id": normalized_team_id,
            "project_id": normalized_project_id,
            "definition_id": _optional_text(definition_id),
            "kind": normalized_kind,
            "name": normalized_name,
            "description": description,
            "reference_requirements": reference_requirements,
            "evidence_requirements": evidence_requirements,
            "handled_inbox_item_ids": sorted(normalized_handled),
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(resolved, "team_create_outcome_kind")
            _require_catalog_permission(
                state,
                actor=actor,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
            )
            _require_present(state.teams, normalized_team_id, "team")
            if normalized_project_id is not None:
                project = _require_present(
                    state.projects,
                    normalized_project_id,
                    "project",
                )
                if project.team_id != normalized_team_id:
                    raise ValueError(
                        f"project {project.id} does not belong to team "
                        f"{normalized_team_id}"
                    )
            if any(
                definition.team_id == normalized_team_id
                and definition.project_id == normalized_project_id
                and definition.kind == normalized_kind
                for definition in state.outcome_kind_definitions.values()
            ):
                raise TeamCommandValidationError(
                    code="outcome_kind_already_exists",
                    message=(
                        f"outcome kind {normalized_kind} already exists in "
                        f"{normalized_project_id or normalized_team_id}"
                    ),
                )
            effective_id = payload["definition_id"] or _generated_id(
                prefix="outcome-kind",
                labels=[
                    normalized_project_id or normalized_team_id,
                    normalized_kind,
                ],
                existing_ids=state.outcome_kind_definitions,
            )
            if effective_id in state.outcome_kind_definitions:
                raise TeamCommandValidationError(
                    code="outcome_kind_id_exists",
                    message=f"outcome kind definition {effective_id} already exists",
                )
            now = utc_now()
            state.outcome_kind_definitions[effective_id] = OutcomeKindDefinition(
                id=effective_id,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                kind=normalized_kind,
                name=normalized_name,
                description=description,
                reference_requirements=reference_requirements,
                evidence_requirements=evidence_requirements,
                created_at=now,
                updated_at=now,
            )
            return TeamCommandApplyResult(
                response_payload={
                    "outcome_kind_definition_id": effective_id,
                    "kind": normalized_kind,
                    "progress_made": True,
                },
                created_domain_ids=[effective_id],
            )

        return self._execute_team_command(
            context=TeamCommandContext(
                ambient_session_id=normalized_session_id,
                tool_name="team_create_outcome_kind",
                request_id=normalized_request_id,
                progress_classification="progress",
            ),
            normalized_payload=payload,
            handled_inbox_item_ids=normalized_handled,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def update_outcome_kind_command(
        self,
        *,
        session_id: str,
        definition_id: str,
        request_id: str,
        name: str | None = None,
        description: str | None = None,
        reference_requirements: str | None = None,
        evidence_requirements: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_id = _required_text(definition_id, "definition_id")
        normalized_name = _optional_text(name)
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        updates = {
            "name": normalized_name,
            "description": description,
            "reference_requirements": reference_requirements,
            "evidence_requirements": evidence_requirements,
        }
        if all(value is None for value in updates.values()):
            raise ValueError("at least one outcome kind field must be updated")
        payload = {
            "definition_id": normalized_id,
            **updates,
            "handled_inbox_item_ids": sorted(normalized_handled),
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(resolved, "team_update_outcome_kind")
            definition = _require_present(
                state.outcome_kind_definitions,
                normalized_id,
                "outcome kind definition",
            )
            _require_catalog_permission(
                state,
                actor=actor,
                team_id=definition.team_id,
                project_id=definition.project_id,
            )
            if normalized_name is not None:
                definition.name = normalized_name
            if description is not None:
                definition.description = description
            if reference_requirements is not None:
                definition.reference_requirements = reference_requirements
            if evidence_requirements is not None:
                definition.evidence_requirements = evidence_requirements
            definition.updated_at = utc_now()
            return TeamCommandApplyResult(
                response_payload={
                    "outcome_kind_definition_id": definition.id,
                    "kind": definition.kind,
                    "progress_made": True,
                }
            )

        return self._execute_team_command(
            context=TeamCommandContext(
                ambient_session_id=normalized_session_id,
                tool_name="team_update_outcome_kind",
                request_id=normalized_request_id,
                progress_classification="progress",
            ),
            normalized_payload=payload,
            handled_inbox_item_ids=normalized_handled,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def archive_outcome_kind_command(
        self,
        *,
        session_id: str,
        definition_id: str,
        request_id: str,
        archived: bool = True,
        handled_inbox_item_ids: Sequence[str] | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_id = _required_text(definition_id, "definition_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        payload = {
            "definition_id": normalized_id,
            "archived": bool(archived),
            "handled_inbox_item_ids": sorted(normalized_handled),
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(resolved, "team_archive_outcome_kind")
            definition = _require_present(
                state.outcome_kind_definitions,
                normalized_id,
                "outcome kind definition",
            )
            _require_catalog_permission(
                state,
                actor=actor,
                team_id=definition.team_id,
                project_id=definition.project_id,
            )
            if archived:
                references = active_outcome_kind_references(
                    state,
                    definition=definition,
                )
                if references:
                    raise TeamCommandValidationError(
                        code="outcome_kind_in_use",
                        message=(
                            f"outcome kind {definition.kind} is used by "
                            + ", ".join(references)
                        ),
                    )
            definition.archived = bool(archived)
            definition.updated_at = utc_now()
            return TeamCommandApplyResult(
                response_payload={
                    "outcome_kind_definition_id": definition.id,
                    "kind": definition.kind,
                    "archived": definition.archived,
                    "progress_made": True,
                }
            )

        return self._execute_team_command(
            context=TeamCommandContext(
                ambient_session_id=normalized_session_id,
                tool_name="team_archive_outcome_kind",
                request_id=normalized_request_id,
                progress_classification="progress",
            ),
            normalized_payload=payload,
            handled_inbox_item_ids=normalized_handled,
            apply_command=_apply,
            require_active_dispatch=True,
        )


def _require_catalog_permission(
    state: TeamState,
    *,
    actor: PersistentAgent,
    team_id: str,
    project_id: str | None,
) -> None:
    if agent_can_manage_outcome_kinds(
        state,
        actor=actor,
        team_id=team_id,
        project_id=project_id,
    ):
        return
    raise TeamCommandValidationError(
        code="outcome_kind_management_forbidden",
        message=(
            f"agent {actor.id} needs manage_outcome_kinds and matching manager "
            "or project-lead scope"
        ),
        correction_context={
            "team_id": team_id,
            "project_id": project_id,
            "required_permission": "manage_outcome_kinds",
        },
    )


__all__ = ["TeamOutcomeCatalogCommandService"]
