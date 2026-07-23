from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..repository import TeamStateRepository
from ..models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentRuntime,
    Deliverable,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
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
    _resolve_action_agent,
    _supervises,
)
from .domain.case_task import (
    _TaskCreationPlan,
    _apply_case_update,
    _apply_goal_update,
    _apply_task_creation_plan,
    _plan_task_creation,
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
    _publish_admin_chat_notifications,
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
    _ephemeral_assignment_event_payload,
)
from .domain.git_promotion import (
    _apply_git_feature_update,
    _apply_git_review_promotion,
)
from .domain.git_work_contexts import bind_deliverable_git_refs_to_work_context
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
from .domain.wake import (
    _append_agent_wake_requested_event,
    _append_agent_wake_requested_event_for_inbox_item,
    _append_follow_up_agent_wake_requested_event,
)
from .domain.work_due import _mark_agent_work_due


class TeamAgentActionService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    def apply_agent_action(
        self,
        *,
        action: dict[str, Any] | None,
        session_id: str | None = None,
        dispatch_id: str | None = None,
        valid: bool = True,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_action = dict(action or {}) if isinstance(action, dict) else {}
        normalized_session_id = _optional_text(session_id)
        normalized_dispatch_id = _optional_text(dispatch_id) or _optional_text(
            normalized_action.get("dispatch_id")
        )
        result: dict[str, Any] = {
            "success": False,
            "applied": False,
            "agent_id": None,
            "errors": [],
            "created_inbox_item_ids": [],
            "created_deliverable_ids": [],
            "created_case_ids": [],
            "updated_case_ids": [],
            "created_task_ids": [],
            "created_status_request_ids": [],
            "created_review_request_ids": [],
            "existing_review_request_ids": [],
            "decided_review_request_ids": [],
            "updated_goal_ids": [],
            "operator_escalation_event_ids": [],
            "git_promotion_event_ids": [],
            "release_recovery_event_ids": [],
            "release_package_ids": [],
            "updated_task_ids": [],
            "handled_inbox_item_ids": [],
            "created_ephemeral_assignment_ids": [],
            "ephemeral_events": [],
            "events": [],
            "accepted_ephemeral_assignment_ids": [],
            "rejected_ephemeral_assignment_ids": [],
            "created_stand_down_ids": [],
            "revoked_stand_down_ids": [],
            "created_chat_reply_message_ids": [],
            "replied_chat_message_ids": [],
            "created_operator_chat_message_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            agent = _resolve_action_agent(
                state_model,
                action=normalized_action,
                session_id=normalized_session_id,
            )
            result["agent_id"] = agent.id
            runtime = state_model.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            action_id = f"action-{uuid4().hex}"
            if not valid:
                _mark_action_failure(
                    state_model,
                    runtime=runtime,
                    agent_id=agent.id,
                    dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                    action=normalized_action,
                    error=error or "invalid action",
                    action_id=action_id,
                    result=result,
                )
                return state_model
            if normalized_dispatch_id and runtime.active_dispatch_id:
                if normalized_dispatch_id != runtime.active_dispatch_id:
                    _mark_action_failure(
                        state_model,
                        runtime=runtime,
                        agent_id=agent.id,
                        dispatch_id=normalized_dispatch_id,
                        action=normalized_action,
                        error="dispatch_id does not match active team runtime lease",
                        action_id=action_id,
                        result=result,
                    )
                    return state_model
            effective_dispatch_id = normalized_dispatch_id or runtime.active_dispatch_id
            active_stand_downs = _active_stand_downs_for_agent(state_model, agent.id)
            if active_stand_downs and _non_empty_action_fields(normalized_action):
                reason = _stand_down_error_detail(active_stand_downs)
                _mark_action_failure(
                    state_model,
                    runtime=runtime,
                    agent_id=agent.id,
                    dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                    action=normalized_action,
                    error=f"agent {agent.id} is stood down: {reason}",
                    action_id=action_id,
                    result=result,
                )
                return state_model

            git_sequence_error = _git_release_sequence_error(normalized_action)
            if git_sequence_error is not None:
                _mark_action_failure(
                    state_model,
                    runtime=runtime,
                    agent_id=agent.id,
                    dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                    action=normalized_action,
                    error=git_sequence_error,
                    action_id=action_id,
                    result=result,
                )
                return state_model

            progress = False
            context_rotation_activity = False
            projects_to_recalc: set[str] = set()
            enforce_manager_fallback_review_routing = (
                runtime.wake_reason == "manager_fallback" and _is_team_manager(state_model, agent)
            )

            def _mark_progress(*, context_activity: bool = True) -> None:
                nonlocal progress, context_rotation_activity
                progress = True
                if context_activity:
                    context_rotation_activity = True

            for index, stand_down_request in enumerate(
                _list_of_mappings(normalized_action.get("stand_downs"), "stand_downs")
            ):
                try:
                    stand_down_id = _apply_superior_stand_down(
                        state_model,
                        actor=agent,
                        request=stand_down_request,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "stand_downs", index, exc)
                    continue
                result["created_stand_down_ids"].append(stand_down_id)
                _mark_progress()

            for index, revoke_request in enumerate(
                _list_of_mappings(
                    normalized_action.get("stand_down_revocations"),
                    "stand_down_revocations",
                )
            ):
                try:
                    revoked_ids = _apply_superior_stand_down_revocation(
                        state_model,
                        actor=agent,
                        request=revoke_request,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "stand_down_revocations", index, exc)
                    continue
                result["revoked_stand_down_ids"].extend(revoked_ids)
                _mark_progress()

            for index, case_update in enumerate(
                _list_of_mappings(normalized_action.get("case_updates"), "case_updates")
            ):
                case_task_state = _snapshot_case_task_state(state_model)
                try:
                    case_id, created = _apply_case_update(
                        state_model,
                        actor=agent,
                        update=case_update,
                    )
                except ValueError as exc:
                    _restore_case_task_state(state_model, case_task_state)
                    _append_action_item_error(result, "case_updates", index, exc)
                    continue
                if created:
                    result["created_case_ids"].append(case_id)
                else:
                    result["updated_case_ids"].append(case_id)
                _mark_progress()

            for index, update in enumerate(
                _list_of_mappings(normalized_action.get("task_updates"), "task_updates")
            ):
                try:
                    task_id = _required_text(update.get("task_id"), "task_id")
                    task = _require_present(state_model.tasks, task_id, "task")
                    is_owner = task.owner_agent_id == agent.id
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
                            state_model,
                            actor=agent,
                            task=task,
                            raw_state=raw_state,
                            new_owner_id=(new_owner_id if has_owner_reassignment else None),
                        )
                    )
                    # State, priority, title, and description remain owner-only,
                    # except that a superior may close claimable subordinate work.
                    if has_owner_only_edit and not is_owner and not subordinate_finish_allowed:
                        result["errors"].append(f"agent {agent.id} cannot update task {task_id}")
                        continue
                    if has_owner_reassignment:
                        reassignment_error = _task_owner_reassignment_error(
                            state_model,
                            actor=agent,
                            task=task,
                            new_owner_id=new_owner_id,
                        )
                        if reassignment_error is not None:
                            result["errors"].append(reassignment_error)
                            continue
                    new_dependency_task_ids: list[str] | None = None
                    if has_dependency_edit:
                        graph_permitted = (
                            is_owner
                            or _supervises(state_model, agent.id, task.owner_agent_id)
                            or _is_project_lead_for(state_model, agent.id, task.project_id)
                            or _is_team_manager(state_model, agent)
                        )
                        if not graph_permitted:
                            result["errors"].append(
                                f"agent {agent.id} is not permitted to edit the task graph "
                                f"for task {task_id}"
                            )
                            continue
                        new_dependency_task_ids = _string_list(
                            update.get("dependency_task_ids"), "dependency_task_ids"
                        )
                        graph_error = _task_graph_edit_error(
                            state_model,
                            task_id=task.id,
                            project_id=task.project_id,
                            parent_task_id=task.parent_task_id,
                            dependency_task_ids=new_dependency_task_ids,
                        )
                        if graph_error is not None:
                            result["errors"].append(graph_error)
                            continue
                    changed = False
                    now = utc_now()
                    if raw_state is not None:
                        _set_task_state(task, TeamTaskState(raw_state), now=now)
                        projects_to_recalc.add(task.project_id)
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
                        task.owner_agent_id = new_owner_id
                        _maybe_mark_idle_context_rotation_due(
                            state_model,
                            agent_id=old_owner_id,
                            now=now,
                        )
                        if task.state in _NON_TERMINAL_TASK_STATES:
                            _mark_agent_work_due(
                                state_model,
                                agent_id=new_owner_id,
                                now=now,
                            )
                        changed = True
                    if new_dependency_task_ids is not None:
                        task.dependency_task_ids = new_dependency_task_ids
                        changed = True
                    if changed:
                        task.updated_at = now
                        if task.state in _NON_TERMINAL_TASK_STATES:
                            _append_agent_wake_requested_event(
                                state_model,
                                agent_id=task.owner_agent_id,
                                result=result,
                                reason="task_ready",
                                now=now,
                                delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                            )
                        _mark_progress()
                        result["updated_task_ids"].append(task.id)
                except ValueError as exc:
                    _append_action_item_error(result, "task_updates", index, exc)

            task_creation_plans: list[_TaskCreationPlan] = []
            reserved_task_ids: set[str] = set()
            for index, creation in enumerate(
                _list_of_mappings(normalized_action.get("task_creations"), "task_creations")
            ):
                try:
                    plan = _plan_task_creation(
                        state_model,
                        actor=agent,
                        creation=creation,
                        result=result,
                        reserved_task_ids=reserved_task_ids,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "task_creations", index, exc)
                    continue
                if plan is None:
                    continue
                reserved_task_ids.add(plan.task_id)
                task_creation_plans.append(plan)

            graph_errors = _task_creation_batch_graph_errors(
                state_model,
                task_creation_plans,
                actor=agent,
            )
            for plan in task_creation_plans:
                graph_error = graph_errors.get(plan.task_id)
                if graph_error is not None:
                    result["errors"].append(graph_error)
                    continue
                created_id = _apply_task_creation_plan(
                    state_model,
                    plan=plan,
                    actor=agent,
                )
                result["created_task_ids"].append(created_id)
                projects_to_recalc.add(state_model.tasks[created_id].project_id)
                _append_agent_wake_requested_event(
                    state_model,
                    agent_id=plan.owner_id,
                    result=result,
                    reason="task_ready",
                    now=utc_now(),
                    delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                )
                _mark_progress()

            for index, question in enumerate(
                _list_of_mappings(normalized_action.get("questions"), "questions")
            ):
                try:
                    recipient_id = (
                        _optional_text(question.get("recipient_agent_id"))
                        or agent.reports_to_agent_id
                    )
                    upward_error = _upward_recipient_error(agent, recipient_id, "questions")
                    if upward_error is not None:
                        result["errors"].append(upward_error)
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
                    _append_action_item_error(result, "questions", index, exc)
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=agent.id,
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
                state_model.inbox[item.id] = item
                result["created_inbox_item_ids"].append(item.id)
                _append_agent_wake_requested_event_for_inbox_item(
                    state_model,
                    item=item,
                    result=result,
                    now=utc_now(),
                )
                _mark_progress()

            for index, report in enumerate(
                _list_of_mappings(
                    normalized_action.get("status_reports"),
                    "status_reports",
                )
            ):
                try:
                    recipient_id = (
                        _optional_text(report.get("recipient_agent_id"))
                        or agent.reports_to_agent_id
                    )
                    upward_error = _upward_recipient_error(agent, recipient_id, "status_reports")
                    if upward_error is not None:
                        result["errors"].append(upward_error)
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
                    _append_action_item_error(result, "status_reports", index, exc)
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=agent.id,
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
                state_model.inbox[item.id] = item
                result["created_inbox_item_ids"].append(item.id)
                _append_agent_wake_requested_event_for_inbox_item(
                    state_model,
                    item=item,
                    result=result,
                    now=utc_now(),
                )
                _mark_progress(context_activity=False)

            for index, status_request in enumerate(
                _list_of_mappings(normalized_action.get("status_requests"), "status_requests")
            ):
                try:
                    recipient_id = _required_text(
                        status_request.get("recipient_agent_id"), "recipient_agent_id"
                    )
                    if not _is_direct_subordinate(agent, recipient_id):
                        result["errors"].append(
                            f"agent {agent.id} can only request status from a direct subordinate"
                        )
                        continue
                    inbox_id = f"inbox-{uuid4().hex}"
                    item = AgentInboxItem(
                        id=inbox_id,
                        recipient_agent_id=recipient_id,
                        sender_agent_id=agent.id,
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
                    state_model.inbox[item.id] = item
                    result["created_status_request_ids"].append(item.id)
                    _append_agent_wake_requested_event_for_inbox_item(
                        state_model,
                        item=item,
                        result=result,
                        now=utc_now(),
                    )
                    _mark_progress()
                except ValueError as exc:
                    _append_action_item_error(result, "status_requests", index, exc)

            for index, reply in enumerate(
                _list_of_mappings(normalized_action.get("chat_replies"), "chat_replies")
            ):
                try:
                    created_id = _apply_chat_reply(
                        state_model,
                        actor=agent,
                        reply=reply,
                        result=result,
                    )
                except ValueError as exc:
                    error_detail = f"chat_replies[{index}]: {exc}"
                    _append_action_item_error(result, "chat_replies", index, exc)
                    _mark_chat_dispatch_message_failed(
                        state_model,
                        dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id,
                        error=error_detail,
                    )
                    continue
                result["created_chat_reply_message_ids"].append(created_id)
                _mark_progress()

            for index, chat_message in enumerate(
                _list_of_mappings(
                    normalized_action.get("operator_chat_messages"),
                    "operator_chat_messages",
                )
            ):
                try:
                    created_id = _apply_operator_chat_message(
                        state_model,
                        actor=agent,
                        message=chat_message,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "operator_chat_messages", index, exc)
                    continue
                result["created_operator_chat_message_ids"].append(created_id)
                _mark_progress()

            for escalation in _list_of_mappings(
                normalized_action.get("operator_escalations"), "operator_escalations"
            ):
                if _apply_operator_escalation(
                    state_model,
                    actor=agent,
                    escalation=escalation,
                    result=result,
                ):
                    _mark_progress()

            for index, deliverable_payload in enumerate(
                _list_of_mappings(normalized_action.get("deliverables"), "deliverables")
            ):
                try:
                    task_id = _required_text(deliverable_payload.get("task_id"), "task_id")
                    task = _require_present(state_model.tasks, task_id, "task")
                    if task.owner_agent_id != agent.id:
                        result["errors"].append(f"agent {agent.id} cannot deliver task {task_id}")
                        continue
                    deliverable_id = _optional_text(deliverable_payload.get("id"))
                    if deliverable_id is None:
                        deliverable_id = _generated_id(
                            prefix="deliverable",
                            labels=[task_id, deliverable_payload.get("summary")],
                            existing_ids=state_model.deliverables,
                        )
                    artifact_refs = _string_list(
                        deliverable_payload.get("artifact_refs"), "artifact_refs"
                    )
                    artifact_space_ref = _optional_text(
                        deliverable_payload.get("artifact_space_ref")
                    )
                    if artifact_space_ref:
                        artifact_refs = _append_unique(artifact_refs, artifact_space_ref)
                    bind_deliverable_git_refs_to_work_context(
                        state_model,
                        agent_id=agent.id,
                        task_id=task.id,
                        deliverable=deliverable_payload,
                        dispatch_id=effective_dispatch_id,
                    )
                    git_refs = _deliverable_git_refs_from_payload(
                        deliverable_payload.get("git_refs")
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
                        state_model,
                        task=task,
                        producer_agent_id=agent.id,
                        supersedes_deliverable_id=supersedes_deliverable_id,
                    )
                    deliverable = Deliverable(
                        id=deliverable_id,
                        task_id=task_id,
                        producer_agent_id=agent.id,
                        summary=_required_text(deliverable_payload.get("summary"), "summary"),
                        artifact_refs=artifact_refs,
                        git_refs=git_refs,
                        verification=verification,
                        state=TeamRecordState.PENDING,
                        supersedes_deliverable_id=supersedes_deliverable_id,
                    )
                    state_model.deliverables[deliverable.id] = deliverable
                    if superseded_deliverable is not None:
                        superseded_deliverable.superseded_by_deliverable_id = deliverable.id
                        superseded_deliverable.supersession_reason = (
                            "Rework deliverable submitted."
                        )
                        superseded_deliverable.superseded_at = deliverable.created_at
                        superseded_deliverable.superseded_by_actor = f"agent:{agent.id}"
                    task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
                    now = utc_now()
                    _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
                    task.updated_at = now
                    projects_to_recalc.add(task.project_id)
                    result["created_deliverable_ids"].append(deliverable.id)
                    if agent.reports_to_agent_id is not None:
                        inbox_id = f"inbox-{uuid4().hex}"
                        payload = {
                            "deliverable_id": deliverable.id,
                            "summary": deliverable.summary,
                            "artifact_refs": list(deliverable.artifact_refs),
                        }
                        verification_payload = _deliverable_verification_payload(deliverable)
                        if verification_payload is not None:
                            payload["verification"] = verification_payload
                        state_model.inbox[inbox_id] = AgentInboxItem(
                            id=inbox_id,
                            recipient_agent_id=agent.reports_to_agent_id,
                            sender_agent_id=agent.id,
                            task_id=task_id,
                            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
                            response_requested=True,
                            priority=TeamInboxPriority.LOW,
                            payload=payload,
                        )
                        result["created_inbox_item_ids"].append(inbox_id)
                    _mark_progress()
                except ValueError as exc:
                    _append_action_item_error(result, "deliverables", index, exc)

            for index, review_request in enumerate(
                _list_of_mappings(normalized_action.get("review_requests"), "review_requests")
            ):
                try:
                    created_id = _apply_review_request(
                        state_model,
                        actor=agent,
                        request=review_request,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "review_requests", index, exc)
                    continue
                if created_id is not None:
                    if _review_request_was_created(result, created_id):
                        result["created_review_request_ids"].append(created_id)
                        _mark_progress()

            for index, decision in enumerate(
                _list_of_mappings(normalized_action.get("review_decisions"), "review_decisions")
            ):
                try:
                    decided_id = _apply_review_decision(
                        state_model,
                        actor=agent,
                        decision=decision,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                        enforce_manager_fallback_routing_guard=(
                            enforce_manager_fallback_review_routing
                        ),
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "review_decisions", index, exc)
                    continue
                if decided_id is not None:
                    result["decided_review_request_ids"].append(decided_id)
                    _mark_progress()

            for index, goal_update in enumerate(
                _list_of_mappings(normalized_action.get("goal_updates"), "goal_updates")
            ):
                try:
                    updated_id = _apply_goal_update(
                        state_model,
                        actor=agent,
                        update=goal_update,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "goal_updates", index, exc)
                    continue
                if updated_id is not None:
                    result["updated_goal_ids"].append(updated_id)
                    _mark_progress()

            for index, promotion in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_review_promotions"), "git_review_promotions"
                )
            ):
                try:
                    promotion_id = _apply_git_review_promotion(
                        state_model,
                        actor=agent,
                        request=promotion,
                        result=result,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_review_promotions", index, exc)
                    continue
                if promotion_id is not None:
                    result["git_promotion_event_ids"].append(promotion_id)
                    _mark_progress()

            for index, feature_update in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_feature_updates"), "git_feature_updates"
                )
            ):
                try:
                    promotion_id = _apply_git_feature_update(
                        state_model,
                        actor=agent,
                        update=feature_update,
                        result=result,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_feature_updates", index, exc)
                    continue
                if promotion_id is not None:
                    result["git_promotion_event_ids"].append(promotion_id)
                    _mark_progress()

            for index, release_request in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_release_requests"), "git_release_requests"
                )
            ):
                try:
                    release_id = _apply_git_release_request(
                        state_model,
                        actor=agent,
                        request=release_request,
                        result=result,
                        projects_to_recalc=projects_to_recalc,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_release_requests", index, exc)
                    continue
                if release_id is not None:
                    result["release_package_ids"].append(release_id)
                    _mark_progress()

            for index, recovery_request in enumerate(
                _list_of_mappings(
                    normalized_action.get("git_release_recoveries"),
                    "git_release_recoveries",
                )
            ):
                try:
                    recovery_id = _apply_git_release_recovery(
                        state_model,
                        actor=agent,
                        request=recovery_request,
                        result=result,
                    )
                except ValueError as exc:
                    _append_action_item_error(result, "git_release_recoveries", index, exc)
                    continue
                if recovery_id is not None:
                    result["release_recovery_event_ids"].append(recovery_id)
                    _mark_progress()

            for inbox_item_id in _string_list(
                normalized_action.get("handled_inbox_item_ids"),
                "handled_inbox_item_ids",
            ):
                item = state_model.inbox.get(inbox_item_id)
                if item is None or item.recipient_agent_id != agent.id:
                    result["errors"].append(
                        f"agent {agent.id} cannot handle inbox item {inbox_item_id}"
                    )
                    continue
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = utc_now()
                result["handled_inbox_item_ids"].append(inbox_item_id)
                _mark_progress()

            ephemeral_requests = _list_of_mappings(
                normalized_action.get("ephemeral_requests"),
                "ephemeral_requests",
            )
            if ephemeral_requests:
                try:
                    _create_ephemeral_assignments(
                        state_model,
                        parent=agent,
                        requests=ephemeral_requests,
                        dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id,
                        result=result,
                    )
                    _mark_progress()
                except ValueError as exc:
                    result["errors"].append(f"ephemeral_requests: {exc}")

            for index, acceptance in enumerate(
                _list_of_mappings(
                    normalized_action.get("ephemeral_acceptances"),
                    "ephemeral_acceptances",
                )
            ):
                try:
                    assignment_id = _required_text(acceptance.get("assignment_id"), "assignment_id")
                    assignment = _require_present(
                        state_model.ephemeral_assignments,
                        assignment_id,
                        "ephemeral assignment",
                    )
                    if assignment.parent_agent_id != agent.id:
                        result["errors"].append(
                            f"agent {agent.id} cannot accept ephemeral assignment {assignment_id}"
                        )
                        continue
                    accepted = _bool_from_value(acceptance.get("accepted"), default=True)
                    _apply_ephemeral_acceptance(
                        state_model,
                        assignment=assignment,
                        accepted=accepted,
                        deliverable_id=_optional_text(acceptance.get("deliverable_id")),
                        rejection_reason=_optional_text(acceptance.get("rejection_reason")),
                    )
                    if accepted:
                        result["accepted_ephemeral_assignment_ids"].append(assignment.id)
                    else:
                        result["rejected_ephemeral_assignment_ids"].append(assignment.id)
                    _mark_progress()
                except ValueError as exc:
                    _append_action_item_error(result, "ephemeral_acceptances", index, exc)

            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, project_id)

            record = AgentActionRecord(
                id=action_id,
                agent_id=agent.id,
                dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                action=normalized_action,
                success=not result["errors"],
                error="; ".join(result["errors"]) if result["errors"] else None,
            )
            state_model.action_records[record.id] = record
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.wake_reason = None
            runtime.no_progress_count = 0 if progress else runtime.no_progress_count + 1
            completed_at = utc_now()
            if context_rotation_activity:
                runtime.last_progress_at = completed_at
            runtime.updated_at = completed_at
            _maybe_mark_idle_context_rotation_due(
                state_model,
                agent_id=agent.id,
                now=runtime.updated_at,
            )
            _append_pending_chat_message_queued_event_for_agent(
                state_model,
                agent=agent,
                completed_dispatch_id=record.dispatch_id,
                result=result,
            )
            _append_follow_up_agent_wake_requested_event(
                state_model,
                agent_id=agent.id,
                result=result,
                now=runtime.updated_at,
                completed_dispatch_id=record.dispatch_id,
                progress_made=progress,
            )
            result["success"] = progress or not result["errors"]
            result["applied"] = progress
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        result["ephemeral_events"] = [
            _ephemeral_assignment_event_payload(state, assignment_id)
            for assignment_id in result["created_ephemeral_assignment_ids"]
        ]
        _publish_admin_chat_notifications(
            runtime_dir=self._repository.runtime_dir,
            state=state,
            message_ids=[
                str(message_id)
                for message_id in (
                    list(result["created_chat_reply_message_ids"])
                    + list(result["created_operator_chat_message_ids"])
                )
            ],
        )
        return state, result
