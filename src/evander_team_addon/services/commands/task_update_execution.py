from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
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
)
from ...models import (
    AgentTask,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamState,
    TeamTaskState,
    TaskDeliverableContract,
    _unique_text_list,
)
from ..domain.agent_access import (
    _is_project_lead_for,
    _is_team_manager,
    _supervises,
)
from ..domain.task_authorship import task_author_identity
from ..domain.case_task import (
    TaskGitBaseValidationError,
    _set_task_git_base_branch,
    _subordinate_task_finish_allowed,
    _task_deliverable_contract_scope_error,
    _task_graph_edit_error,
    _task_integration_boundary_transition_error,
    _task_owner_reassignment_error,
)
from ..domain.blockers import (
    active_blockers_for_source,
    open_or_get_blocker,
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
    task_state_can_wake_owner,
)
from ..domain.context_rotation import _maybe_mark_idle_context_rotation_due
from ..domain.git_work_contexts import finalize_active_git_work_contexts_for_task
from ..domain.project_status import _recalculate_project_status
from ..domain.preparation_failures import (
    task_git_work_context_generation,
)
from ..domain.task_assessment import (
    TaskAssessmentBoundaryError,
    require_task_can_finish,
)
from ..domain.task_preparation_invalidation import (
    invalidate_task_preparation_after_edit,
)
from ..domain.task_supervision import queue_parent_resolution_transitions
from ..domain.wake import _append_agent_wake_requested_event
from ..domain.work_due import _mark_agent_work_due


def _empty_task_reassignment_result() -> dict[str, Any]:
    return {
        "created_inbox_item_ids": [],
        "events": [],
        "resolved_blocker_ids": [],
        "blocker_event_ids": [],
    }


def _apply_task_owner_reassignment(
    state: TeamState,
    *,
    task: AgentTask,
    new_owner_id: str,
    actor_agent_id: str,
    dispatch_id: str | None,
    reconciliation_result: dict[str, Any],
    now: datetime,
) -> None:
    old_owner_id = task.owner_agent_id
    finalize_active_git_work_contexts_for_task(
        state,
        task_id=task.id,
        now=now,
    )
    task.owner_agent_id = new_owner_id
    task.assignment_mode = "agent"
    task.assignment_role_id = None
    reconcile_task_blockers_after_reassignment(
        state,
        task_id=task.id,
        old_owner_agent_id=old_owner_id,
        new_owner_agent_id=new_owner_id,
        actor_agent_id=actor_agent_id,
        dispatch_id=dispatch_id,
        result=reconciliation_result,
        now=now,
    )
    _maybe_mark_idle_context_rotation_due(
        state,
        agent_id=old_owner_id,
        now=now,
        result=reconciliation_result,
    )
    if task_state_can_wake_owner(task.state):
        _mark_agent_work_due(state, agent_id=new_owner_id, now=now)


