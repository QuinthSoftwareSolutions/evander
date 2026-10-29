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
    PersistentAgent,
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
    reconcile_task_blockers_after_reassignment,
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
        return _TaskUpdateApplication(
            executor=executor,
            state=state_model,
            resolved=resolved,
            task_id=normalized_task_id,
            updates=normalized_updates,
        ).apply()

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


class _TaskUpdateApplication:
    """Validate and apply one task update inside the command executor's transaction."""

    agent: PersistentAgent
    task: AgentTask
    reconciliation_result: dict[str, Any]
    preparation_generation_before: str
    is_owner: bool
    is_same_team_manager: bool
    queue_manager: bool
    raw_state: Any
    has_state: bool
    has_priority: bool
    has_title: bool
    has_authored_by: bool
    has_description: bool
    new_owner_id: str | None
    has_owner_reassignment: bool
    has_dependency_edit: bool
    has_git_base_edit: bool
    has_deliverable_policy_request: bool
    next_integration_boundary: bool
    next_deliverable_contract: TaskDeliverableContract | None
    has_deliverable_policy_edit: bool
    has_completion: bool
    next_state: TeamTaskState | None
    new_dependency_ids: list[str] | None
    changed: bool
    now: datetime

    def __init__(
        self,
        *,
        executor: TeamCommandExecutor,
        state: TeamState,
        resolved: _ResolvedTeamCommandContext,
        task_id: str,
        updates: dict[str, Any],
    ) -> None:
        self.executor = executor
        self.state = state
        self.resolved = resolved
        self.task_id = task_id
        self.updates = updates

    def apply(self) -> TeamCommandApplyResult:
        self._load_task()
        self._describe_request()
        self._validate_deliverable_policy()
        self._validate_locked_fields()
        self._validate_owner()
        self._validate_state()
        self._validate_reassignment()
        self._validate_git_base_access()
        self._validate_graph()
        self._validate_completion()
        self._apply_edits()
        self._reconcile_edits()
        return self._build_result()

    def _load_task(self) -> None:
        self.agent = _require_command_agent(self.resolved, "team_update_task")
        self.reconciliation_result = _empty_task_reassignment_result()
        task = self.state.tasks.get(self.task_id)
        if task is None:
            raise TeamCommandValidationError(
                code="task_not_found",
                message=f"task {self.task_id} does not exist",
                correction_context={"task_id": self.task_id},
            )
        self.task = task
        self.preparation_generation_before = task_git_work_context_generation(
            self.state,
            self.task.id,
        )
        self.is_owner = self.task.owner_agent_id == self.agent.id
        project = self.state.projects.get(self.task.project_id)
        self.is_same_team_manager = bool(
            project is not None
            and self.agent.team_id == project.team_id
            and _is_team_manager(self.state, self.agent)
        )
        from ..domain.role_task_queue import can_manage_unclaimed_role_task

        self.queue_manager = can_manage_unclaimed_role_task(self.state, self.task, self.agent.id)
        self.raw_state = self.updates.get("state")

    def _describe_request(self) -> None:
        if self.task.owner_agent_id is None and self.raw_state in {
            "in_progress",
            "waiting_for_review",
            "finished",
        }:
            raise TeamCommandValidationError(
                code="role_task_not_assigned",
                message="role task must be assigned before execution or completion",
            )
        self.has_state = "state" in self.updates
        self.has_priority = "priority" in self.updates
        self.has_title = "title" in self.updates
        self.has_authored_by = "authored_by" in self.updates
        self.has_description = "description" in self.updates
        self.new_owner_id = self.updates.get("owner_agent_id")
        self.has_owner_reassignment = (
            isinstance(self.new_owner_id, str) and self.new_owner_id != self.task.owner_agent_id
        )
        self.has_dependency_edit = "dependency_task_ids" in self.updates
        self.has_git_base_edit = "git_base_branch" in self.updates
        has_integration_boundary_edit = "integration_boundary" in self.updates
        has_deliverable_contract_edit = "deliverable_contract" in self.updates
        self.has_deliverable_policy_request = (
            has_integration_boundary_edit or has_deliverable_contract_edit
        )
        self.next_integration_boundary = self.task.integration_boundary
        self.next_deliverable_contract = self.task.deliverable_contract

    def _validate_deliverable_policy(self) -> None:
        if self.has_deliverable_policy_request:
            self.next_integration_boundary = bool(
                self.updates.get(
                    "integration_boundary",
                    self.task.integration_boundary,
                )
            )
            raw_deliverable_contract = self.updates.get(
                "deliverable_contract",
                self.task.deliverable_contract,
            )
            if not self.next_integration_boundary:
                raw_deliverable_contract = None
            try:
                self.next_deliverable_contract = (
                    TaskDeliverableContract.model_validate(raw_deliverable_contract)
                    if raw_deliverable_contract is not None
                    else None
                )
            except ValueError as exc:
                raise TeamCommandValidationError(
                    code="task_deliverable_contract_invalid",
                    message=str(exc),
                    correction_context={"task_id": self.task.id},
                ) from None
        self.has_deliverable_policy_edit = self.has_deliverable_policy_request and (
            self.next_integration_boundary != self.task.integration_boundary
            or self.next_deliverable_contract != self.task.deliverable_contract
        )
        transition_error = _task_integration_boundary_transition_error(
            self.state,
            task=self.task,
            proposed_integration_boundary=self.next_integration_boundary,
        )
        if transition_error is not None:
            raise TeamCommandValidationError(
                code="integration_boundary_requires_fresh_task",
                message=transition_error,
                correction_context={
                    "task_id": self.task.id,
                    "project_id": self.task.project_id,
                    "recommended_action": (
                        "create a fresh task with team_create_task and integration_boundary=true"
                    ),
                },
            )

    def _validate_locked_fields(self) -> None:
        if self.task.mutation_policy is not None:
            changed_locked_fields: list[str] = []
            locked_fields = set(self.task.mutation_policy.locked_fields)
            if (
                self.has_dependency_edit
                and "dependency_task_ids" in locked_fields
                and list(self.updates["dependency_task_ids"]) != self.task.dependency_task_ids
            ):
                changed_locked_fields.append("dependency_task_ids")
            if (
                self.has_git_base_edit
                and "git_base_branch" in locked_fields
                and self.updates.get("git_base_branch") != self.task.git_base_branch
            ):
                changed_locked_fields.append("git_base_branch")
            if self.has_deliverable_policy_edit:
                if (
                    "integration_boundary" in locked_fields
                    and self.next_integration_boundary != self.task.integration_boundary
                ):
                    changed_locked_fields.append("integration_boundary")
                if (
                    "deliverable_contract" in locked_fields
                    and self.next_deliverable_contract != self.task.deliverable_contract
                ):
                    changed_locked_fields.append("deliverable_contract")
            if changed_locked_fields:
                raise TeamCommandValidationError(
                    code="task_mutation_policy_locked",
                    message=(
                        f"task {self.task.id} is workflow-managed and cannot change "
                        + ", ".join(changed_locked_fields)
                    ),
                    correction_context={
                        "task_id": self.task.id,
                        "policy": self.task.mutation_policy.model_dump(mode="json"),
                        "locked_requested_fields": changed_locked_fields,
                        "recommended_action": (
                            "reassign the same task if a different owner is needed"
                        ),
                    },
                )

    def _validate_owner(self) -> None:
        self.has_completion = "completion" in self.updates
        subordinate_finish_allowed = (
            self.has_state
            and not self.has_priority
            and not self.has_title
            and not self.has_description
            and not self.has_authored_by
            and not self.has_deliverable_policy_edit
            and _subordinate_task_finish_allowed(
                self.state,
                actor=self.agent,
                task=self.task,
                raw_state=self.raw_state,
                new_owner_id=self.new_owner_id if self.has_owner_reassignment else None,
            )
        )
        if (
            (
                self.has_state
                or self.has_priority
                or self.has_title
                or self.has_description
                or self.has_completion
                or self.has_authored_by
            )
            and not self.is_owner
            and not self.queue_manager
            and not subordinate_finish_allowed
        ):
            raise TeamCommandValidationError(
                code="task_update_not_owner",
                message=f"agent {self.agent.id} cannot update task {self.task.id}",
                correction_context={
                    "task_id": self.task.id,
                    "caller_agent_id": self.agent.id,
                    "owner_agent_id": self.task.owner_agent_id,
                },
            )

    def _validate_state(self) -> None:
        self.next_state: TeamTaskState | None = None
        if self.has_state:
            try:
                self.next_state = TeamTaskState(str(self.raw_state))
            except ValueError as exc:
                raise TeamCommandValidationError(
                    code="invalid_task_state",
                    message=f"task update has invalid state {self.raw_state}",
                    correction_context={
                        "state": self.raw_state,
                        "allowed_states": _MCP_TASK_UPDATE_STATE_VALUES,
                    },
                ) from exc
            if self.next_state.value not in _MCP_TASK_UPDATE_STATE_VALUES:
                raise TeamCommandValidationError(
                    code="invalid_task_state",
                    message=(
                        "team_update_task.state must be pending, in_progress, "
                        "waiting_for_review, or finished; workflows set "
                        "waiting_for_input for externally paused work"
                    ),
                    correction_context={
                        "state": self.next_state.value,
                        "allowed_states": _MCP_TASK_UPDATE_STATE_VALUES,
                    },
                )
            if self.next_state == TeamTaskState.BLOCKED or (
                self.task.state == TeamTaskState.BLOCKED
                and self.next_state != TeamTaskState.BLOCKED
            ):
                raise TeamCommandValidationError(
                    code="task_blocker_command_required",
                    message=(
                        "direct task state edits into or out of blocked are disabled; "
                        "use team_report_blocker or team_update_blocker resolve"
                    ),
                    correction_context={
                        "task_id": self.task.id,
                        "current_state": self.task.state.value,
                        "requested_state": self.next_state.value,
                        "recommended_tools": [
                            "team_report_blocker",
                            "team_update_blocker",
                        ],
                    },
                )
            if self.next_state == TeamTaskState.FINISHED:
                if self.has_completion:
                    raise TeamCommandValidationError(
                        code="task_completion_requires_accepted_deliverable",
                        message=(
                            "structured outcome completion is derived from an "
                            "accepted deliverable; team_update_task cannot set it"
                        ),
                        correction_context={
                            "task_id": self.task.id,
                            "recommended_tool": "team_submit_deliverable",
                        },
                    )
        if self.has_completion and self.next_state != TeamTaskState.FINISHED:
            raise TeamCommandValidationError(
                code="task_completion_requires_accepted_deliverable",
                message=(
                    "structured outcome completion is derived from an accepted "
                    "deliverable; team_update_task cannot set it"
                ),
                correction_context={
                    "task_id": self.task.id,
                    "recommended_tool": "team_submit_deliverable",
                },
            )

    def _validate_reassignment(self) -> None:
        if self.has_owner_reassignment and isinstance(self.new_owner_id, str):
            reassignment_error = _task_owner_reassignment_error(
                self.state,
                actor=self.agent,
                task=self.task,
                new_owner_id=self.new_owner_id,
            )
            if reassignment_error is not None:
                raise TeamCommandValidationError(
                    code="task_reassignment_not_permitted",
                    message=reassignment_error,
                    correction_context={
                        "task_id": self.task.id,
                        "caller_agent_id": self.agent.id,
                        "owner_agent_id": self.task.owner_agent_id,
                        "new_owner_agent_id": self.new_owner_id,
                    },
                )

    def _validate_git_base_access(self) -> None:
        if self.has_git_base_edit:
            git_base_permitted = (
                self.is_owner
                or self.queue_manager
                or _supervises(self.state, self.agent.id, self.task.owner_agent_id)
                or _is_project_lead_for(self.state, self.agent.id, self.task.project_id)
                or self.is_same_team_manager
            )
            if not git_base_permitted:
                raise TeamCommandValidationError(
                    code="task_git_base_not_permitted",
                    message=(
                        f"agent {self.agent.id} is not permitted to edit the Git base for task {self.task.id}"
                    ),
                    correction_context={"task_id": self.task.id},
                )

    def _validate_graph(self) -> None:
        self.new_dependency_ids: list[str] | None = None
        if self.has_dependency_edit or self.has_deliverable_policy_edit:
            graph_permitted = (
                self.is_owner
                or self.queue_manager
                or _supervises(self.state, self.agent.id, self.task.owner_agent_id)
                or _is_project_lead_for(self.state, self.agent.id, self.task.project_id)
                or self.is_same_team_manager
            )
            if not graph_permitted:
                raise TeamCommandValidationError(
                    code="task_graph_not_permitted",
                    message=(
                        f"agent {self.agent.id} is not permitted to edit the task graph "
                        f"for task {self.task.id}"
                    ),
                    correction_context={
                        "task_id": self.task.id,
                        "caller_agent_id": self.agent.id,
                        "owner_agent_id": self.task.owner_agent_id,
                    },
                )
            self.new_dependency_ids = (
                list(self.updates["dependency_task_ids"])
                if self.has_dependency_edit
                else list(self.task.dependency_task_ids)
            )
            graph_error = _task_graph_edit_error(
                self.state,
                task_id=self.task.id,
                project_id=self.task.project_id,
                parent_task_id=self.task.parent_task_id,
                dependency_task_ids=self.new_dependency_ids,
            )
            if graph_error is not None:
                raise TeamCommandValidationError(
                    code="task_graph_invalid",
                    message=graph_error,
                    correction_context=_task_graph_correction_context(
                        self.state,
                        project_id=self.task.project_id,
                        task_id=self.task.id,
                        parent_task_id=self.task.parent_task_id,
                        dependency_task_ids=self.new_dependency_ids,
                    ),
                )
            if self.has_deliverable_policy_edit:
                if self.task.state not in _NON_TERMINAL_TASK_STATES:
                    raise TeamCommandValidationError(
                        code="task_deliverable_contract_terminal",
                        message=(
                            f"terminal task {self.task.id} cannot change its "
                            "integration deliverable contract"
                        ),
                        correction_context={"task_id": self.task.id},
                    )
                if self.task.deliverable_ids:
                    raise TeamCommandValidationError(
                        code="task_deliverable_contract_after_submission",
                        message=(
                            f"task {self.task.id} cannot change its integration "
                            "deliverable contract after a deliverable was submitted"
                        ),
                        correction_context={
                            "task_id": self.task.id,
                            "deliverable_ids": list(self.task.deliverable_ids),
                        },
                    )
                contract_error = _task_deliverable_contract_scope_error(
                    self.state,
                    task_id=self.task.id,
                    project_id=self.task.project_id,
                    dependency_task_ids=self.new_dependency_ids,
                    integration_boundary=self.next_integration_boundary,
                    deliverable_contract=self.next_deliverable_contract,
                )
                if contract_error is not None:
                    raise TeamCommandValidationError(
                        code="task_deliverable_contract_invalid",
                        message=contract_error,
                        correction_context={
                            "task_id": self.task.id,
                            "integration_boundary": (self.next_integration_boundary),
                            "deliverable_contract": (
                                self.next_deliverable_contract.model_dump(mode="json")
                                if self.next_deliverable_contract is not None
                                else None
                            ),
                        },
                    )

    def _validate_completion(self) -> None:
        if self.next_state == TeamTaskState.FINISHED:
            try:
                require_task_can_finish(
                    self.state,
                    self.task.id,
                    dependency_task_ids=self.new_dependency_ids,
                )
            except TaskAssessmentBoundaryError as exc:
                raise TeamCommandValidationError(
                    code=exc.code,
                    message=exc.message,
                    correction_context=exc.correction_context,
                ) from None

    def _apply_edits(self) -> None:
        self.changed = False
        self.now = utc_now()
        if self.next_state is not None:
            self.changed = _set_task_state(self.task, self.next_state, now=self.now) or self.changed
            if self.task.state not in _NON_TERMINAL_TASK_STATES:
                finalize_active_git_work_contexts_for_task(
                    self.state,
                    task_id=self.task.id,
                    now=self.now,
                )
        if self.has_priority:
            next_priority = int(self.updates["priority"])
            if self.task.priority != next_priority:
                self.task.priority = next_priority
                self.changed = True
        if self.has_title and isinstance(self.updates["title"], str):
            if self.task.title != self.updates["title"]:
                self.task.title = self.updates["title"]
                self.changed = True
        if self.has_description and isinstance(self.updates["description"], str):
            if self.task.description != self.updates["description"]:
                self.task.description = self.updates["description"]
                self.changed = True
        if self.has_deliverable_policy_edit:
            if self.task.integration_boundary != self.next_integration_boundary:
                self.task.integration_boundary = self.next_integration_boundary
                self.changed = True
            if self.task.deliverable_contract != self.next_deliverable_contract:
                self.task.deliverable_contract = self.next_deliverable_contract
                self.changed = True
        if self.has_authored_by:
            identity = task_author_identity(self.updates["authored_by"])
            if self.task.authored_by != identity:
                self.task.authored_by = identity
                self.changed = True
        if self.has_git_base_edit:
            try:
                self.changed = (
                    _set_task_git_base_branch(
                        self.state,
                        task=self.task,
                        git_base_branch=self.updates.get("git_base_branch"),
                        git_default_branch_resolver=self.executor.git_default_branch_resolver,
                    )
                    or self.changed
                )
            except TaskGitBaseValidationError as exc:
                raise TeamCommandValidationError(
                    code=exc.code,
                    message=exc.message,
                    correction_context={"task_id": self.task.id},
                ) from None
        if self.has_owner_reassignment and isinstance(self.new_owner_id, str):
            _apply_task_owner_reassignment(
                self.state,
                task=self.task,
                new_owner_id=self.new_owner_id,
                actor_agent_id=self.agent.id,
                dispatch_id=self.resolved.dispatch_id,
                reconciliation_result=self.reconciliation_result,
                now=self.now,
            )
            self.changed = True
        if (
            self.new_dependency_ids is not None
            and self.task.dependency_task_ids != self.new_dependency_ids
        ):
            self.task.dependency_task_ids = self.new_dependency_ids
            self.changed = True

    def _reconcile_edits(self) -> None:
        if self.changed:
            invalidate_task_preparation_after_edit(
                self.state,
                task_id=self.task.id,
                preparation_generation_before=self.preparation_generation_before,
                changed_fields=sorted(self.updates),
                sender_agent_id=self.agent.id,
                result=self.reconciliation_result,
                now=self.now,
            )
            self.task.updated_at = self.now
            if task_state_can_wake_owner(self.task.state):
                _mark_agent_work_due(
                    self.state,
                    agent_id=self.task.owner_agent_id,
                    now=self.now,
                )
            else:
                _maybe_mark_idle_context_rotation_due(
                    self.state,
                    agent_id=self.task.owner_agent_id,
                    now=self.now,
                    result=self.reconciliation_result,
                )
            if self.next_state is not None or self.has_owner_reassignment:
                _recalculate_project_status(self.state, self.task.project_id)
            if self.task.state in {
                TeamTaskState.ACCEPTED,
                TeamTaskState.CANCELLED,
                TeamTaskState.FINISHED,
            }:
                queue_parent_resolution_transitions(
                    self.state,
                    child_task_id=self.task.id,
                    sender_agent_id=self.agent.id,
                    now=self.now,
                )
        if (
            self.changed
            and self.task.assignment_mode == "role"
            and self.task.owner_agent_id is None
        ):
            from ..domain.role_task_queue import append_role_queue_wake_events

            append_role_queue_wake_events(
                self.state,
                result=self.reconciliation_result,
                now=self.now,
                project_id=self.task.project_id,
            )

    def _build_result(self) -> TeamCommandApplyResult:
        response_payload = {
            "task_id": self.task.id,
            "updated_task_ids": [self.task.id] if self.changed else [],
            "progress_made": self.changed,
            "created_blocker_ids": [],
            "created_inbox_item_ids": list(self.reconciliation_result["created_inbox_item_ids"]),
            "events": list(self.reconciliation_result["events"]),
            "resolved_blocker_ids": list(self.reconciliation_result["resolved_blocker_ids"]),
            "blocker_event_ids": list(self.reconciliation_result["blocker_event_ids"]),
            "resolved_blocker_event_ids": [],
            "automatically_handled_inbox_item_ids": [],
            "invalidated_git_work_context_ids": list(
                self.reconciliation_result.get("invalidated_git_work_context_ids") or []
            ),
            "created_preparation_invalidation_inbox_item_ids": list(
                self.reconciliation_result.get("created_preparation_invalidation_inbox_item_ids")
                or []
            ),
        }
        if "preparation_generation_before" in self.reconciliation_result:
            response_payload["preparation_generation_before"] = self.reconciliation_result[
                "preparation_generation_before"
            ]
            response_payload["preparation_generation_after"] = self.reconciliation_result[
                "preparation_generation_after"
            ]
        if self.changed and task_state_can_wake_owner(self.task.state):
            _append_agent_wake_requested_event(
                self.state,
                agent_id=self.task.owner_agent_id,
                result=response_payload,
                reason="task_ready",
                now=self.now,
                delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
            )
        return TeamCommandApplyResult(
            response_payload=response_payload,
            created_domain_ids=(
                [
                    self.task.id,
                    *response_payload["blocker_event_ids"],
                    *response_payload["created_inbox_item_ids"],
                ]
                if self.changed
                else []
            ),
        )


__all__ = ["execute_task_update_command"]
