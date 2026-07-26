from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from evander.models import utc_now

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
from ...models import (
    TaskCancellation,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    _unique_text_list,
)
from ..domain.agent_access import (
    _is_project_lead_for,
    _is_team_manager,
    _supervises,
)
from ..domain.case_task import (
    _apply_goal_update,
    _subordinate_task_finish_allowed,
    _task_graph_edit_error,
    _task_owner_reassignment_error,
)
from ..domain.blockers import (
    active_blockers_for_source,
    open_or_get_blocker,
    reconcile_task_blockers_after_cancellation,
    reconcile_task_blockers_after_reassignment,
    resolve_blocker,
)
from ..domain.common import (
    DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
    _MCP_TASK_UPDATE_STATE_VALUES,
    _NON_TERMINAL_TASK_STATES,
    _UNSET,
    _optional_text,
    _required_text,
    _set_task_state,
)
from ..domain.context_rotation import _maybe_mark_idle_context_rotation_due
from ..domain.git_work_contexts import finalize_active_git_work_contexts_for_task
from ..domain.project_status import _recalculate_project_status
from ..domain.task_assessment import (
    TaskAssessmentBoundaryError,
    assess_task,
    require_task_can_finish,
)
from ..domain.task_supervision import queue_parent_resolution_transitions
from ..domain.wake import _append_agent_wake_requested_event
from ..domain.work_due import _mark_agent_work_due


