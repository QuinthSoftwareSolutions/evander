from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ..models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentRuntime,
    PersistentAgent,
    Deliverable,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamDispatchStatus,
    TeamTaskState,
)
from .candidate_correction_reconciliation_service import (
    reconcile_candidate_correction_successors,
)
from .domain.blockers import (
    open_or_get_blocker,
    reconcile_task_blockers_after_reassignment,
)
from .domain.action_feedback import (
    _append_action_item_error,
    _git_release_sequence_error,
    _mark_action_failure,
    _priority_from_item,
    _response_requested_from_item,
)
from .domain.action_feedback_payloads import _non_empty_action_fields
from .domain.agent_access import (
    _is_direct_subordinate,
    _is_project_lead_for,
    _is_team_manager,
    _supervises,
)
from .domain.case_task import (
    _TaskCreationPlan,
    _apply_case_update,
    _apply_goal_update,
    _apply_task_creation_plan,
    _plan_task_creation,
    _resolve_task_creation_plan_git_bases,
    _restore_case_task_state,
    _snapshot_case_task_state,
    _subordinate_task_finish_allowed,
    _task_creation_batch_graph_errors,
    _task_graph_edit_error,
    _task_owner_reassignment_error,
    _upward_recipient_error,
)
from .domain.chat import (
    _append_pending_chat_message_queued_event_for_agent,
    _apply_chat_reply,
    _apply_operator_chat_message,
    _mark_chat_dispatch_message_failed,
)
from .domain.common import (
    DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
    _NON_TERMINAL_TASK_STATES,
    _append_unique,
    _bool_from_value,
    _generated_id,
    _list_of_mappings,
    _optional_text,
    _require_present,
    _required_text,
    _set_task_state,
    _string_list,
    task_state_can_wake_owner,
    utc_now,
)
from .domain.context_rotation import _maybe_mark_idle_context_rotation_due
from .domain.deliverables import (
    _deliverable_git_refs_from_payload,
    _deliverable_verification_from_payload,
    _deliverable_verification_payload,
    _superseded_deliverable_for_rework,
)
from .domain.ephemeral import (
    _apply_ephemeral_acceptance,
    _create_ephemeral_assignments,
)
from .ephemeral_directory import resolve_ephemeral_parent_directory_path
from .domain.git_promotion import (
    _apply_git_feature_update,
    _apply_git_review_promotion,
)
from .domain.git_work_contexts import (
    bind_deliverable_git_refs_to_work_context,
    finalize_active_git_work_contexts_for_dispatch,
    finalize_active_git_work_contexts_for_task,
    mark_git_work_context_submitted,
)
from .domain.operator_escalation import _apply_operator_escalation
from .domain.project_status import _recalculate_project_status
from .domain.release_packages import (
    _apply_git_release_recovery,
    _apply_git_release_request,
)
from .domain.review import (
    _apply_review_decision,
    _apply_review_request,
    _review_request_was_created,
)
from .domain.stand_down import (
    _active_stand_downs_for_agent,
    _apply_superior_stand_down,
    _apply_superior_stand_down_revocation,
    _stand_down_error_detail,
)
from .domain.task_assessment import (
    require_deliverable_submission,
    require_task_can_finish,
    require_task_deliverable_contract_provenance,
)
from .domain.task_supervision import (
    parent_task_for_child,
    queue_parent_resolution_transitions,
    queue_parent_task_transition,
    task_acceptance_recipient_agent_id,
)
from .domain.wake import (
    _append_agent_wake_requested_event,
    _append_agent_wake_requested_event_for_inbox_item,
    _append_follow_up_agent_wake_requested_event,
)
from .domain.work_due import (
    _agent_has_open_execution_stall,
    _mark_agent_work_due,
)
from .domain.work_lineage import ensure_task_work_lineages