def execute_task_update_command(
    executor: TeamCommandExecutor,
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
    authored_by: object = _UNSET,
    git_base_branch: object = _UNSET,
    integration_boundary: object = _UNSET,
    deliverable_contract: object = _UNSET,
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
        authored_by=authored_by,
        git_base_branch=git_base_branch,
        integration_boundary=integration_boundary,
        deliverable_contract=deliverable_contract,
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
        reconciliation_result = _empty_task_reassignment_result()
        task = state_model.tasks.get(normalized_task_id)
        if task is None:
            raise TeamCommandValidationError(
                code="task_not_found",
                message=f"task {normalized_task_id} does not exist",
                correction_context={"task_id": normalized_task_id},
            )
        preparation_generation_before = task_git_work_context_generation(
            state_model,
            task.id,
        )
        is_owner = task.owner_agent_id == agent.id
        project = state_model.projects.get(task.project_id)
        is_same_team_manager = bool(
            project is not None
            and agent.team_id == project.team_id
            and _is_team_manager(state_model, agent)
        )
        from ..domain.role_task_queue import can_manage_unclaimed_role_task

        queue_manager = can_manage_unclaimed_role_task(state_model, task, agent.id)
        raw_state = normalized_updates.get("state")
        if task.owner_agent_id is None and raw_state in {
            "in_progress",
            "waiting_for_review",
            "finished",
        }:
            raise TeamCommandValidationError(
                code="role_task_not_assigned",
                message="role task must be assigned before execution or completion",
            )
        has_state = "state" in normalized_updates
        has_priority = "priority" in normalized_updates
        has_title = "title" in normalized_updates
        has_authored_by = "authored_by" in normalized_updates
        has_description = "description" in normalized_updates
        new_owner_id = normalized_updates.get("owner_agent_id")
        has_owner_reassignment = (
            isinstance(new_owner_id, str) and new_owner_id != task.owner_agent_id
        )
        has_dependency_edit = "dependency_task_ids" in normalized_updates
        has_git_base_edit = "git_base_branch" in normalized_updates
        has_integration_boundary_edit = "integration_boundary" in normalized_updates
        has_deliverable_contract_edit = "deliverable_contract" in normalized_updates
        has_deliverable_policy_request = (
            has_integration_boundary_edit or has_deliverable_contract_edit
        )
        next_integration_boundary = task.integration_boundary
        next_deliverable_contract = task.deliverable_contract
        if has_deliverable_policy_request:
            next_integration_boundary = bool(
                normalized_updates.get(
                    "integration_boundary",
                    task.integration_boundary,
                )
            )
            raw_deliverable_contract = normalized_updates.get(
                "deliverable_contract",
                task.deliverable_contract,
            )
            if not next_integration_boundary:
                raw_deliverable_contract = None
            try:
                next_deliverable_contract = (
                    TaskDeliverableContract.model_validate(raw_deliverable_contract)
                    if raw_deliverable_contract is not None
                    else None
                )
            except ValueError as exc:
                raise TeamCommandValidationError(
                    code="task_deliverable_contract_invalid",
                    message=str(exc),
                    correction_context={"task_id": task.id},
                ) from None
        has_deliverable_policy_edit = has_deliverable_policy_request and (
            next_integration_boundary != task.integration_boundary
            or next_deliverable_contract != task.deliverable_contract
        )
        transition_error = _task_integration_boundary_transition_error(
            state_model,
            task=task,
            proposed_integration_boundary=next_integration_boundary,
        )
        if transition_error is not None:
            raise TeamCommandValidationError(
                code="integration_boundary_requires_fresh_task",
                message=transition_error,
                correction_context={
                    "task_id": task.id,
                    "project_id": task.project_id,
                    "recommended_action": (
                        "create a fresh task with team_create_task and integration_boundary=true"
                    ),
                },
            )
        if task.mutation_policy is not None:
            changed_locked_fields: list[str] = []
            locked_fields = set(task.mutation_policy.locked_fields)
            if (
                has_dependency_edit
                and "dependency_task_ids" in locked_fields
                and list(normalized_updates["dependency_task_ids"]) != task.dependency_task_ids
            ):
                changed_locked_fields.append("dependency_task_ids")
            if (
                has_git_base_edit
                and "git_base_branch" in locked_fields
                and normalized_updates.get("git_base_branch") != task.git_base_branch
            ):
                changed_locked_fields.append("git_base_branch")
            if has_deliverable_policy_edit:
                if (
                    "integration_boundary" in locked_fields
                    and next_integration_boundary != task.integration_boundary
                ):
                    changed_locked_fields.append("integration_boundary")
                if (
                    "deliverable_contract" in locked_fields
                    and next_deliverable_contract != task.deliverable_contract
                ):
                    changed_locked_fields.append("deliverable_contract")
            if changed_locked_fields:
                raise TeamCommandValidationError(
                    code="task_mutation_policy_locked",
                    message=(
                        f"task {task.id} is workflow-managed and cannot change "
                        + ", ".join(changed_locked_fields)
                    ),
                    correction_context={
                        "task_id": task.id,
                        "policy": task.mutation_policy.model_dump(mode="json"),
                        "locked_requested_fields": changed_locked_fields,
                        "recommended_action": (
                            "reassign the same task if a different owner is needed"
                        ),
                    },
                )
        has_completion = "completion" in normalized_updates
        subordinate_finish_allowed = (
            has_state
            and not has_priority
            and not has_title
            and not has_description
            and not has_authored_by
            and not has_deliverable_policy_edit
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
                or has_authored_by
            )
            and not is_owner
            and not queue_manager
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
                        "waiting_for_review, or finished; workflows set "
                        "waiting_for_input for externally paused work"
                    ),
                    correction_context={
                        "state": next_state.value,
                        "allowed_states": _MCP_TASK_UPDATE_STATE_VALUES,
                    },
                )
            if next_state == TeamTaskState.BLOCKED or (
                task.state == TeamTaskState.BLOCKED and next_state != TeamTaskState.BLOCKED
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
        if has_git_base_edit:
            git_base_permitted = (
                is_owner
                or queue_manager
                or _supervises(state_model, agent.id, task.owner_agent_id)
                or _is_project_lead_for(state_model, agent.id, task.project_id)
                or is_same_team_manager
            )
            if not git_base_permitted:
                raise TeamCommandValidationError(
                    code="task_git_base_not_permitted",
                    message=(
                        f"agent {agent.id} is not permitted to edit the Git base for task {task.id}"
                    ),
                    correction_context={"task_id": task.id},
                )
        new_dependency_ids: list[str] | None = None
        if has_dependency_edit or has_deliverable_policy_edit:
            graph_permitted = (
                is_owner
                or queue_manager
                or _supervises(state_model, agent.id, task.owner_agent_id)
                or _is_project_lead_for(state_model, agent.id, task.project_id)
                or is_same_team_manager
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
            new_dependency_ids = (
                list(normalized_updates["dependency_task_ids"])
                if has_dependency_edit
                else list(task.dependency_task_ids)
            )
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
            if has_deliverable_policy_edit:
                if task.state not in _NON_TERMINAL_TASK_STATES:
                    raise TeamCommandValidationError(
                        code="task_deliverable_contract_terminal",
                        message=(
                            f"terminal task {task.id} cannot change its "
                            "integration deliverable contract"
                        ),
                        correction_context={"task_id": task.id},
                    )
                if task.deliverable_ids:
                    raise TeamCommandValidationError(
                        code="task_deliverable_contract_after_submission",
                        message=(
                            f"task {task.id} cannot change its integration "
                            "deliverable contract after a deliverable was submitted"
                        ),
                        correction_context={
                            "task_id": task.id,
                            "deliverable_ids": list(task.deliverable_ids),
                        },
                    )
                contract_error = _task_deliverable_contract_scope_error(
                    state_model,
                    task_id=task.id,
                    project_id=task.project_id,
                    dependency_task_ids=new_dependency_ids,
                    integration_boundary=next_integration_boundary,
                    deliverable_contract=next_deliverable_contract,
                )
                if contract_error is not None:
                    raise TeamCommandValidationError(
                        code="task_deliverable_contract_invalid",
                        message=contract_error,
                        correction_context={
                            "task_id": task.id,
                            "integration_boundary": (next_integration_boundary),
                            "deliverable_contract": (
                                next_deliverable_contract.model_dump(mode="json")
                                if next_deliverable_contract is not None
                                else None
                            ),
                        },
                    )
        if next_state == TeamTaskState.FINISHED:
            try:
                require_task_can_finish(
                    state_model,
                    task.id,
                    dependency_task_ids=new_dependency_ids,
                )
            except TaskAssessmentBoundaryError as exc:
                raise TeamCommandValidationError(
                    code=exc.code,
                    message=exc.message,
                    correction_context=exc.correction_context,
                ) from None
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
        if has_deliverable_policy_edit:
            if task.integration_boundary != next_integration_boundary:
                task.integration_boundary = next_integration_boundary
                changed = True
            if task.deliverable_contract != next_deliverable_contract:
                task.deliverable_contract = next_deliverable_contract
                changed = True
        if has_authored_by:
            identity = task_author_identity(normalized_updates["authored_by"])
            if task.authored_by != identity:
                task.authored_by = identity
                changed = True
        if has_git_base_edit:
            try:
                changed = (
                    _set_task_git_base_branch(
                        state_model,
                        task=task,
                        git_base_branch=normalized_updates.get("git_base_branch"),
                        git_default_branch_resolver=executor.git_default_branch_resolver,
                    )
                    or changed
                )
            except TaskGitBaseValidationError as exc:
                raise TeamCommandValidationError(
                    code=exc.code,
                    message=exc.message,
                    correction_context={"task_id": task.id},
                ) from None
        if has_owner_reassignment and isinstance(new_owner_id, str):
            _apply_task_owner_reassignment(
                state_model,
                task=task,
                new_owner_id=new_owner_id,
                actor_agent_id=agent.id,
                dispatch_id=resolved.dispatch_id,
                reconciliation_result=reconciliation_result,
                now=now,
            )
            changed = True
        if new_dependency_ids is not None and task.dependency_task_ids != new_dependency_ids:
            task.dependency_task_ids = new_dependency_ids
            changed = True
        if changed:
            invalidate_task_preparation_after_edit(
                state_model,
                task_id=task.id,
                preparation_generation_before=preparation_generation_before,
                changed_fields=sorted(normalized_updates),
                sender_agent_id=agent.id,
                result=reconciliation_result,
                now=now,
            )
            task.updated_at = now
            if task_state_can_wake_owner(task.state):
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
                    result=reconciliation_result,
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
        if changed and task.assignment_mode == "role" and task.owner_agent_id is None:
            from ..domain.role_task_queue import append_role_queue_wake_events

            append_role_queue_wake_events(
                state_model,
                result=reconciliation_result,
                now=now,
                project_id=task.project_id,
            )
        response_payload = {
            "task_id": task.id,
            "updated_task_ids": [task.id] if changed else [],
            "progress_made": changed,
            "created_blocker_ids": [],
            "created_inbox_item_ids": list(reconciliation_result["created_inbox_item_ids"]),
            "events": list(reconciliation_result["events"]),
            "resolved_blocker_ids": list(reconciliation_result["resolved_blocker_ids"]),
            "blocker_event_ids": list(reconciliation_result["blocker_event_ids"]),
            "resolved_blocker_event_ids": [],
            "automatically_handled_inbox_item_ids": [],
            "invalidated_git_work_context_ids": list(
                reconciliation_result.get("invalidated_git_work_context_ids") or []
            ),
            "created_preparation_invalidation_inbox_item_ids": list(
                reconciliation_result.get("created_preparation_invalidation_inbox_item_ids") or []
            ),
        }
        if "preparation_generation_before" in reconciliation_result:
            response_payload["preparation_generation_before"] = reconciliation_result[
                "preparation_generation_before"
            ]
            response_payload["preparation_generation_after"] = reconciliation_result[
                "preparation_generation_after"
            ]
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
        if changed and task_state_can_wake_owner(task.state):
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

    return executor._execute_team_command(
        context=context,
        normalized_payload=normalized_payload,
        handled_inbox_item_ids=normalized_handled_ids,
        apply_command=_apply,
        require_active_dispatch=True,
        reject_stale_high_priority_inbox=(
            normalized_updates.get("state") == TeamTaskState.FINISHED.value
        ),
        reject_stale_source_evidence=(
            normalized_updates.get("state") == TeamTaskState.FINISHED.value
        ),
    )


__all__ = ["execute_task_update_command"]