class TeamTaskUpdateCommandService(TeamCommandExecutor):
    def cancel_task_command(
        self,
        *,
        session_id: str,
        task_id: str,
        reason_code: str,
        reason: str,
        request_id: str,
        successor_task_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_reason_code = _required_text(reason_code, "reason_code")
        normalized_reason = _required_text(reason, "reason")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_successor_id = _optional_text(successor_task_id)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_cancel_task",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "task_id": normalized_task_id,
            "reason_code": normalized_reason_code,
            "reason": normalized_reason,
            "successor_task_id": normalized_successor_id,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(resolved, "team_cancel_task")
            task = state_model.tasks.get(normalized_task_id)
            if task is None:
                raise TeamCommandValidationError(
                    code="task_not_found",
                    message=f"task {normalized_task_id} does not exist",
                )
            parent = state_model.tasks.get(task.parent_task_id or "")
            authorized = (
                task.owner_agent_id == actor.id
                or (parent is not None and parent.owner_agent_id == actor.id)
                or _is_project_lead_for(state_model, actor.id, task.project_id)
                or _is_team_manager(state_model, actor)
            )
            if not authorized:
                raise TeamCommandValidationError(
                    code="task_cancellation_not_permitted",
                    message=f"agent {actor.id} cannot cancel task {task.id}",
                    correction_context={
                        "task_id": task.id,
                        "owner_agent_id": task.owner_agent_id,
                        "parent_owner_agent_id": (
                            parent.owner_agent_id if parent is not None else None
                        ),
                    },
                )
            if task.state not in {
                *_NON_TERMINAL_TASK_STATES,
                TeamTaskState.REJECTED,
            }:
                raise TeamCommandValidationError(
                    code="task_cancellation_terminal",
                    message=f"task {task.id} is already {task.state.value}",
                )
            assessment = assess_task(state_model, task.id)
            if assessment.unresolved_descendant_ids:
                raise TeamCommandValidationError(
                    code="task_cancellation_unresolved_descendants",
                    message=(
                        f"task {task.id} cannot be cancelled while descendants remain "
                        "unresolved"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "unresolved_descendant_ids": list(
                            assessment.unresolved_descendant_ids
                        ),
                    },
                )
            if normalized_successor_id is not None:
                successor = state_model.tasks.get(normalized_successor_id)
                if successor is None:
                    raise TeamCommandValidationError(
                        code="task_cancellation_successor_not_found",
                        message=f"successor task {normalized_successor_id} does not exist",
                    )
                if successor.id == task.id or successor.project_id != task.project_id:
                    raise TeamCommandValidationError(
                        code="task_cancellation_successor_invalid",
                        message=(
                            "a cancellation successor must be a different task in "
                            f"project {task.project_id}"
                        ),
                        correction_context={
                            "task_id": task.id,
                            "successor_task_id": successor.id,
                            "successor_project_id": successor.project_id,
                        },
                    )
            now = utc_now()
            task.cancellation = TaskCancellation(
                reason_code=normalized_reason_code,
                reason=normalized_reason,
                actor_kind="agent",
                actor_id=actor.id,
                successor_task_id=normalized_successor_id,
                cancelled_at=now,
            )
            _set_task_state(task, TeamTaskState.CANCELLED, now=now)
            task.updated_at = now
            git_context_finalization = finalize_active_git_work_contexts_for_task(
                state_model,
                task_id=task.id,
                now=now,
            )
            result: dict[str, Any] = {
                "task_id": task.id,
                "cancelled_task_ids": [task.id],
                "successor_task_id": normalized_successor_id,
                "resolved_blocker_ids": [],
                "git_context_finalization": git_context_finalization,
                "progress_made": True,
            }
            queue_parent_resolution_transitions(
                state_model,
                child_task_id=task.id,
                sender_agent_id=actor.id,
                result=result,
                now=now,
            )
            before_event_ids = set(state_model.blocker_events)
            reconcile_task_blockers_after_cancellation(
                state_model,
                task_id=task.id,
                actor_agent_id=actor.id,
                dispatch_id=resolved.dispatch_id,
                result=result,
                now=now,
            )
            blocker_event_ids = [
                event_id
                for event_id in state_model.blocker_events
                if event_id not in before_event_ids
            ]
            result["blocker_event_ids"] = blocker_event_ids
            _maybe_mark_idle_context_rotation_due(
                state_model,
                agent_id=task.owner_agent_id,
                now=now,
            )
            _recalculate_project_status(state_model, task.project_id)
            return TeamCommandApplyResult(
                response_payload=result,
                created_domain_ids=[task.id, *blocker_event_ids],
                created_event_ids=blocker_event_ids,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )

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
        completion: object = _UNSET,
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
            completion=completion,
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
            reconciliation_result: dict[str, Any] = {
                "created_inbox_item_ids": [],
                "events": [],
                "resolved_blocker_ids": [],
                "blocker_event_ids": [],
            }
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
            has_completion = "completion" in normalized_updates
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
                (
                    has_state
                    or has_priority
                    or has_title
                    or has_description
                    or has_completion
                )
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
                            "waiting_for_review, or finished"
                        ),
                        correction_context={
                            "state": next_state.value,
                            "allowed_states": _MCP_TASK_UPDATE_STATE_VALUES,
                        },
                    )
                if (
                    next_state == TeamTaskState.BLOCKED
                    or (
                        task.state == TeamTaskState.BLOCKED
                        and next_state != TeamTaskState.BLOCKED
                    )
                ):
                    raise TeamCommandValidationError(
                        code="task_blocker_command_required",
                        message=(
                            "direct task state edits into or out of blocked are disabled; "
                            "use team_report_blocker or team_update_blocker resolve"
                        ),
                        correction_context={
                            "task_id": task.id,
                            "current_state": task.state.value,
                            "requested_state": next_state.value,
                            "recommended_tools": [
                                "team_report_blocker",
                                "team_update_blocker",
                            ],
                        },
                    )
                if next_state == TeamTaskState.FINISHED:
                    try:
                        require_task_can_finish(state_model, task.id)
                    except TaskAssessmentBoundaryError as exc:
                        raise TeamCommandValidationError(
                            code=exc.code,
                            message=exc.message,
                            correction_context=exc.correction_context,
                        ) from None
                    if has_completion:
                        raise TeamCommandValidationError(
                            code="task_completion_requires_accepted_deliverable",
                            message=(
                                "structured outcome completion is derived from an "
                                "accepted deliverable; team_update_task cannot set it"
                            ),
                            correction_context={
                                "task_id": task.id,
                                "recommended_tool": "team_submit_deliverable",
                            },
                        )
            if has_completion and next_state != TeamTaskState.FINISHED:
                raise TeamCommandValidationError(
                    code="task_completion_requires_accepted_deliverable",
                    message=(
                        "structured outcome completion is derived from an accepted "
                        "deliverable; team_update_task cannot set it"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "recommended_tool": "team_submit_deliverable",
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
            previous_task_state = task.state
            if next_state is not None:
                changed = _set_task_state(task, next_state, now=now) or changed
                if task.state not in _NON_TERMINAL_TASK_STATES:
                    finalize_active_git_work_contexts_for_task(
                        state_model,
                        task_id=task.id,
                        now=now,
                    )
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
                finalize_active_git_work_contexts_for_task(
                    state_model,
                    task_id=task.id,
                    now=now,
                )
                task.owner_agent_id = new_owner_id
                reconcile_task_blockers_after_reassignment(
                    state_model,
                    task_id=task.id,
                    old_owner_agent_id=old_owner_id,
                    new_owner_agent_id=new_owner_id,
                    actor_agent_id=agent.id,
                    dispatch_id=resolved.dispatch_id,
                    result=reconciliation_result,
                    now=now,
                )
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
                if next_state is not None or has_owner_reassignment:
                    _recalculate_project_status(state_model, task.project_id)
                if task.state in {
                    TeamTaskState.ACCEPTED,
                    TeamTaskState.CANCELLED,
                    TeamTaskState.FINISHED,
                }:
                    queue_parent_resolution_transitions(
                        state_model,
                        child_task_id=task.id,
                        sender_agent_id=agent.id,
                        now=now,
                    )
            response_payload = {
                "task_id": task.id,
                "updated_task_ids": [task.id] if changed else [],
                "progress_made": changed,
                "created_blocker_ids": [],
                "created_inbox_item_ids": list(
                    reconciliation_result["created_inbox_item_ids"]
                ),
                "events": list(reconciliation_result["events"]),
                "resolved_blocker_ids": list(
                    reconciliation_result["resolved_blocker_ids"]
                ),
                "blocker_event_ids": list(
                    reconciliation_result["blocker_event_ids"]
                ),
                "resolved_blocker_event_ids": [],
                "automatically_handled_inbox_item_ids": [],
            }
            blocker_domain_ids: list[str] = []
            if (
                changed
                and previous_task_state != TeamTaskState.BLOCKED
                and task.state == TeamTaskState.BLOCKED
            ):
                blocker, created = open_or_get_blocker(
                    state_model,
                    source_kind=TeamBlockerSourceKind.TASK,
                    source_id=task.id,
                    summary=f"Task {task.id} is blocked",
                    details=task.description,
                    reporter_agent_id=agent.id,
                    team_id=agent.team_id,
                    project_id=task.project_id,
                    task_id=task.id,
                    result=response_payload,
                    now=now,
                )
                if created:
                    blocker_domain_ids.append(blocker.id)
            elif (
                changed
                and previous_task_state == TeamTaskState.BLOCKED
                and task.state != TeamTaskState.BLOCKED
            ):
                for blocker in active_blockers_for_source(
                    state_model,
                    source_kind=TeamBlockerSourceKind.TASK,
                    source_id=task.id,
                ):
                    pending_blocker_inbox_ids = [
                        item.id
                        for item in state_model.inbox.values()
                        if item.state == TeamInboxItemState.PENDING
                        and item.payload.get("blocker_id") == blocker.id
                    ]
                    event = resolve_blocker(
                        state_model,
                        blocker=blocker,
                        actor_agent_id=agent.id,
                        dispatch_id=resolved.dispatch_id,
                        note=f"Task moved from blocked to {task.state.value}.",
                        reference_ids=[task.id],
                        now=now,
                    )
                    blocker_domain_ids.extend([blocker.id, event.id])
                    response_payload["resolved_blocker_ids"].append(blocker.id)
                    response_payload["resolved_blocker_event_ids"].append(event.id)
                    response_payload["automatically_handled_inbox_item_ids"].extend(
                        pending_blocker_inbox_ids
                    )
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
                created_domain_ids=(
                    [
                        task.id,
                        *blocker_domain_ids,
                        *response_payload["blocker_event_ids"],
                        *response_payload["created_inbox_item_ids"],
                    ]
                    if changed
                    else []
                ),
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=(
                normalized_updates.get("state") == TeamTaskState.FINISHED.value
            ),
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
            reject_stale_high_priority_inbox=(
                normalized_state == TeamRecordState.ACCEPTED.value
            ),
        )


__all__ = ["TeamTaskUpdateCommandService"]
