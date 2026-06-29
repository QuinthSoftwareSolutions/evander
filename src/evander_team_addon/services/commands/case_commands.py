from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import (
    _require_command_agent,
    _team_command_validation_error_from_exception,
)
from .work_item_helpers import (
    _case_command_fingerprint,
    _normalize_case_update_command_fields,
    _team_case_priority_value,
    _team_case_status_value,
    _team_create_case_normalized_payload,
)
from ...models import (
    TeamCaseStatus,
    TeamInboxPriority,
    TeamState,
    _unique_text_list,
)
from ..domain.case_task import (
    _apply_case_update,
    _restore_case_task_state,
    _snapshot_case_task_state,
)
from ..domain.common import (
    _UNSET,
    _optional_text,
    _required_text,
)

from .executor import TeamCommandExecutor


class TeamCaseCommandService(TeamCommandExecutor):
    def create_case_command(
        self,
        *,
        session_id: str,
        title: str,
        request_id: str,
        case_id: str | None = None,
        project_id: str | None = None,
        description: str | None = None,
        status: TeamCaseStatus | str = TeamCaseStatus.OPEN,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        owner_agent_id: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        tags: Sequence[str] | None = None,
        task_ids: Sequence[str] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_case_id = _optional_text(case_id)
        normalized_project_id = _optional_text(project_id)
        normalized_title = _optional_text(title) or ""
        normalized_description = _optional_text(description) or ""
        normalized_status = _team_case_status_value(status, default=TeamCaseStatus.OPEN)
        normalized_priority = _team_case_priority_value(
            priority,
            default=TeamInboxPriority.NORMAL,
        )
        normalized_owner_id = _optional_text(owner_agent_id)
        normalized_source = _optional_text(source)
        normalized_source_ref = _optional_text(source_ref)
        normalized_tags = _unique_text_list(tags or [], "tags")
        normalized_task_ids = _unique_text_list(task_ids or [], "task_ids")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_create_case",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = _team_create_case_normalized_payload(
            case_id=normalized_case_id,
            project_id=normalized_project_id,
            title=normalized_title,
            description=normalized_description,
            status=normalized_status,
            priority=normalized_priority,
            owner_agent_id=normalized_owner_id,
            source=normalized_source,
            source_ref=normalized_source_ref,
            tags=normalized_tags,
            task_ids=normalized_task_ids,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_create_case")
            if normalized_case_id is not None and normalized_case_id in state.cases:
                raise TeamCommandValidationError(
                    code="case_already_exists",
                    message=f"case {normalized_case_id} already exists",
                    correction_context={"case_id": normalized_case_id},
                )
            update: dict[str, Any] = {
                "title": normalized_title,
                "description": normalized_description,
                "status": normalized_status,
                "priority": normalized_priority,
                "tags": list(normalized_tags),
                "task_ids": list(normalized_task_ids),
            }
            if normalized_case_id is not None:
                update["id"] = normalized_case_id
            if normalized_project_id is not None:
                update["project_id"] = normalized_project_id
            if normalized_owner_id is not None:
                update["owner_agent_id"] = normalized_owner_id
            if normalized_source is not None:
                update["source"] = normalized_source
            if normalized_source_ref is not None:
                update["source_ref"] = normalized_source_ref
            snapshot = _snapshot_case_task_state(state)
            try:
                created_case_id, created = _apply_case_update(
                    state,
                    actor=agent,
                    update=update,
                )
            except ValueError as exc:
                _restore_case_task_state(state, snapshot)
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="case_validation_failed",
                    correction_context={
                        "case_id": normalized_case_id,
                        "project_id": normalized_project_id,
                        "owner_agent_id": normalized_owner_id,
                        "task_ids": list(normalized_task_ids),
                    },
                ) from exc
            if not created:
                _restore_case_task_state(state, snapshot)
                raise TeamCommandValidationError(
                    code="case_already_exists",
                    message=f"case {created_case_id} already exists",
                    correction_context={"case_id": created_case_id},
                )
            return TeamCommandApplyResult(
                response_payload={
                    "case_id": created_case_id,
                    "created_case_ids": [created_case_id],
                    "progress_made": True,
                },
                created_domain_ids=[created_case_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def update_case_command(
        self,
        *,
        session_id: str,
        case_id: str,
        request_id: str | None = None,
        project_id: object = _UNSET,
        title: object = _UNSET,
        description: object = _UNSET,
        status: object = _UNSET,
        priority: object = _UNSET,
        owner_agent_id: object = _UNSET,
        source: object = _UNSET,
        source_ref: object = _UNSET,
        tags: object = _UNSET,
        task_ids: object = _UNSET,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_case_id = _required_text(case_id, "case_id")
        normalized_request_id = _optional_text(request_id)
        normalized_updates = _normalize_case_update_command_fields(
            project_id=project_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            owner_agent_id=owner_agent_id,
            source=source,
            source_ref=source_ref,
            tags=tags,
            task_ids=task_ids,
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_case",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "case_id": normalized_case_id,
            "updates": normalized_updates,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_update_case")
            if normalized_case_id not in state.cases:
                raise TeamCommandValidationError(
                    code="case_not_found",
                    message=f"case {normalized_case_id} does not exist",
                    correction_context={"case_id": normalized_case_id},
                )
            before = _case_command_fingerprint(state, normalized_case_id)
            snapshot = _snapshot_case_task_state(state)
            try:
                updated_case_id, created = _apply_case_update(
                    state,
                    actor=agent,
                    update={"case_id": normalized_case_id, **normalized_updates},
                )
            except ValueError as exc:
                _restore_case_task_state(state, snapshot)
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="case_validation_failed",
                    correction_context={
                        "case_id": normalized_case_id,
                        "updates": dict(normalized_updates),
                    },
                ) from exc
            if created:
                _restore_case_task_state(state, snapshot)
                raise TeamCommandValidationError(
                    code="case_not_found",
                    message=f"case {normalized_case_id} does not exist",
                    correction_context={"case_id": normalized_case_id},
                )
            changed = before != _case_command_fingerprint(state, updated_case_id)
            return TeamCommandApplyResult(
                response_payload={
                    "case_id": updated_case_id,
                    "updated_case_ids": [updated_case_id] if changed else [],
                    "progress_made": changed,
                },
                created_domain_ids=[updated_case_id] if changed else [],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamCaseCommandService"]
