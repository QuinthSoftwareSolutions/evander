"""Workflow entry point for the existing Team role queue."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.effects.providers.base import EffectProviderBase, scoped_effect_directory_path
from evander.events.models import WorkflowNodeEnvelope

from .outbox_publication import request_team_outbox_publication_for_records
from .provider_events import _materialize
from .provider_results import _task_effect_error_result, _task_effect_success_result
from .provider_schemas import TeamCreateTaskForRoleEffectDefinition
from .provider_values import (
    _definition_payload,
    _optional_bool,
    _optional_int,
    _optional_text,
    _runtime_dir,
)
from .repository import TeamStateConflictError, TeamStateRepository
from .services.commands.types import TeamCommandValidationError
from .services.workflow_role_task_service import TeamWorkflowRoleTaskService


class TeamCreateTaskForRoleEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamCreateTaskForRoleEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "team_id",
            "project_id",
            "actor_agent_id",
            "role_id",
            "task_id",
            "title",
            "description",
            "priority",
            "case_id",
            "task_kind",
            "parent_task_id",
            "originating_task_id",
            "authored_by",
            "git_base_branch",
            "git_base_commit_sha",
            "git_constraints",
            "dependency_task_ids",
            "integration_boundary",
            "deliverable_contract",
            "outcome_contract",
            "expected_revision",
        )
        if resolved_config:
            payload.update(resolved_config)
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        task_id = _optional_text(payload.get("task_id"))
        team_id = _optional_text(payload.get("team_id"))
        project_id = _optional_text(payload.get("project_id"))
        try:
            payload["priority"] = _optional_int(payload.get("priority"), default=0)
            payload["integration_boundary"] = _optional_bool(
                payload.get("integration_boundary"),
                default=False,
            )
            state, result = TeamWorkflowRoleTaskService(
                repo,
                managed_git_admin=getattr(
                    getattr(self.runtime_context, "admin_service", None),
                    "managed_git_admin",
                    None,
                ),
            ).create_or_reuse(
                team_id=team_id,
                actor_agent_id=payload.get("actor_agent_id"),
                role_id=payload.get("role_id"),
                task=payload,
                directory_scope=scoped_effect_directory_path(self.runtime_context),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except (TeamCommandValidationError, TeamStateConflictError, TypeError, ValueError) as exc:
            return {
                **_task_effect_error_result(
                    revision=repo.read().revision,
                    task_id=task_id,
                    team_id=team_id,
                    project_id=project_id,
                    owner_agent_id=None,
                    error=str(exc),
                    expected_revision=getattr(exc, "expected_revision", None),
                    current_revision=getattr(exc, "current_revision", None),
                ),
                "reused": False,
                "assignment_mode": None,
                "assignment_role_id": None,
                "outbox_record_ids": [],
                "outbox_publication": None,
                "error_code": getattr(exc, "code", None),
            }
        task = state.tasks[task_id]
        return {
            **_task_effect_success_result(state, task_id=task_id, created=result["created"]),
            "reused": not result["created"],
            "assignment_mode": task.assignment_mode,
            "assignment_role_id": task.assignment_role_id,
            "outbox_record_ids": result["outbox_record_ids"],
            "outbox_publication": request_team_outbox_publication_for_records(
                self.runtime_context,
                outbox_record_ids=result["outbox_record_ids"],
            ),
            "error_code": None,
        }
