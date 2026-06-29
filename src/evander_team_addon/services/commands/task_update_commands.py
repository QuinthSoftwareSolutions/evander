from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_sandbox.models import utc_now

from .executor import TeamCommandExecutor
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import _require_command_agent
from .work_item_helpers import (
    _normalize_task_update_command_fields,
    _task_graph_correction_context,
    _team_record_state_value,
)
from ..domain_helpers import (
    DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    _MCP_TASK_UPDATE_STATE_VALUES,
    _NON_TERMINAL_TASK_STATES,
    _UNSET,
    _append_agent_wake_requested_event,
    _apply_goal_update,
    _is_project_lead_for,
    _is_team_manager,
    _mark_agent_work_due,
    _maybe_mark_idle_context_rotation_due,
    _optional_text,
    _recalculate_project_status,
    _required_text,
    _set_task_state,
    _subordinate_task_finish_allowed,
    _supervises,
    _task_graph_edit_error,
    _task_owner_reassignment_error,
    _unique_text_list,
)


class TeamTaskUpdateCommandService(TeamCommandExecutor):
    def update_task_command(
        self,
        *,
        session_id: str,
        task_id: str,
        request_id: str | None = None,
        state: object = _UNSET,
        priority: object = _UNSET,
        title: object = _UNSET,
        description: object = _UNSET,
        owner_agent_id: object = _UNSET,
        dependency_task_ids: object = _UNSET,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_request_id = _optional_text(request_id)
        normalized_updates = _normalize_task_update_command_fields(
            state=state,
            priority=priority,
            title=title,
            description=description,
            owner_agent_id=owner_agent_id,
            dependency_task_ids=dependency_task_ids,
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_task",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "task_id": normalized_task_id,
            "updates": normalized_updates,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_update_task")
            task = state_model.tasks.get(normalized_task_id)
            if task is None:
                raise TeamCommandValidationError(
                    code="task_not_found",
                    message=f"task {normalized_task_id} does not exist",
                    correction_context={"task_id": normalized_task_id},
                )
            is_owner = task.owner_agent_id == agent.id
            raw_state = normalized_updates.get("state")
            has_state = "state" in normalized_updates
            has_priority = "priority" in normalized_updates
            has_title = "title" in normalized_updates
            has_description = "description" in normalized_updates
            new_owner_id = normalized_updates.get("owner_agent_id")
            has_owner_reassignment = (
                isinstance(new_owner_id, str) and new_owner_id != task.owner_agent_id
            )
            has_dependency_edit = "dependency_task_ids" in normalized_updates
            subordinate_finish_allowed = (
                has_state
                and not has_priority
                and not has_title
                and not has_description
                and _subordinate_task_finish_allowed(
                    state_model,
                    actor=agent,
                    task=task,
                    raw_state=raw_state,
                    new_owner_id=new_owner_id if has_owner_reassignment else None,
                )
            )
            if (
                (has_state or has_priority or has_title or has_description)
                and not is_owner
                and not subordinate_finish_allowed
            ):
                raise TeamCommandValidationError(
                    code="task_update_not_owner",
                    message=f"agent {agent.id} cannot update task {task.id}",
                    correction_context={
                        "task_id": task.id,
                        "caller_agent_id": agent.id,
                        "owner_agent_id": task.owner_agent_id,
                    },
                )
            next_state: TeamTaskState | None = None
            if has_state:
                try:
                    next_state = TeamTaskState(str(raw_state))
                except ValueError as exc:
                    raise TeamCommandValidationError(
                        code="invalid_task_state",
                        message=f"task update has invalid state {raw_state}",
                        correction_context={
                            "state": raw_state,
                            "allowed_states": _MCP_TASK_UPDATE_STATE_VALUES,
                        },
                    ) from exc
                if next_state.value not in _MCP_TASK_UPDATE_STATE_VALUES:
                    raise TeamCommandValidationError(
                        code="invalid_task_state",
                        message=(
                            "team_update_task.state must be pending, in_progress, "
                            "blocked, waiting_for_review, or finished"
                        ),
                        correction_context={
                            "state": next_state.value,
                            "allowed_states": _MCP_TASK_UPDATE_STATE_VALUES,
                        },
                    )
            if has_owner_reassignment and isinstance(new_owner_id, str):
                reassignment_error = _task_owner_reassignment_error(
                    state_model,
                    actor=agent,
                    task=task,
                    new_owner_id=new_owner_id,
                )
                if reassignment_error is not None:
                    raise TeamCommandValidationError(
                        code="task_reassignment_not_permitted",
                        message=reassignment_error,
                        correction_context={
                            "task_id": task.id,
                            "caller_agent_id": agent.id,
                            "owner_agent_id": task.owner_agent_id,
                            "new_owner_agent_id": new_owner_id,
                        },
                    )
            new_dependency_ids: list[str] | None = None
            if has_dependency_edit:
                graph_permitted = (
                    is_owner
                    or _supervises(state_model, agent.id, task.owner_agent_id)
                    or _is_project_lead_for(state_model, agent.id, task.project_id)
                    or _is_team_manager(state_model, agent)
                )
                if not graph_permitted:
                    raise TeamCommandValidationError(
                        code="task_graph_not_permitted",
                        message=(
                            f"agent {agent.id} is not permitted to edit the task graph "
                            f"for task {task.id}"
                        ),
                        correction_context={
                            "task_id": task.id,
                            "caller_agent_id": agent.id,
                            "owner_agent_id": task.owner_agent_id,
                        },
                    )
                new_dependency_ids = list(normalized_updates["dependency_task_ids"])
                graph_error = _task_graph_edit_error(
                    state_model,
                    task_id=task.id,
                    project_id=task.project_id,
                    parent_task_id=task.parent_task_id,
                    dependency_task_ids=new_dependency_ids,
                )
                if graph_error is not None:
                    raise TeamCommandValidationError(
                        code="task_graph_invalid",
                        message=graph_error,
                        correction_context=_task_graph_correction_context(
                            state_model,
                            project_id=task.project_id,
                            task_id=task.id,
                            parent_task_id=task.parent_task_id,
                            dependency_task_ids=new_dependency_ids,
                        ),
                    )
            changed = False
            now = utc_now()
            if next_state is not None:
                changed = _set_task_state(task, next_state, now=now) or changed
            if has_priority:
                next_priority = int(normalized_updates["priority"])
                if task.priority != next_priority:
                    task.priority = next_priority
                    changed = True
            if has_title and isinstance(normalized_updates["title"], str):
                if task.title != normalized_updates["title"]:
                    task.title = normalized_updates["title"]
                    changed = True
            if has_description and isinstance(normalized_updates["description"], str):
                if task.description != normalized_updates["description"]:
                    task.description = normalized_updates["description"]
                    changed = True
            if has_owner_reassignment and isinstance(new_owner_id, str):
                old_owner_id = task.owner_agent_id
                task.owner_agent_id = new_owner_id
                _maybe_mark_idle_context_rotation_due(
                    state_model,
                    agent_id=old_owner_id,
                    now=now,
                )
                if task.state in _NON_TERMINAL_TASK_STATES:
                    _mark_agent_work_due(state_model, agent_id=new_owner_id, now=now)
                changed = True
            if new_dependency_ids is not None and task.dependency_task_ids != new_dependency_ids:
                task.dependency_task_ids = new_dependency_ids
                changed = True
            if changed:
                task.updated_at = now
                if task.state in _NON_TERMINAL_TASK_STATES:
                    _mark_agent_work_due(
                        state_model,
                        agent_id=task.owner_agent_id,
                        now=now,
                    )
                else:
                    _maybe_mark_idle_context_rotation_due(
                        state_model,
                        agent_id=task.owner_agent_id,
                        now=now,
                    )
                if next_state is not None:
                    _recalculate_project_status(state_model, task.project_id)
            response_payload = {
                "task_id": task.id,
                "updated_task_ids": [task.id] if changed else [],
                "progress_made": changed,
            }
            if changed and task.state in _NON_TERMINAL_TASK_STATES:
                _append_agent_wake_requested_event(
                    state_model,
                    agent_id=task.owner_agent_id,
                    result=response_payload,
                    reason="task_ready",
                    now=now,
                    delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=[task.id] if changed else [],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def update_goal_command(
        self,
        *,
        session_id: str,
        project_id: str,
        goal_id: str,
        state: TeamRecordState | str,
        request_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_project_id = _required_text(project_id, "project_id")
        normalized_goal_id = _required_text(goal_id, "goal_id")
        normalized_state = _team_record_state_value(state)
        normalized_request_id = _optional_text(request_id)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_goal",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "project_id": normalized_project_id,
            "goal_id": normalized_goal_id,
            "state": normalized_state,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_update_goal")
            project = state_model.projects.get(normalized_project_id)
            before_state = None
            if project is not None:
                goal = next(
                    (item for item in project.goals if item.id == normalized_goal_id),
                    None,
                )
                before_state = goal.state.value if goal is not None else None
            result: dict[str, Any] = {"errors": []}
            projects_to_recalc: set[str] = set()
            updated_id = _apply_goal_update(
                state_model,
                actor=agent,
                update={
                    "project_id": normalized_project_id,
                    "goal_id": normalized_goal_id,
                    "state": normalized_state,
                },
                result=result,
                projects_to_recalc=projects_to_recalc,
            )
            if result["errors"] or updated_id is None:
                raise TeamCommandValidationError(
                    code="goal_update_failed",
                    message="; ".join(result["errors"]) or "goal update failed",
                    correction_context={
                        "project_id": normalized_project_id,
                        "goal_id": normalized_goal_id,
                        "state": normalized_state,
                    },
                )
            for recalc_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, recalc_id)
            changed = before_state != normalized_state
            return TeamCommandApplyResult(
                response_payload={
                    "project_id": normalized_project_id,
                    "goal_id": updated_id,
                    "updated_goal_ids": [updated_id] if changed else [],
                    "progress_made": changed,
                },
                created_domain_ids=[updated_id] if changed else [],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamTaskUpdateCommandService"]
