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
    _team_record_state_value,
)
from ...models import (
    AgentTask,
    AgentStatus,
    TaskCancellation,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamRecordState,
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
    _apply_goal_update,
    _set_task_git_base_branch,
    _subordinate_task_finish_allowed,
    _task_deliverable_contract_scope_error,
    _task_graph_edit_error,
    _task_owner_reassignment_error,
)
from ..domain.blockers import (
    active_blockers_for_source,
    open_or_get_blocker,
    reconcile_task_blockers_after_cancellation,
    reconcile_task_blockers_after_reassignment,
    resolve_blocker,
    task_cancellation_blocking_handoff_ids,
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
from ..domain.dependency_composition_recovery import (
    release_dependency_composition_recovery_after_task_cancellation,
)
from ..domain.git_work_contexts import finalize_active_git_work_contexts_for_task
from ..domain.project_status import _recalculate_project_status
from ..domain.preparation_failures import (
    task_git_work_context_generation,
    task_preparation_generation,
)
from ..domain.role_task_allocation import (
    RoleTaskAllocationError,
    select_role_task_owner,
)
from ..domain.review_handoff import queue_actionable_inbox_item
from ..domain.task_assessment import (
    TaskAssessmentBoundaryError,
    assess_task,
    require_task_can_finish,
)
from ..domain.task_preparation_invalidation import (
    invalidate_task_preparation_after_edit,
)
from ..domain.task_supervision import queue_parent_resolution_transitions
from ..domain.submitted_integration_recovery import (
    SubmittedIntegrationBoundaryRecoveryError,
    apply_submitted_integration_boundary_recovery,
    plan_submitted_integration_boundary_recovery,
)
from ..domain.task_replacement import (
    TaskDependencyRebindingError,
    apply_cancelled_task_dependency_repair,
    apply_task_dependency_rebinding,
    invalidate_rebound_integration_git_contexts,
    plan_task_dependency_rebinding,
    plan_cancelled_task_dependency_repair,
    queue_rebound_integration_preparation_recovery,
)
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


class TeamTaskUpdateCommandService(TeamCommandExecutor):
    def reopen_task_for_rework_command(
        self,
        *,
        session_id: str,
        task_id: str,
        accepted_deliverable_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_deliverable_id = _required_text(
            accepted_deliverable_id,
            "accepted_deliverable_id",
        )
        normalized_reason = _required_text(reason, "reason")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_reopen_task_for_rework",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="task_rework_recovery",
        )
        normalized_payload = {
            "task_id": normalized_task_id,
            "accepted_deliverable_id": normalized_deliverable_id,
            "reason": normalized_reason,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = resolved.agent
            if agent is None:
                raise TeamCommandValidationError(
                    code="invalid_command_scope",
                    message=("team_reopen_task_for_rework requires a persistent Team agent"),
                )
            task = state.tasks.get(normalized_task_id)
            if task is None:
                raise TeamCommandValidationError(
                    code="task_not_found",
                    message=f"task {normalized_task_id} does not exist",
                    correction_context={"task_id": normalized_task_id},
                )
            project = state.projects.get(task.project_id)
            if project is None:
                raise TeamCommandValidationError(
                    code="task_project_not_found",
                    message=f"task {task.id} references missing project {task.project_id}",
                    correction_context={"task_id": task.id},
                )
            owner = state.agents.get(task.owner_agent_id)
            if (
                owner is None
                or owner.status != AgentStatus.ACTIVE
                or owner.team_id != project.team_id
            ):
                raise TeamCommandValidationError(
                    code="task_rework_owner_unavailable",
                    message=(
                        f"task {task.id} cannot be reopened because original owner "
                        f"{task.owner_agent_id} is not an active member of project team "
                        f"{project.team_id}"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "owner_agent_id": task.owner_agent_id,
                        "project_id": project.id,
                    },
                )
            team_manager_allowed = agent.team_id == project.team_id and _is_team_manager(
                state, agent
            )
            if not (
                agent.id == task.owner_agent_id
                or _is_project_lead_for(state, agent.id, project.id)
                or team_manager_allowed
            ):
                raise TeamCommandValidationError(
                    code="task_rework_reopen_forbidden",
                    message=(
                        f"agent {agent.id} must own task {task.id}, lead project "
                        f"{project.id}, or manage team {project.team_id} to reopen it"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "project_id": project.id,
                        "owner_agent_id": task.owner_agent_id,
                        "caller_agent_id": agent.id,
                    },
                )
            if task.state not in {
                TeamTaskState.FINISHED,
                TeamTaskState.ACCEPTED,
            }:
                raise TeamCommandValidationError(
                    code="task_not_resolved_for_rework",
                    message=(
                        f"task {task.id} is {task.state.value}; only a finished or "
                        "accepted task can enter accepted-deliverable rework"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "task_state": task.state.value,
                    },
                )
            assessment = assess_task(state, task.id)
            if assessment.resolution_status.value != "resolved":
                raise TeamCommandValidationError(
                    code="task_not_resolved_for_rework",
                    message=(
                        f"task {task.id} is not currently resolved ({assessment.resolution_reason})"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "assessment": assessment.model_dump(mode="json"),
                    },
                )
            deliverable = state.deliverables.get(normalized_deliverable_id)
            if deliverable is None:
                raise TeamCommandValidationError(
                    code="deliverable_not_found",
                    message=f"deliverable {normalized_deliverable_id} does not exist",
                )
            if deliverable.task_id != task.id or deliverable.id not in task.deliverable_ids:
                raise TeamCommandValidationError(
                    code="task_rework_deliverable_mismatch",
                    message=(f"deliverable {deliverable.id} does not belong to task {task.id}"),
                    correction_context={
                        "task_id": task.id,
                        "accepted_deliverable_id": deliverable.id,
                        "deliverable_task_id": deliverable.task_id,
                    },
                )
            if (
                deliverable.state != TeamRecordState.ACCEPTED
                or deliverable.superseded_by_deliverable_id is not None
            ):
                raise TeamCommandValidationError(
                    code="deliverable_not_currently_accepted",
                    message=(
                        f"deliverable {deliverable.id} must be a current accepted "
                        "deliverable before it can be reopened for rework"
                    ),
                    correction_context={
                        "deliverable_id": deliverable.id,
                        "deliverable_state": deliverable.state.value,
                        "superseded_by_deliverable_id": (deliverable.superseded_by_deliverable_id),
                    },
                )
            if deliverable.rework_requested_at is not None:
                raise TeamCommandValidationError(
                    code="deliverable_rework_already_requested",
                    message=(
                        f"deliverable {deliverable.id} already has an accepted rework request"
                    ),
                    correction_context={
                        "deliverable_id": deliverable.id,
                        "rework_requested_by_agent_id": (deliverable.rework_requested_by_agent_id),
                        "rework_requested_at": (deliverable.rework_requested_at.isoformat()),
                    },
                )

            now = utc_now()
            deliverable.rework_requested_at = now
            deliverable.rework_requested_by_agent_id = agent.id
            deliverable.rework_reason = normalized_reason
            task.completion = None
            _set_task_state(task, TeamTaskState.IN_PROGRESS, now=now)
            task.updated_at = now
            required_review_profile_ids = sorted(
                {
                    review.review_profile_id
                    for review_id in deliverable.review_request_ids
                    if (review := state.review_requests.get(review_id)) is not None
                    and review.state == TeamRecordState.ACCEPTED
                    and review.review_profile_id is not None
                    and review.superseded_by_review_request_id is None
                    and review.retired_at is None
                }
            )
            side_effects: dict[str, Any] = {
                "created_inbox_item_ids": [],
                "events": [],
            }
            queue_actionable_inbox_item(
                state,
                recipient_agent_id=task.owner_agent_id,
                sender_agent_id=agent.id,
                task_id=task.id,
                priority=TeamInboxPriority.HIGH,
                payload={
                    "kind": "accepted_deliverable_rework_requested",
                    "task_id": task.id,
                    "deliverable_id": deliverable.id,
                    "supersedes_deliverable_id": deliverable.id,
                    "summary": (
                        "Previously accepted work requires a same-task replacement "
                        "and fresh reviews."
                    ),
                    "reason": normalized_reason,
                    "required_tool": "team_submit_deliverable_for_review",
                    "next_action": (
                        "Submit a replacement for this task with "
                        f"deliverable.supersedes_deliverable_id={deliverable.id} "
                        "and at least one fresh formal review."
                    ),
                    "retained_review_request_ids": list(deliverable.review_request_ids),
                    "required_review_profile_ids": required_review_profile_ids,
                },
                result=side_effects,
                now=now,
            )
            _recalculate_project_status(state, task.project_id)
            created_inbox_ids = list(side_effects.get("created_inbox_item_ids") or [])
            return TeamCommandApplyResult(
                response_payload={
                    "task_id": task.id,
                    "task_state": task.state.value,
                    "accepted_deliverable_id": deliverable.id,
                    "supersedes_deliverable_id": deliverable.id,
                    "rework_reason": normalized_reason,
                    "rework_requested_by_agent_id": agent.id,
                    "rework_requested_at": now.isoformat(),
                    "retained_review_request_ids": list(deliverable.review_request_ids),
                    "required_review_profile_ids": required_review_profile_ids,
                    "created_inbox_item_ids": created_inbox_ids,
                    "events": list(side_effects.get("events") or []),
                    "intent_recorded": True,
                    "async_effect_pending": bool(created_inbox_ids),
                    "progress_made": True,
                },
                created_domain_ids=created_inbox_ids,
                progress_classification="task_rework_recovery",
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=False,
        )

    def cancel_task_command(
        self,
        *,
        session_id: str,
        task_id: str,
        reason_code: str,
        reason: str,
        request_id: str,
        successor_task_id: str | None = None,
        rebind_dependents: bool = False,
        expected_dependent_task_ids: Sequence[str] | None = None,
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
        if not isinstance(rebind_dependents, bool):
            raise ValueError("rebind_dependents must be a boolean")
        normalized_rebind_dependents = rebind_dependents
        normalized_expected_dependent_ids = (
            None
            if expected_dependent_task_ids is None
            else tuple(
                sorted(
                    _unique_text_list(
                        expected_dependent_task_ids,
                        "expected_dependent_task_ids",
                    )
                )
            )
        )
        if normalized_rebind_dependents and normalized_expected_dependent_ids is None:
            raise ValueError(
                "expected_dependent_task_ids is required when rebind_dependents is true"
            )
        if not normalized_rebind_dependents and normalized_expected_dependent_ids:
            raise ValueError(
                "expected_dependent_task_ids requires rebind_dependents=true"
            )
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
            "rebind_dependents": normalized_rebind_dependents,
            "expected_dependent_task_ids": (
                list(normalized_expected_dependent_ids)
                if normalized_expected_dependent_ids is not None
                else None
            ),
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
            if (
                task.mutation_policy is not None
                and not task.mutation_policy.allow_agent_cancellation
            ):
                raise TeamCommandValidationError(
                    code="task_mutation_policy_cancellation_locked",
                    message=(
                        f"task {task.id} is workflow-managed and cannot be cancelled "
                        "through an agent dispatch"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "policy": task.mutation_policy.model_dump(mode="json"),
                        "recommended_action": (
                            "reassign and complete the same task, or request an audited "
                            "operator disposition"
                        ),
                    },
                )
            parent = state_model.tasks.get(task.parent_task_id or "")
            project = state_model.projects.get(task.project_id)
            team_manager_allowed = (
                project is not None
                and actor.team_id == project.team_id
                and _is_team_manager(state_model, actor)
            )
            authorized = (
                task.owner_agent_id == actor.id
                or (task.owner_agent_id is None and task.created_by_agent_id == actor.id)
                or (parent is not None and parent.owner_agent_id == actor.id)
                or _is_project_lead_for(state_model, actor.id, task.project_id)
                or team_manager_allowed
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
            if normalized_rebind_dependents and not (
                _is_project_lead_for(state_model, actor.id, task.project_id)
                or team_manager_allowed
            ):
                raise TeamCommandValidationError(
                    code="task_replacement_not_permitted",
                    message=(
                        "replacing task dependencies requires the task's project lead "
                        "or a Team manager"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "project_id": task.project_id,
                        "actor_agent_id": actor.id,
                        "recommended_action": (
                            "cancel without rebind_dependents, or ask the project lead "
                            "or Team manager to replace the task in the graph"
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
                        f"task {task.id} cannot be cancelled while descendants remain unresolved"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "unresolved_descendant_ids": list(assessment.unresolved_descendant_ids),
                    },
                )
            (
                blocking_acceptance_ids,
                blocking_review_request_ids,
            ) = task_cancellation_blocking_handoff_ids(
                state_model,
                acceptance_ids=assessment.active_acceptance_ids,
                review_request_ids=assessment.active_review_request_ids,
            )
            if blocking_acceptance_ids or blocking_review_request_ids:
                raise TeamCommandValidationError(
                    code="task_cancellation_active_handoff",
                    message=(
                        f"task {task.id} cannot be cancelled while acceptance or review "
                        "handoffs remain active"
                    ),
                    correction_context={
                        "task_id": task.id,
                        "active_acceptance_ids": blocking_acceptance_ids,
                        "active_review_request_ids": blocking_review_request_ids,
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
            if normalized_rebind_dependents and normalized_successor_id is None:
                raise TeamCommandValidationError(
                    code="task_replacement_successor_required",
                    message="rebind_dependents requires a same-project successor task",
                    correction_context={"task_id": task.id},
                )

            replacement_plan = None
            preparation_generations_before: dict[str, str] = {}
            readiness_before: dict[str, str] = {}
            if normalized_rebind_dependents and normalized_successor_id is not None:
                try:
                    replacement_plan = plan_task_dependency_rebinding(
                        state_model,
                        predecessor_task_id=task.id,
                        successor_task_id=normalized_successor_id,
                    )
                    assert normalized_expected_dependent_ids is not None
                    if (
                        replacement_plan.dependent_task_ids
                        != normalized_expected_dependent_ids
                    ):
                        raise TaskDependencyRebindingError(
                            "task replacement affected set changed; expected "
                            f"{list(normalized_expected_dependent_ids)!r}, current "
                            f"{list(replacement_plan.dependent_task_ids)!r}",
                            code="task_replacement_affected_set_mismatch",
                            affected_task_ids=replacement_plan.dependent_task_ids,
                        )
                except TaskDependencyRebindingError as exc:
                    raise TeamCommandValidationError(
                        code=exc.code,
                        message=str(exc),
                        correction_context={
                            "task_id": task.id,
                            "successor_task_id": normalized_successor_id,
                            "dependent_task_id": exc.dependent_task_id,
                            "current_dependent_task_ids": list(
                                exc.affected_task_ids
                            ),
                        },
                    ) from None
                preparation_generations_before = {
                    dependent_id: task_preparation_generation(state_model, dependent_id)
                    for dependent_id in replacement_plan.dependent_task_ids
                }
                readiness_before = {
                    dependent_id: assess_task(state_model, dependent_id).readiness.value
                    for dependent_id in replacement_plan.dependent_task_ids
                }
            now = utc_now()
            before_event_ids = set(state_model.blocker_events)
            rebound_dependent_task_ids = (
                apply_task_dependency_rebinding(
                    state_model,
                    plan=replacement_plan,
                    now=now,
                )
                if replacement_plan is not None
                else []
            )
            invalidated_git_context_ids_by_task = (
                invalidate_rebound_integration_git_contexts(
                    state_model,
                    plan=replacement_plan,
                    now=now,
                )
                if replacement_plan is not None
                else {}
            )
            affected_integration_boundary_task_ids = (
                list(replacement_plan.affected_integration_boundary_task_ids)
                if replacement_plan is not None
                else []
            )
            invalidated_git_work_context_ids = sorted(
                context_id
                for context_ids in invalidated_git_context_ids_by_task.values()
                for context_id in context_ids
            )
            task.cancellation = TaskCancellation(
                reason_code=normalized_reason_code,
                reason=normalized_reason,
                actor_kind="agent",
                actor_id=actor.id,
                successor_task_id=normalized_successor_id,
                rebind_dependents=normalized_rebind_dependents,
                rebound_dependent_task_ids=rebound_dependent_task_ids,
                rebound_integration_boundary_task_ids=(
                    affected_integration_boundary_task_ids
                ),
                invalidated_git_work_context_ids=invalidated_git_work_context_ids,
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
                "rebind_dependents": normalized_rebind_dependents,
                "expected_dependent_task_ids": (
                    list(normalized_expected_dependent_ids or ())
                ),
                "rebound_dependent_task_ids": rebound_dependent_task_ids,
                "affected_integration_boundary_task_ids": (
                    affected_integration_boundary_task_ids
                ),
                "invalidated_git_work_context_ids": invalidated_git_work_context_ids,
                "invalidated_git_context_ids_by_task": invalidated_git_context_ids_by_task,
                "resolved_blocker_ids": [],
                "git_context_finalization": git_context_finalization,
                "progress_made": True,
            }
            released_recovery = release_dependency_composition_recovery_after_task_cancellation(
                state_model,
                task_id=task.id,
                actor_agent_id=actor.id,
                dispatch_id=resolved.dispatch_id,
                now=now,
            )
            if released_recovery is not None:
                result["released_dependency_composition_recovery_id"] = released_recovery.id
            reconcile_task_blockers_after_cancellation(
                state_model,
                task_id=task.id,
                actor_agent_id=actor.id,
                dispatch_id=resolved.dispatch_id,
                result=result,
                now=now,
            )
            queue_parent_resolution_transitions(
                state_model,
                child_task_id=task.id,
                sender_agent_id=actor.id,
                result=result,
                now=now,
            )
            blocker_event_ids = [
                event_id
                for event_id in state_model.blocker_events
                if event_id not in before_event_ids
            ]
            result["blocker_event_ids"] = blocker_event_ids
            if replacement_plan is not None:
                result["dependency_rebindings"] = [
                    {
                        "dependent_task_id": dependent_id,
                        "predecessor_task_id": task.id,
                        "successor_task_id": normalized_successor_id,
                        "dependency_task_ids": list(
                            state_model.tasks[dependent_id].dependency_task_ids
                        ),
                    }
                    for dependent_id in sorted(
                        replacement_plan.dependency_task_ids_by_dependent
                    )
                ]
                result["required_contribution_rebindings"] = [
                    {
                        "integration_task_id": integration_task_id,
                        "predecessor_task_id": task.id,
                        "successor_task_id": normalized_successor_id,
                        "required_contribution_task_ids": list(contribution_ids),
                    }
                    for integration_task_id, contribution_ids in (
                        replacement_plan.required_contribution_task_ids_by_integration_task.items()
                    )
                ]
                result["preparation_generation_changes"] = {
                    dependent_id: {
                        "before": preparation_generations_before[dependent_id],
                        "after": task_preparation_generation(state_model, dependent_id),
                    }
                    for dependent_id in rebound_dependent_task_ids
                }
                result["newly_actionable_dependent_task_ids"] = [
                    dependent_id
                    for dependent_id in rebound_dependent_task_ids
                    if readiness_before[dependent_id] != "actionable"
                    and assess_task(state_model, dependent_id).readiness.value == "actionable"
                ]
                queue_rebound_integration_preparation_recovery(
                    state_model,
                    plan=replacement_plan,
                    invalidated_git_context_ids_by_task=(
                        invalidated_git_context_ids_by_task
                    ),
                    preparation_generations_before=preparation_generations_before,
                    sender_agent_id=actor.id,
                    result=result,
                    now=now,
                )
            _maybe_mark_idle_context_rotation_due(
                state_model,
                agent_id=task.owner_agent_id,
                now=now,
                result=result,
            )
            _recalculate_project_status(state_model, task.project_id)
            return TeamCommandApplyResult(
                response_payload=result,
                created_domain_ids=[
                    task.id,
                    *rebound_dependent_task_ids,
                    *invalidated_git_work_context_ids,
                    *list(result.get("created_preparation_recovery_inbox_item_ids") or []),
                    *blocker_event_ids,
                ],
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

    def repair_cancelled_task_dependencies_command(
        self,
        *,
        session_id: str,
        predecessor_task_id: str,
        successor_task_id: str,
        expected_dependent_task_ids: Sequence[str],
        reason: str,
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_predecessor_id = _required_text(
            predecessor_task_id,
            "predecessor_task_id",
        )
        normalized_successor_id = _required_text(successor_task_id, "successor_task_id")
        normalized_expected_dependent_ids = tuple(
            sorted(
                _unique_text_list(
                    expected_dependent_task_ids,
                    "expected_dependent_task_ids",
                )
            )
        )
        normalized_reason = _required_text(reason, "reason")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_repair_cancelled_task_dependencies",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="task_dependency_graph_repair",
        )
        normalized_payload = {
            "task_id": normalized_predecessor_id,
            "predecessor_task_id": normalized_predecessor_id,
            "successor_task_id": normalized_successor_id,
            "expected_dependent_task_ids": list(normalized_expected_dependent_ids),
            "reason": normalized_reason,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(
                resolved,
                "team_repair_cancelled_task_dependencies",
            )
            predecessor = state_model.tasks.get(normalized_predecessor_id)
            if predecessor is None:
                raise TeamCommandValidationError(
                    code="task_dependency_repair_predecessor_not_found",
                    message=(
                        f"cancelled predecessor task {normalized_predecessor_id} "
                        "does not exist"
                    ),
                )
            project = state_model.projects.get(predecessor.project_id)
            team_manager_allowed = (
                project is not None
                and actor.team_id == project.team_id
                and _is_team_manager(state_model, actor)
            )
            if not (
                _is_project_lead_for(state_model, actor.id, predecessor.project_id)
                or team_manager_allowed
            ):
                raise TeamCommandValidationError(
                    code="task_dependency_repair_not_permitted",
                    message=(
                        "repairing dependencies behind a cancelled task requires "
                        "the project lead or a Team manager"
                    ),
                    correction_context={
                        "predecessor_task_id": predecessor.id,
                        "project_id": predecessor.project_id,
                        "actor_agent_id": actor.id,
                    },
                )
            try:
                plan = plan_cancelled_task_dependency_repair(
                    state_model,
                    predecessor_task_id=predecessor.id,
                    successor_task_id=normalized_successor_id,
                    expected_dependent_task_ids=normalized_expected_dependent_ids,
                )
                result: dict[str, Any] = {"events": []}
                changed_domain_ids, state_changed = (
                    apply_cancelled_task_dependency_repair(
                        state_model,
                        plan=plan,
                        actor_kind="agent",
                        actor_id=actor.id,
                        reason=normalized_reason,
                        expected_dependent_task_ids=normalized_expected_dependent_ids,
                        result=result,
                        now=utc_now(),
                    )
                )
            except TaskDependencyRebindingError as exc:
                raise TeamCommandValidationError(
                    code=(
                        exc.code
                        if exc.code.startswith("task_dependency_repair_")
                        else "task_dependency_repair_graph_invalid"
                    ),
                    message=str(exc),
                    correction_context={
                        "predecessor_task_id": predecessor.id,
                        "successor_task_id": normalized_successor_id,
                        "dependent_task_id": exc.dependent_task_id,
                        "current_dependent_task_ids": list(exc.affected_task_ids),
                    },
                ) from None
            if state_changed:
                _recalculate_project_status(state_model, predecessor.project_id)
            return TeamCommandApplyResult(
                response_payload=result,
                created_domain_ids=changed_domain_ids,
                progress_classification="task_dependency_graph_repair",
                state_changed=state_changed,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )

    def recover_submitted_integration_boundary_command(
        self,
        *,
        session_id: str,
        predecessor_task_id: str,
        successor_task_id: str,
        integration_task_id: str,
        replacement_task_id: str,
        expected_dependency_task_ids: Sequence[str],
        expected_required_contribution_task_ids: Sequence[str],
        reason: str,
        request_id: str,
        git_base_commit_sha: str | None = None,
        git_base_branch: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_predecessor_id = _required_text(
            predecessor_task_id,
            "predecessor_task_id",
        )
        normalized_successor_id = _required_text(successor_task_id, "successor_task_id")
        normalized_integration_id = _required_text(
            integration_task_id,
            "integration_task_id",
        )
        normalized_replacement_id = _required_text(
            replacement_task_id,
            "replacement_task_id",
        )
        normalized_expected_dependency_ids = tuple(
            sorted(
                _unique_text_list(
                    expected_dependency_task_ids,
                    "expected_dependency_task_ids",
                )
            )
        )
        normalized_expected_contribution_ids = tuple(
            sorted(
                _unique_text_list(
                    expected_required_contribution_task_ids,
                    "expected_required_contribution_task_ids",
                )
            )
        )
        normalized_reason = _required_text(reason, "reason")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_git_base_commit_sha = _optional_text(git_base_commit_sha)
        normalized_git_base_branch = _optional_text(git_base_branch)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_recover_submitted_integration_boundary",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="submitted_integration_boundary_recovery",
        )
        normalized_payload = {
            "task_id": normalized_integration_id,
            "predecessor_task_id": normalized_predecessor_id,
            "successor_task_id": normalized_successor_id,
            "integration_task_id": normalized_integration_id,
            "replacement_task_id": normalized_replacement_id,
            "expected_dependency_task_ids": list(
                normalized_expected_dependency_ids
            ),
            "expected_required_contribution_task_ids": list(
                normalized_expected_contribution_ids
            ),
            "reason": normalized_reason,
            "git_base_commit_sha": normalized_git_base_commit_sha,
            "git_base_branch": normalized_git_base_branch,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(
                resolved,
                "team_recover_submitted_integration_boundary",
            )
            integration = state_model.tasks.get(normalized_integration_id)
            if integration is None:
                raise TeamCommandValidationError(
                    code="submitted_integration_recovery_boundary_not_found",
                    message=(
                        f"integration boundary task {normalized_integration_id} "
                        "does not exist"
                    ),
                )
            project = state_model.projects.get(integration.project_id)
            team_manager_allowed = (
                project is not None
                and actor.team_id == project.team_id
                and _is_team_manager(state_model, actor)
            )
            if not (
                _is_project_lead_for(state_model, actor.id, integration.project_id)
                or team_manager_allowed
            ):
                raise TeamCommandValidationError(
                    code="submitted_integration_recovery_not_permitted",
                    message=(
                        "recovering submitted integration history requires the "
                        "project lead or a Team manager"
                    ),
                    correction_context={
                        "integration_task_id": integration.id,
                        "project_id": integration.project_id,
                        "actor_agent_id": actor.id,
                    },
                )
            try:
                plan = plan_submitted_integration_boundary_recovery(
                    state_model,
                    predecessor_task_id=normalized_predecessor_id,
                    successor_task_id=normalized_successor_id,
                    integration_task_id=integration.id,
                    replacement_task_id=normalized_replacement_id,
                    expected_dependency_task_ids=(
                        normalized_expected_dependency_ids
                    ),
                    expected_required_contribution_task_ids=(
                        normalized_expected_contribution_ids
                    ),
                    reason=normalized_reason,
                    git_base_commit_sha=normalized_git_base_commit_sha,
                    git_base_branch=normalized_git_base_branch,
                    actor_kind="agent",
                )
                result: dict[str, Any] = {"events": []}
                before_blocker_event_ids = set(state_model.blocker_events)
                changed_domain_ids, state_changed = (
                    apply_submitted_integration_boundary_recovery(
                        state_model,
                        plan=plan,
                        actor_kind="agent",
                        actor_id=actor.id,
                        result=result,
                        now=utc_now(),
                    )
                )
            except SubmittedIntegrationBoundaryRecoveryError as exc:
                raise TeamCommandValidationError(
                    code=exc.code,
                    message=str(exc),
                    correction_context={
                        "predecessor_task_id": normalized_predecessor_id,
                        "successor_task_id": normalized_successor_id,
                        "integration_task_id": normalized_integration_id,
                        "replacement_task_id": normalized_replacement_id,
                        "current_dependency_task_ids": list(
                            exc.current_dependency_task_ids
                        ),
                        "current_required_contribution_task_ids": list(
                            exc.current_required_contribution_task_ids
                        ),
                    },
                ) from None
            blocker_event_ids = sorted(
                set(state_model.blocker_events) - before_blocker_event_ids
            )
            if blocker_event_ids:
                result["blocker_event_ids"] = blocker_event_ids
                changed_domain_ids.extend(blocker_event_ids)
            if state_changed:
                replacement = state_model.tasks[normalized_replacement_id]
                if assess_task(state_model, replacement.id).readiness.value == "actionable":
                    _append_agent_wake_requested_event(
                        state_model,
                        agent_id=replacement.owner_agent_id,
                        result=result,
                        reason="task_ready",
                        now=utc_now(),
                        delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                        payload_extra={
                            "task_id": replacement.id,
                            "recovery_kind": (
                                "submitted_integration_boundary_recovery"
                            ),
                        },
                    )
                _recalculate_project_status(state_model, integration.project_id)
            return TeamCommandApplyResult(
                response_payload=result,
                created_domain_ids=list(dict.fromkeys(changed_domain_ids)),
                created_event_ids=blocker_event_ids,
                progress_classification=(
                    "submitted_integration_boundary_recovery"
                ),
                state_changed=state_changed,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )

    def reassign_task_for_role_command(
        self,
        *,
        session_id: str,
        task_id: str,
        role_id: str,
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_role_id = _required_text(role_id, "role_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_reassign_task_for_role",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "task_id": normalized_task_id,
            "role_id": normalized_role_id,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state_model: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_reassign_task_for_role")
            task = state_model.tasks.get(normalized_task_id)
            if task is None:
                raise TeamCommandValidationError(
                    code="task_not_found",
                    message=f"task {normalized_task_id} does not exist",
                    correction_context={"task_id": normalized_task_id},
                )
            previous_owner_id = task.owner_agent_id
            try:
                selection = select_role_task_owner(
                    state_model,
                    actor=agent,
                    project_id=task.project_id,
                    role_id=normalized_role_id,
                    candidate_authorization_error=lambda candidate: (
                        _task_owner_reassignment_error(
                            state_model,
                            actor=agent,
                            task=task,
                            new_owner_id=candidate.id,
                        )
                    ),
                )
            except RoleTaskAllocationError as exc:
                raise TeamCommandValidationError(
                    code=exc.code,
                    message=str(exc),
                    correction_context=exc.correction_context,
                ) from None

            selected_owner_id = selection.selected_owner_agent_id
            if selected_owner_id == previous_owner_id:
                return TeamCommandApplyResult(
                    response_payload={
                        "task_id": task.id,
                        "updated_task_ids": [],
                        "progress_made": False,
                        **selection.response_payload(
                            outcome="unchanged",
                            previous_owner_agent_id=previous_owner_id,
                        ),
                    },
                    progress_classification="none",
                    state_changed=False,
                )

            reconciliation_result = _empty_task_reassignment_result()
            now = utc_now()
            _apply_task_owner_reassignment(
                state_model,
                task=task,
                new_owner_id=selected_owner_id,
                actor_agent_id=agent.id,
                dispatch_id=resolved.dispatch_id,
                reconciliation_result=reconciliation_result,
                now=now,
            )
            task.updated_at = now
            _recalculate_project_status(state_model, task.project_id)
            response_payload = {
                "task_id": task.id,
                "updated_task_ids": [task.id],
                "progress_made": True,
                "created_blocker_ids": [],
                "created_inbox_item_ids": list(
                    reconciliation_result["created_inbox_item_ids"]
                ),
                "events": list(reconciliation_result["events"]),
                "resolved_blocker_ids": list(reconciliation_result["resolved_blocker_ids"]),
                "blocker_event_ids": list(reconciliation_result["blocker_event_ids"]),
                "resolved_blocker_event_ids": [],
                "automatically_handled_inbox_item_ids": [],
                **selection.response_payload(
                    outcome="reassigned",
                    previous_owner_agent_id=previous_owner_id,
                ),
            }
            if task_state_can_wake_owner(task.state):
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
                created_domain_ids=[
                    task.id,
                    *response_payload["blocker_event_ids"],
                    *response_payload["created_inbox_item_ids"],
                ],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
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
            if task.mutation_policy is not None:
                changed_locked_fields: list[str] = []
                locked_fields = set(task.mutation_policy.locked_fields)
                if (
                    has_dependency_edit
                    and "dependency_task_ids" in locked_fields
                    and list(normalized_updates["dependency_task_ids"])
                    != task.dependency_task_ids
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
                            f"agent {agent.id} is not permitted to edit the Git base "
                            f"for task {task.id}"
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
                            git_default_branch_resolver=self.git_default_branch_resolver,
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
                    reconciliation_result.get(
                        "created_preparation_invalidation_inbox_item_ids"
                    )
                    or []
                ),
            }
            if "preparation_generation_before" in reconciliation_result:
                response_payload["preparation_generation_before"] = (
                    reconciliation_result["preparation_generation_before"]
                )
                response_payload["preparation_generation_after"] = (
                    reconciliation_result["preparation_generation_after"]
                )
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

        return self._execute_team_command(
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
            reject_stale_high_priority_inbox=(normalized_state == TeamRecordState.ACCEPTED.value),
        )


__all__ = ["TeamTaskUpdateCommandService"]