@dataclass
class AgentActionBatch:
    """Apply one action in one repository transaction, with invocation-local state."""

    state: TeamState
    agent: PersistentAgent
    runtime: AgentRuntime
    action: dict[str, Any]
    dispatch_id: str | None
    action_id: str
    result: dict[str, Any]
    git_default_branch_resolver: Callable[..., object] | None
    git_base_commit_resolver: Callable[..., object] | None
    session_lookup: Any | None
    progress: bool = field(default=False, init=False)
    context_rotation_activity: bool = field(default=False, init=False)
    projects_to_recalc: set[str] = field(default_factory=set, init=False)
    effective_dispatch_id: str | None = field(default=None, init=False)
    enforce_manager_fallback_review_routing: bool = field(default=False, init=False)

    def apply(self, *, valid: bool, error: str | None) -> TeamState:
        if not self._validate(valid=valid, error=error):
            return self.state
        self.enforce_manager_fallback_review_routing = (
            self.runtime.wake_reason == "manager_fallback"
            and _is_team_manager(self.state, self.agent)
        )

        # Preserve stage order: later stages can refer to work created earlier
        # in this action. All stages and lease completion share the transaction.
        self._apply_stand_downs()
        self._apply_case_updates()
        self._apply_task_updates()
        self._apply_task_creations()
        self._apply_questions()
        self._apply_status_reports()
        self._apply_status_requests()
        self._apply_chat_replies()
        self._apply_operator_chat_messages()
        self._apply_operator_escalations()
        self._apply_deliverables()
        self._apply_review_requests()
        self._apply_review_decisions()
        self._apply_goal_updates()
        self._apply_git_review_promotions()
        self._apply_git_feature_updates()
        self._apply_git_release_requests()
        self._apply_git_release_recoveries()
        self._handle_inbox_items()
        self._apply_ephemeral_requests()
        self._apply_ephemeral_acceptances()
        return self._complete()

    def _validate(self, *, valid: bool, error: str | None) -> bool:
        if not valid:
            _mark_action_failure(
                self.state,
                runtime=self.runtime,
                agent_id=self.agent.id,
                dispatch_id=self.dispatch_id or self.runtime.active_dispatch_id or self.action_id,
                action=self.action,
                error=error or "invalid action",
                action_id=self.action_id,
                result=self.result,
            )
            return False
        if self.dispatch_id and self.runtime.active_dispatch_id:
            if self.dispatch_id != self.runtime.active_dispatch_id:
                _mark_action_failure(
                    self.state,
                    runtime=self.runtime,
                    agent_id=self.agent.id,
                    dispatch_id=self.dispatch_id,
                    action=self.action,
                    error="dispatch_id does not match active team runtime lease",
                    action_id=self.action_id,
                    result=self.result,
                )
                return False
        self.effective_dispatch_id = self.dispatch_id or self.runtime.active_dispatch_id
        active_stand_downs = _active_stand_downs_for_agent(self.state, self.agent.id)
        if active_stand_downs and _non_empty_action_fields(self.action):
            reason = _stand_down_error_detail(active_stand_downs)
            _mark_action_failure(
                self.state,
                runtime=self.runtime,
                agent_id=self.agent.id,
                dispatch_id=self.dispatch_id or self.runtime.active_dispatch_id or self.action_id,
                action=self.action,
                error=f"agent {self.agent.id} is stood down: {reason}",
                action_id=self.action_id,
                result=self.result,
            )
            return False

        git_sequence_error = _git_release_sequence_error(self.action)
        if git_sequence_error is not None:
            _mark_action_failure(
                self.state,
                runtime=self.runtime,
                agent_id=self.agent.id,
                dispatch_id=self.dispatch_id or self.runtime.active_dispatch_id or self.action_id,
                action=self.action,
                error=git_sequence_error,
                action_id=self.action_id,
                result=self.result,
            )
            return False

        return True

    def _mark_progress(self, *, context_activity: bool = True) -> None:
        self.progress = True
        if context_activity:
            self.context_rotation_activity = True

    def _apply_stand_downs(self) -> None:
        for index, stand_down_request in enumerate(
            _list_of_mappings(self.action.get("stand_downs"), "stand_downs")
        ):
            try:
                stand_down_id = _apply_superior_stand_down(
                    self.state,
                    actor=self.agent,
                    request=stand_down_request,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "stand_downs", index, exc)
                continue
            self.result["created_stand_down_ids"].append(stand_down_id)
            self._mark_progress()

        for index, revoke_request in enumerate(
            _list_of_mappings(
                self.action.get("stand_down_revocations"),
                "stand_down_revocations",
            )
        ):
            try:
                revoked_ids = _apply_superior_stand_down_revocation(
                    self.state,
                    actor=self.agent,
                    request=revoke_request,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "stand_down_revocations", index, exc)
                continue
            self.result["revoked_stand_down_ids"].extend(revoked_ids)
            self._mark_progress()

    def _apply_case_updates(self) -> None:
        for index, case_update in enumerate(
            _list_of_mappings(self.action.get("case_updates"), "case_updates")
        ):
            case_task_state = _snapshot_case_task_state(self.state)
            case_event_count = len(self.result["case_closed_events"])
            try:
                case_id, created = _apply_case_update(
                    self.state,
                    actor=self.agent,
                    update=case_update,
                    case_closed_events=self.result["case_closed_events"],
                )
            except ValueError as exc:
                _restore_case_task_state(self.state, case_task_state)
                _append_action_item_error(self.result, "case_updates", index, exc)
                continue
            if created:
                self.result["created_case_ids"].append(case_id)
            else:
                self.result["updated_case_ids"].append(case_id)
            self.result["events"].extend(self.result["case_closed_events"][case_event_count:])
            self._mark_progress()

    def _apply_task_updates(self) -> None:
        for index, update in enumerate(
            _list_of_mappings(self.action.get("task_updates"), "task_updates")
        ):
            try:
                task_id = _required_text(update.get("task_id"), "task_id")
                task = _require_present(self.state.tasks, task_id, "task")
                is_owner = task.owner_agent_id == self.agent.id
                raw_state = _optional_text(update.get("state"))
                priority = update.get("priority")
                title = _optional_text(update.get("title"))
                description = _optional_text(update.get("description"))
                new_owner_id = (
                    _required_text(update.get("owner_agent_id"), "owner_agent_id")
                    if "owner_agent_id" in update
                    else None
                )
                has_owner_only_edit = (
                    raw_state is not None
                    or priority is not None
                    or title is not None
                    or description is not None
                )
                has_owner_reassignment = (
                    new_owner_id is not None and new_owner_id != task.owner_agent_id
                )
                has_dependency_edit = "dependency_task_ids" in update
                subordinate_finish_allowed = (
                    raw_state is not None
                    and priority is None
                    and title is None
                    and description is None
                    and _subordinate_task_finish_allowed(
                        self.state,
                        actor=self.agent,
                        task=task,
                        raw_state=raw_state,
                        new_owner_id=(new_owner_id if has_owner_reassignment else None),
                    )
                )
                # State, priority, title, and description remain owner-only,
                # except that a superior may close claimable subordinate work.
                if has_owner_only_edit and not is_owner and not subordinate_finish_allowed:
                    self.result["errors"].append(
                        f"agent {self.agent.id} cannot update task {task_id}"
                    )
                    continue
                if has_owner_reassignment:
                    reassignment_error = _task_owner_reassignment_error(
                        self.state,
                        actor=self.agent,
                        task=task,
                        new_owner_id=new_owner_id,
                    )
                    if reassignment_error is not None:
                        self.result["errors"].append(reassignment_error)
                        continue
                new_dependency_task_ids: list[str] | None = None
                if has_dependency_edit:
                    project = self.state.projects.get(task.project_id)
                    graph_permitted = (
                        is_owner
                        or _supervises(self.state, self.agent.id, task.owner_agent_id)
                        or _is_project_lead_for(self.state, self.agent.id, task.project_id)
                        or (
                            project is not None
                            and self.agent.team_id == project.team_id
                            and _is_team_manager(self.state, self.agent)
                        )
                    )
                    if not graph_permitted:
                        self.result["errors"].append(
                            f"agent {self.agent.id} is not permitted to edit the task graph "
                            f"for task {task_id}"
                        )
                        continue
                    new_dependency_task_ids = _string_list(
                        update.get("dependency_task_ids"), "dependency_task_ids"
                    )
                    graph_error = _task_graph_edit_error(
                        self.state,
                        task_id=task.id,
                        project_id=task.project_id,
                        parent_task_id=task.parent_task_id,
                        dependency_task_ids=new_dependency_task_ids,
                    )
                    if graph_error is not None:
                        self.result["errors"].append(graph_error)
                        continue
                changed = False
                now = utc_now()
                previous_task_state = task.state
                if raw_state is not None:
                    if raw_state == TeamTaskState.WAITING_FOR_INPUT.value:
                        self.result["errors"].append(
                            "waiting_for_input is reserved for operator-owned workflows"
                        )
                        continue
                    if task.state == TeamTaskState.WAITING_FOR_INPUT:
                        self.result["errors"].append(
                            f"task {task.id} is waiting_for_input and can only be "
                            "resumed by an operator-owned workflow"
                        )
                        continue
                    if raw_state == TeamTaskState.FINISHED.value:
                        require_task_can_finish(
                            self.state,
                            task.id,
                            dependency_task_ids=new_dependency_task_ids,
                        )
                    _set_task_state(task, TeamTaskState(raw_state), now=now)
                    if task.state not in _NON_TERMINAL_TASK_STATES:
                        finalize_active_git_work_contexts_for_task(
                            self.state,
                            task_id=task.id,
                            now=now,
                        )
                    self.projects_to_recalc.add(task.project_id)
                    changed = True
                if priority is not None:
                    task.priority = int(priority)
                    changed = True
                if title is not None:
                    task.title = title
                    changed = True
                if description is not None:
                    task.description = description
                    changed = True
                if has_owner_reassignment and new_owner_id is not None:
                    old_owner_id = task.owner_agent_id
                    finalize_active_git_work_contexts_for_task(
                        self.state,
                        task_id=task.id,
                        now=now,
                    )
                    task.owner_agent_id = new_owner_id
                    reconcile_task_blockers_after_reassignment(
                        self.state,
                        task_id=task.id,
                        old_owner_agent_id=old_owner_id,
                        new_owner_agent_id=new_owner_id,
                        actor_agent_id=self.agent.id,
                        dispatch_id=self.effective_dispatch_id,
                        result=self.result,
                        now=now,
                    )
                    self.projects_to_recalc.add(task.project_id)
                    _maybe_mark_idle_context_rotation_due(
                        self.state,
                        agent_id=old_owner_id,
                        now=now,
                        result=self.result,
                    )
                    if task_state_can_wake_owner(task.state):
                        _mark_agent_work_due(
                            self.state,
                            agent_id=new_owner_id,
                            now=now,
                        )
                    changed = True
                if new_dependency_task_ids is not None:
                    task.dependency_task_ids = new_dependency_task_ids
                    changed = True
                if changed:
                    task.updated_at = now
                    if (
                        previous_task_state != TeamTaskState.BLOCKED
                        and task.state == TeamTaskState.BLOCKED
                    ):
                        open_or_get_blocker(
                            self.state,
                            source_kind=TeamBlockerSourceKind.TASK,
                            source_id=task.id,
                            summary=f"Task {task.id} is blocked",
                            details=task.description,
                            reporter_agent_id=self.agent.id,
                            team_id=self.agent.team_id,
                            project_id=task.project_id,
                            task_id=task.id,
                            result=self.result,
                            now=now,
                        )
                    if previous_task_state != task.state and task.state in {
                        TeamTaskState.ACCEPTED,
                        TeamTaskState.CANCELLED,
                        TeamTaskState.FINISHED,
                    }:
                        queue_parent_resolution_transitions(
                            self.state,
                            child_task_id=task.id,
                            sender_agent_id=self.agent.id,
                            result=self.result,
                            now=now,
                        )
                    if task_state_can_wake_owner(task.state):
                        _append_agent_wake_requested_event(
                            self.state,
                            agent_id=task.owner_agent_id,
                            result=self.result,
                            reason="task_ready",
                            now=now,
                            delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                        )
                    self._mark_progress()
                    self.result["updated_task_ids"].append(task.id)
            except ValueError as exc:
                _append_action_item_error(self.result, "task_updates", index, exc)

    def _apply_task_creations(self) -> None:
        task_creation_plans: list[_TaskCreationPlan] = []
        reserved_task_ids: set[str] = set()
        for index, creation in enumerate(
            _list_of_mappings(self.action.get("task_creations"), "task_creations")
        ):
            try:
                plan = _plan_task_creation(
                    self.state,
                    actor=self.agent,
                    creation=creation,
                    result=self.result,
                    reserved_task_ids=reserved_task_ids,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "task_creations", index, exc)
                continue
            if plan is None:
                continue
            reserved_task_ids.add(plan.task_id)
            task_creation_plans.append(plan)

        # Prune invalid graphs (including their dependents) before resolving lineage.
        # Agent actions preserve independent valid creations rather than failing the batch.
        graph_errors = _task_creation_batch_graph_errors(
            self.state,
            task_creation_plans,
            actor=self.agent,
        )
        for plan in task_creation_plans:
            if plan.task_id in graph_errors:
                self.result["errors"].append(graph_errors[plan.task_id])
        task_creation_plans = [
            plan for plan in task_creation_plans if plan.task_id not in graph_errors
        ]

        try:
            task_creation_plans = _resolve_task_creation_plan_git_bases(
                self.state,
                task_creation_plans,
                git_default_branch_resolver=(self.git_default_branch_resolver),
                git_base_commit_resolver=(self.git_base_commit_resolver),
            )
        except ValueError as exc:
            self.result["errors"].append(f"task_creations: {exc}")
            task_creation_plans = []

        created_task_ids: list[str] = []
        for plan in task_creation_plans:
            created_id = _apply_task_creation_plan(
                self.state,
                plan=plan,
                actor=self.agent,
                assign_work_lineage=False,
            )
            created_task_ids.append(created_id)
            self.result["created_task_ids"].append(created_id)
            self.projects_to_recalc.add(self.state.tasks[created_id].project_id)
            _append_agent_wake_requested_event(
                self.state,
                agent_id=plan.owner_id,
                result=self.result,
                reason="task_ready",
                now=utc_now(),
                delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
            )
            self._mark_progress()
        ensure_task_work_lineages(
            self.state,
            (self.state.tasks[task_id] for task_id in created_task_ids),
        )

    def _apply_questions(self) -> None:
        for index, question in enumerate(
            _list_of_mappings(self.action.get("questions"), "questions")
        ):
            try:
                recipient_id = (
                    _optional_text(question.get("recipient_agent_id"))
                    or self.agent.reports_to_agent_id
                )
                upward_error = _upward_recipient_error(self.agent, recipient_id, "questions")
                if upward_error is not None:
                    self.result["errors"].append(upward_error)
                    continue
                response_requested = _response_requested_from_item(
                    question,
                    default=True,
                )
                priority = _priority_from_item(
                    question,
                    default=TeamInboxPriority.NORMAL,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "questions", index, exc)
                continue
            inbox_id = f"inbox-{uuid4().hex}"
            item = AgentInboxItem(
                id=inbox_id,
                recipient_agent_id=recipient_id,
                sender_agent_id=self.agent.id,
                task_id=_optional_text(question.get("task_id")),
                item_type=TeamInboxItemType.QUESTION,
                response_requested=response_requested,
                priority=priority,
                payload={
                    key: value
                    for key, value in question.items()
                    if key
                    not in {
                        "recipient_agent_id",
                        "task_id",
                        "response_requested",
                        "priority",
                    }
                },
            )
            self.state.inbox[item.id] = item
            self.result["created_inbox_item_ids"].append(item.id)
            _append_agent_wake_requested_event_for_inbox_item(
                self.state,
                item=item,
                result=self.result,
                now=utc_now(),
            )
            self._mark_progress()

    def _apply_status_reports(self) -> None:
        for index, report in enumerate(
            _list_of_mappings(
                self.action.get("status_reports"),
                "status_reports",
            )
        ):
            try:
                recipient_id = (
                    _optional_text(report.get("recipient_agent_id"))
                    or self.agent.reports_to_agent_id
                )
                upward_error = _upward_recipient_error(self.agent, recipient_id, "status_reports")
                if upward_error is not None:
                    self.result["errors"].append(upward_error)
                    continue
                response_requested = _response_requested_from_item(
                    report,
                    default=False,
                )
                priority = _priority_from_item(
                    report,
                    default=TeamInboxPriority.LOW,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "status_reports", index, exc)
                continue
            inbox_id = f"inbox-{uuid4().hex}"
            item = AgentInboxItem(
                id=inbox_id,
                recipient_agent_id=recipient_id,
                sender_agent_id=self.agent.id,
                task_id=_optional_text(report.get("task_id")),
                item_type=TeamInboxItemType.PROGRESS_REPORT,
                response_requested=response_requested,
                priority=priority,
                payload={
                    key: value
                    for key, value in report.items()
                    if key
                    not in {
                        "recipient_agent_id",
                        "task_id",
                        "response_requested",
                        "priority",
                    }
                },
            )
            self.state.inbox[item.id] = item
            self.result["created_inbox_item_ids"].append(item.id)
            _append_agent_wake_requested_event_for_inbox_item(
                self.state,
                item=item,
                result=self.result,
                now=utc_now(),
            )
            self._mark_progress(context_activity=False)

    def _apply_status_requests(self) -> None:
        for index, status_request in enumerate(
            _list_of_mappings(self.action.get("status_requests"), "status_requests")
        ):
            try:
                recipient_id = _required_text(
                    status_request.get("recipient_agent_id"), "recipient_agent_id"
                )
                if not _is_direct_subordinate(self.agent, recipient_id):
                    self.result["errors"].append(
                        f"agent {self.agent.id} can only request status from a direct subordinate"
                    )
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=self.agent.id,
                    task_id=_optional_text(status_request.get("task_id")),
                    item_type=TeamInboxItemType.STATUS_REQUEST,
                    response_requested=_response_requested_from_item(
                        status_request,
                        default=True,
                    ),
                    priority=_priority_from_item(
                        status_request,
                        default=TeamInboxPriority.NORMAL,
                    ),
                    payload={
                        key: value
                        for key, value in status_request.items()
                        if key
                        not in {
                            "recipient_agent_id",
                            "task_id",
                            "response_requested",
                            "priority",
                        }
                    },
                )
                self.state.inbox[item.id] = item
                self.result["created_status_request_ids"].append(item.id)
                _append_agent_wake_requested_event_for_inbox_item(
                    self.state,
                    item=item,
                    result=self.result,
                    now=utc_now(),
                )
                self._mark_progress()
            except ValueError as exc:
                _append_action_item_error(self.result, "status_requests", index, exc)

    def _apply_chat_replies(self) -> None:
        for index, reply in enumerate(
            _list_of_mappings(self.action.get("chat_replies"), "chat_replies")
        ):
            try:
                created_id = _apply_chat_reply(
                    self.state,
                    actor=self.agent,
                    reply=reply,
                    result=self.result,
                )
            except ValueError as exc:
                error_detail = f"chat_replies[{index}]: {exc}"
                _append_action_item_error(self.result, "chat_replies", index, exc)
                _mark_chat_dispatch_message_failed(
                    self.state,
                    dispatch_id=self.dispatch_id or self.runtime.active_dispatch_id,
                    error=error_detail,
                )
                continue
            self.result["created_chat_reply_message_ids"].append(created_id)
            self._mark_progress()

    def _apply_operator_chat_messages(self) -> None:
        for index, chat_message in enumerate(
            _list_of_mappings(
                self.action.get("operator_chat_messages"),
                "operator_chat_messages",
            )
        ):
            try:
                created_id = _apply_operator_chat_message(
                    self.state,
                    actor=self.agent,
                    message=chat_message,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "operator_chat_messages", index, exc)
                continue
            self.result["created_operator_chat_message_ids"].append(created_id)
            self._mark_progress()

    def _apply_operator_escalations(self) -> None:
        for index, escalation in enumerate(
            _list_of_mappings(
                self.action.get("operator_escalations"),
                "operator_escalations",
            )
        ):
            try:
                _apply_operator_escalation(
                    self.state,
                    actor=self.agent,
                    escalation=escalation,
                    result=self.result,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "operator_escalations", index, exc)

    def _apply_deliverables(self) -> None:
        for index, deliverable_payload in enumerate(
            _list_of_mappings(self.action.get("deliverables"), "deliverables")
        ):
            try:
                task_id = _required_text(deliverable_payload.get("task_id"), "task_id")
                task = _require_present(self.state.tasks, task_id, "task")
                if task.owner_agent_id != self.agent.id:
                    self.result["errors"].append(
                        f"agent {self.agent.id} cannot deliver task {task_id}"
                    )
                    continue
                deliverable_id = _optional_text(deliverable_payload.get("id"))
                if deliverable_id is None:
                    deliverable_id = _generated_id(
                        prefix="deliverable",
                        labels=[task_id, deliverable_payload.get("summary")],
                        existing_ids=self.state.deliverables,
                    )
                artifact_refs = _string_list(
                    deliverable_payload.get("artifact_refs"), "artifact_refs"
                )
                artifact_space_ref = _optional_text(deliverable_payload.get("artifact_space_ref"))
                if artifact_space_ref:
                    artifact_refs = _append_unique(artifact_refs, artifact_space_ref)
                source_deliverable_ids = _string_list(
                    deliverable_payload.get("source_deliverable_ids"),
                    "source_deliverable_ids",
                )
                require_deliverable_submission(
                    self.state,
                    task_id=task.id,
                    source_deliverable_ids=source_deliverable_ids,
                )
                bind_deliverable_git_refs_to_work_context(
                    self.state,
                    agent_id=self.agent.id,
                    task_id=task.id,
                    deliverable=deliverable_payload,
                    dispatch_id=self.effective_dispatch_id,
                )
                git_refs = _deliverable_git_refs_from_payload(deliverable_payload.get("git_refs"))
                require_task_deliverable_contract_provenance(
                    self.state,
                    task_id=task.id,
                    git_refs=git_refs,
                    producer_agent_id=self.agent.id,
                    deliverable_id=deliverable_id,
                    require_submitted_context=False,
                )
                verification = _deliverable_verification_from_payload(
                    deliverable_payload.get("verification")
                )
                supersedes_deliverable_id = _optional_text(
                    deliverable_payload.get("supersedes_deliverable_id")
                )
                if (
                    supersedes_deliverable_id is not None
                    and deliverable_id == supersedes_deliverable_id
                ):
                    raise ValueError("deliverable id cannot match supersedes_deliverable_id")
                superseded_deliverable = _superseded_deliverable_for_rework(
                    self.state,
                    task=task,
                    producer_agent_id=self.agent.id,
                    supersedes_deliverable_id=supersedes_deliverable_id,
                )
                if (
                    superseded_deliverable is not None
                    and superseded_deliverable.review_cohort_id is not None
                    and superseded_deliverable.review_cohort_sealed_at is not None
                ):
                    raise ValueError(
                        f"replacement for sealed-cohort deliverable "
                        f"{superseded_deliverable.id} must use "
                        "team_submit_deliverable_for_review to atomically "
                        "create a complete fresh review cohort"
                    )
                acceptance_recipient_agent_id = task_acceptance_recipient_agent_id(
                    self.state,
                    task=task,
                    producer_agent_id=self.agent.id,
                )
                deliverable = Deliverable(
                    id=deliverable_id,
                    task_id=task_id,
                    producer_agent_id=self.agent.id,
                    summary=_required_text(deliverable_payload.get("summary"), "summary"),
                    artifact_refs=artifact_refs,
                    git_refs=git_refs,
                    verification=verification,
                    source_deliverable_ids=source_deliverable_ids,
                    state=TeamRecordState.PENDING,
                    acceptance_recipient_agent_id=acceptance_recipient_agent_id,
                    supersedes_deliverable_id=supersedes_deliverable_id,
                )
                self.state.deliverables[deliverable.id] = deliverable
                if superseded_deliverable is not None:
                    superseded_deliverable.superseded_by_deliverable_id = deliverable.id
                    superseded_deliverable.supersession_reason = "Rework deliverable submitted."
                    superseded_deliverable.superseded_at = deliverable.created_at
                    superseded_deliverable.superseded_by_actor = f"agent:{self.agent.id}"
                task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
                now = utc_now()
                mark_git_work_context_submitted(
                    self.state,
                    branch_context_id=deliverable.git_refs.branch_context_id,
                    now=now,
                )
                _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
                task.updated_at = now
                self.projects_to_recalc.add(task.project_id)
                self.result["created_deliverable_ids"].append(deliverable.id)
                parent_task = parent_task_for_child(self.state, task)
                if acceptance_recipient_agent_id is not None:
                    inbox_id = f"inbox-{uuid4().hex}"
                    payload = {
                        "kind": "superior_acceptance_requested",
                        "deliverable_id": deliverable.id,
                        "task_id": task.id,
                        "producer_agent_id": self.agent.id,
                        "acceptance_recipient_agent_id": (acceptance_recipient_agent_id),
                        "summary": deliverable.summary,
                        "artifact_refs": list(deliverable.artifact_refs),
                    }
                    if parent_task is not None:
                        payload.update(
                            {
                                "transition": "handoff",
                                "parent_task_id": parent_task.id,
                                "child_task_id": task.id,
                            }
                        )
                    verification_payload = _deliverable_verification_payload(deliverable)
                    if verification_payload is not None:
                        payload["verification"] = verification_payload
                    self.state.inbox[inbox_id] = AgentInboxItem(
                        id=inbox_id,
                        recipient_agent_id=acceptance_recipient_agent_id,
                        sender_agent_id=self.agent.id,
                        task_id=task_id,
                        item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
                        response_requested=True,
                        priority=TeamInboxPriority.LOW,
                        payload=payload,
                    )
                    self.result["created_inbox_item_ids"].append(inbox_id)
                    _mark_agent_work_due(
                        self.state,
                        agent_id=acceptance_recipient_agent_id,
                        now=now,
                    )
                if (
                    parent_task is not None
                    and acceptance_recipient_agent_id != parent_task.owner_agent_id
                ):
                    queue_parent_task_transition(
                        self.state,
                        child_task_id=task.id,
                        transition="handoff",
                        sender_agent_id=self.agent.id,
                        result=self.result,
                        now=now,
                        references={"deliverable_id": deliverable.id},
                    )
                self._mark_progress()
            except ValueError as exc:
                _append_action_item_error(self.result, "deliverables", index, exc)

    def _apply_review_requests(self) -> None:
        for index, review_request in enumerate(
            _list_of_mappings(self.action.get("review_requests"), "review_requests")
        ):
            try:
                created_id = _apply_review_request(
                    self.state,
                    actor=self.agent,
                    request=review_request,
                    result=self.result,
                    projects_to_recalc=self.projects_to_recalc,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "review_requests", index, exc)
                continue
            if created_id is not None:
                if _review_request_was_created(self.result, created_id):
                    self.result["created_review_request_ids"].append(created_id)
                    self._mark_progress()

    def _apply_review_decisions(self) -> None:
        for index, decision in enumerate(
            _list_of_mappings(self.action.get("review_decisions"), "review_decisions")
        ):
            try:
                decided_id = _apply_review_decision(
                    self.state,
                    actor=self.agent,
                    decision=decision,
                    result=self.result,
                    projects_to_recalc=self.projects_to_recalc,
                    enforce_manager_fallback_routing_guard=(
                        self.enforce_manager_fallback_review_routing
                    ),
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "review_decisions", index, exc)
                continue
            if decided_id is not None:
                self.result["decided_review_request_ids"].append(decided_id)
                self._mark_progress()

    def _apply_goal_updates(self) -> None:
        for index, goal_update in enumerate(
            _list_of_mappings(self.action.get("goal_updates"), "goal_updates")
        ):
            try:
                updated_id = _apply_goal_update(
                    self.state,
                    actor=self.agent,
                    update=goal_update,
                    result=self.result,
                    projects_to_recalc=self.projects_to_recalc,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "goal_updates", index, exc)
                continue
            if updated_id is not None:
                self.result["updated_goal_ids"].append(updated_id)
                self._mark_progress()

    def _apply_git_review_promotions(self) -> None:
        for index, promotion in enumerate(
            _list_of_mappings(self.action.get("git_review_promotions"), "git_review_promotions")
        ):
            try:
                promotion_id = _apply_git_review_promotion(
                    self.state,
                    actor=self.agent,
                    request=promotion,
                    result=self.result,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "git_review_promotions", index, exc)
                continue
            if promotion_id is not None:
                self.result["git_promotion_event_ids"].append(promotion_id)
                self._mark_progress()

    def _apply_git_feature_updates(self) -> None:
        for index, feature_update in enumerate(
            _list_of_mappings(self.action.get("git_feature_updates"), "git_feature_updates")
        ):
            try:
                promotion_id = _apply_git_feature_update(
                    self.state,
                    actor=self.agent,
                    update=feature_update,
                    result=self.result,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "git_feature_updates", index, exc)
                continue
            if promotion_id is not None:
                self.result["git_promotion_event_ids"].append(promotion_id)
                self._mark_progress()

    def _apply_git_release_requests(self) -> None:
        for index, release_request in enumerate(
            _list_of_mappings(self.action.get("git_release_requests"), "git_release_requests")
        ):
            try:
                release_id = _apply_git_release_request(
                    self.state,
                    actor=self.agent,
                    request=release_request,
                    result=self.result,
                    projects_to_recalc=self.projects_to_recalc,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "git_release_requests", index, exc)
                continue
            if release_id is not None:
                self.result["release_package_ids"].append(release_id)
                self._mark_progress()

    def _apply_git_release_recoveries(self) -> None:
        for index, recovery_request in enumerate(
            _list_of_mappings(
                self.action.get("git_release_recoveries"),
                "git_release_recoveries",
            )
        ):
            try:
                recovery_id = _apply_git_release_recovery(
                    self.state,
                    actor=self.agent,
                    request=recovery_request,
                    result=self.result,
                )
            except ValueError as exc:
                _append_action_item_error(self.result, "git_release_recoveries", index, exc)
                continue
            if recovery_id is not None:
                self.result["release_recovery_event_ids"].append(recovery_id)
                self._mark_progress()

    def _handle_inbox_items(self) -> None:
        for inbox_item_id in _string_list(
            self.action.get("handled_inbox_item_ids"),
            "handled_inbox_item_ids",
        ):
            item = self.state.inbox.get(inbox_item_id)
            if item is None or item.recipient_agent_id != self.agent.id:
                self.result["errors"].append(
                    f"agent {self.agent.id} cannot handle inbox item {inbox_item_id}"
                )
                continue
            item.state = TeamInboxItemState.HANDLED
            item.updated_at = utc_now()
            self.result["handled_inbox_item_ids"].append(inbox_item_id)
            self._mark_progress()

    def _apply_ephemeral_requests(self) -> None:
        ephemeral_requests = _list_of_mappings(
            self.action.get("ephemeral_requests"),
            "ephemeral_requests",
        )
        if ephemeral_requests:
            try:
                _create_ephemeral_assignments(
                    self.state,
                    parent=self.agent,
                    parent_directory_path=resolve_ephemeral_parent_directory_path(
                        self.session_lookup,
                        self.agent.session_id,
                    ),
                    requests=ephemeral_requests,
                    dispatch_id=self.dispatch_id or self.runtime.active_dispatch_id,
                    result=self.result,
                )
                self._mark_progress()
            except ValueError as exc:
                self.result["errors"].append(f"ephemeral_requests: {exc}")

    def _apply_ephemeral_acceptances(self) -> None:
        for index, acceptance in enumerate(
            _list_of_mappings(
                self.action.get("ephemeral_acceptances"),
                "ephemeral_acceptances",
            )
        ):
            try:
                assignment_id = _required_text(acceptance.get("assignment_id"), "assignment_id")
                assignment = _require_present(
                    self.state.ephemeral_assignments,
                    assignment_id,
                    "ephemeral assignment",
                )
                if assignment.parent_agent_id != self.agent.id:
                    self.result["errors"].append(
                        f"agent {self.agent.id} cannot accept ephemeral assignment {assignment_id}"
                    )
                    continue
                accepted = _bool_from_value(acceptance.get("accepted"), default=True)
                _apply_ephemeral_acceptance(
                    self.state,
                    assignment=assignment,
                    accepted=accepted,
                    deliverable_id=_optional_text(acceptance.get("deliverable_id")),
                    rejection_reason=_optional_text(acceptance.get("rejection_reason")),
                )
                if accepted:
                    self.result["accepted_ephemeral_assignment_ids"].append(assignment.id)
                else:
                    self.result["rejected_ephemeral_assignment_ids"].append(assignment.id)
                self._mark_progress()
            except ValueError as exc:
                _append_action_item_error(self.result, "ephemeral_acceptances", index, exc)

    def _complete(self) -> TeamState:
        if self.result["decided_review_request_ids"]:
            reconcile_candidate_correction_successors(
                self.state,
                result=self.result,
                now=utc_now(),
            )

        for project_id in sorted(self.projects_to_recalc):
            _recalculate_project_status(self.state, project_id)

        record = AgentActionRecord(
            id=self.action_id,
            agent_id=self.agent.id,
            dispatch_id=self.dispatch_id or self.runtime.active_dispatch_id or self.action_id,
            action=self.action,
            success=not self.result["errors"],
            error="; ".join(self.result["errors"]) if self.result["errors"] else None,
        )
        self.state.action_records[record.id] = record
        completed_at = utc_now()
        finalize_active_git_work_contexts_for_dispatch(
            self.state,
            agent_id=self.agent.id,
            dispatch_id=self.effective_dispatch_id or record.dispatch_id,
            now=completed_at,
        )
        durable_dispatch = self.state.dispatches.get(
            self.effective_dispatch_id or record.dispatch_id
        )
        if durable_dispatch is not None and durable_dispatch.status in {
            TeamDispatchStatus.PREPARING,
            TeamDispatchStatus.LEASED,
        }:
            durable_dispatch.status = TeamDispatchStatus.COMPLETED
            durable_dispatch.completed_at = completed_at
            durable_dispatch.error = None
        self.runtime.active_dispatch_id = None
        self.runtime.wake_leased_until = None
        self.runtime.wake_reason = None
        if self.progress:
            if not _agent_has_open_execution_stall(
                self.state,
                agent_id=self.agent.id,
            ):
                self.runtime.no_progress_count = 0
        else:
            self.runtime.no_progress_count += 1
        if self.context_rotation_activity:
            self.runtime.last_progress_at = completed_at
        self.runtime.updated_at = completed_at
        _maybe_mark_idle_context_rotation_due(
            self.state,
            agent_id=self.agent.id,
            now=self.runtime.updated_at,
            result=self.result,
        )
        _append_pending_chat_message_queued_event_for_agent(
            self.state,
            agent=self.agent,
            completed_dispatch_id=record.dispatch_id,
            result=self.result,
        )
        _append_follow_up_agent_wake_requested_event(
            self.state,
            agent_id=self.agent.id,
            result=self.result,
            now=self.runtime.updated_at,
            completed_dispatch_id=record.dispatch_id,
            progress_made=self.progress,
        )
        self.result["success"] = self.progress or not self.result["errors"]
        self.result["applied"] = self.progress
        return self.state
