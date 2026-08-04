from __future__ import annotations

from evander.models import utc_now
from collections.abc import (
    Callable,
    Mapping,
    Sequence,
)
from typing import Any
from uuid import uuid4
from .context import (
    _normalize_team_command_context,
    _normalized_wake_session_class,
    _resolve_command_agent,
    _resolve_command_assignment,
    _resolve_command_governance_group,
    _team_command_context_scope,
)
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from ...models import (
    AgentStatus,
    TaskAssessmentReadiness,
    TeamBlockerSourceKind,
    TeamBlockerRoute,
    TeamBlockerState,
    TeamDispatch,
    TeamDispatchStatus,
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    TeamIdempotencyRecord,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    _unique_text_list,
)
from ..domain.common import (
    _ensure_utc,
    _optional_text,
)
from ..domain.dispatch_scope import (
    _blocker_project_ids,
    _inbox_project_ids,
    _review_project_ids,
)
from ..domain.manager_fallback import (
    _manager_fallback_review_routing_authority_error,
)
from ..domain.git_work_contexts import GitCandidateCommitResolver
from ..domain.stand_down import (
    _active_stand_downs_for_agent,
    _stand_down_error_detail,
)
from ..domain.task_assessment import TaskAssessmentService
from ..domain.wake import (
    _append_newly_ready_task_owner_wake_requested_events,
    _dependency_status_snapshot,
)
from ...sqlite_store import (
    TeamIdempotencyConflictError,
    TeamStateSqliteCommandTransaction,
)

from ...repository import TeamStateRepository
from .outbox import _append_response_outbox_record_ids, _team_command_outbox_records
from .responses import (
    _command_correction_context_summary,
    _command_idempotency_conflict_response,
    _command_success_response,
)


class TeamCommandExecutor:
    def __init__(
        self,
        repository: TeamStateRepository,
        *,
        git_candidate_commit_resolver: GitCandidateCommitResolver | None = None,
        git_default_branch_resolver: Callable[..., object] | None = None,
    ) -> None:
        self._repository = repository
        self._git_candidate_commit_resolver = git_candidate_commit_resolver
        self._git_default_branch_resolver = git_default_branch_resolver

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    @property
    def git_candidate_commit_resolver(self) -> GitCandidateCommitResolver | None:
        return self._git_candidate_commit_resolver

    @property
    def git_default_branch_resolver(self) -> Callable[..., object] | None:
        return self._git_default_branch_resolver

    def _execute_team_command(
        self,
        *,
        context: TeamCommandContext,
        normalized_payload: Mapping[str, Any],
        handled_inbox_item_ids: Sequence[str],
        apply_command: Callable[
            [TeamState, _ResolvedTeamCommandContext],
            TeamCommandApplyResult,
        ],
        require_active_dispatch: bool,
        reject_stale_high_priority_inbox: bool = False,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_context = _normalize_team_command_context(context)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids,
            "handled_inbox_item_ids",
        )
        payload_digest = self._repository.normalized_payload_digest(normalized_payload)

        def _transaction(
            payload: dict[str, Any],
            tx: TeamStateSqliteCommandTransaction,
        ) -> tuple[TeamState, dict[str, Any]]:
            state = TeamState.model_validate(payload)
            resolved, resolve_error = self._resolve_team_command_context(
                state,
                normalized_context,
                require_active_dispatch=require_active_dispatch,
            )
            if resolve_error is not None:
                activity = self._append_command_failure_activity(
                    tx,
                    resolved=resolved,
                    error=resolve_error,
                )
                return state, self._command_failure_response(
                    state=state,
                    error=resolve_error,
                    activity=activity,
                )
            if resolved is None:  # pragma: no cover - defensive guard
                raise RuntimeError("command context resolution did not produce a result")

            if normalized_context.request_id is not None:
                try:
                    replay = tx.get_idempotency_record(
                        tool_name=normalized_context.tool_name,
                        caller_identity=resolved.caller_identity,
                        scope_kind=resolved.scope_kind,
                        scope_id=resolved.scope_id,
                        request_id=normalized_context.request_id,
                    )
                    if replay is not None:
                        if replay.normalized_payload_digest != payload_digest:
                            raise TeamIdempotencyConflictError(
                                tool_name=normalized_context.tool_name,
                                caller_identity=resolved.caller_identity,
                                scope_kind=resolved.scope_kind,
                                scope_id=resolved.scope_id,
                                request_id=normalized_context.request_id,
                                existing_digest=replay.normalized_payload_digest,
                                attempted_digest=payload_digest,
                            )
                        return state, dict(replay.response_payload)
                except TeamIdempotencyConflictError as exc:
                    return state, _command_idempotency_conflict_response(
                        state=state,
                        conflict=exc,
                    )

            try:
                self._validate_agent_dispatch_command_scope(
                    state,
                    resolved=resolved,
                    normalized_payload=normalized_payload,
                    handled_inbox_item_ids=normalized_handled_ids,
                )
                self._validate_stand_down_dispatch_completion_authority(
                    state,
                    resolved=resolved,
                    normalized_payload=normalized_payload,
                )
                if reject_stale_high_priority_inbox:
                    self._validate_dispatch_freshness(
                        state,
                        resolved=resolved,
                        handled_inbox_item_ids=normalized_handled_ids,
                    )
                pending_handled_ids, already_handled_ids = (
                    self._validate_command_handled_inbox_items(
                    state,
                    resolved=resolved,
                    handled_inbox_item_ids=normalized_handled_ids,
                    )
                )
                dependency_statuses_before = _dependency_status_snapshot(state)
                apply_result = apply_command(state, resolved)
                _append_newly_ready_task_owner_wake_requested_events(
                    state,
                    dependency_statuses_before=dependency_statuses_before,
                    result=apply_result.response_payload,
                    now=utc_now(),
                )
            except TeamCommandValidationError as exc:
                activity = self._append_command_failure_activity(
                    tx,
                    resolved=resolved,
                    error=exc,
                )
                return state, self._command_failure_response(
                    state=state,
                    error=exc,
                    activity=activity,
                )
            except ValueError as exc:
                error = TeamCommandValidationError(
                    code="validation_failed",
                    message=str(exc),
                )
                activity = self._append_command_failure_activity(
                    tx,
                    resolved=resolved,
                    error=error,
                )
                return state, self._command_failure_response(
                    state=state,
                    error=error,
                    activity=activity,
                )

            now = utc_now()
            for inbox_item_id in pending_handled_ids:
                item = state.inbox[inbox_item_id]
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = now

            state_changed = apply_result.state_changed or bool(pending_handled_ids)
            next_state = (
                state.model_copy(
                    update={"revision": state.revision + 1, "updated_at": now},
                    deep=True,
                )
                if state_changed
                else state
            )
            validated = TeamState.model_validate(next_state.model_dump(mode="json"))
            payload.clear()
            payload.update(validated.model_dump(mode="json"))

            activity_id = f"activity-{uuid4().hex}"
            outbox_records = _team_command_outbox_records(apply_result)
            stored_outbox_records = [
                tx.enqueue_outbox_record(outbox_record) for outbox_record in outbox_records
            ]
            response = _command_success_response(
                state=validated,
                context=normalized_context,
                activity_id=activity_id,
                handled_inbox_item_ids=pending_handled_ids,
                already_handled_inbox_item_ids=already_handled_ids,
                apply_result=apply_result,
            )
            activity_progress_classification = (
                _optional_text(apply_result.progress_classification)
                or normalized_context.progress_classification
            )
            _append_response_outbox_record_ids(response, stored_outbox_records)
            tx.append_dispatch_activity(
                TeamDispatchActivity(
                    id=activity_id,
                    tool_name=normalized_context.tool_name,
                    caller_session_id=normalized_context.ambient_session_id,
                    caller_identity=resolved.caller_identity,
                    agent_id=resolved.agent.id if resolved.agent is not None else None,
                    governance_group_id=resolved.governance_group_id,
                    assignment_id=resolved.assignment_id,
                    dispatch_id=resolved.dispatch_id,
                    request_id=normalized_context.request_id,
                    correlation_id=normalized_context.correlation_id,
                    causation_id=normalized_context.causation_id,
                    status=TeamDispatchActivityStatus.SUCCESS,
                    progress_classification=activity_progress_classification,
                    action_record_id=_optional_text(response.get("action_record_id")),
                    audit_record_id=_optional_text(response.get("audit_record_id")),
                )
            )
            if normalized_context.request_id is not None:
                created_domain_ids = list(apply_result.created_domain_ids)
                for outbox_record in stored_outbox_records:
                    if outbox_record.id not in created_domain_ids:
                        created_domain_ids.append(outbox_record.id)
                tx.store_idempotency_record(
                    TeamIdempotencyRecord(
                        id=f"idempotency-{uuid4().hex}",
                        tool_name=normalized_context.tool_name,
                        caller_identity=resolved.caller_identity,
                        scope_kind=resolved.scope_kind,
                        scope_id=resolved.scope_id,
                        request_id=normalized_context.request_id,
                        normalized_payload_digest=payload_digest,
                        response_payload=response,
                        created_domain_ids=created_domain_ids,
                        created_event_ids=apply_result.created_event_ids,
                    )
                )
            return validated, response

        return self._repository.store.update_object_with_command_transaction(_transaction)

    def _validate_agent_dispatch_command_scope(
        self,
        state: TeamState,
        *,
        resolved: _ResolvedTeamCommandContext,
        normalized_payload: Mapping[str, Any],
        handled_inbox_item_ids: Sequence[str],
    ) -> None:
        if resolved.agent is None or resolved.dispatch_id is None:
            return
        dispatch = state.dispatches.get(resolved.dispatch_id)
        if dispatch is None:
            # Context resolution produces the stable missing-dispatch error before
            # command validation. This guard keeps governance/assignment paths inert.
            return
        if (
            dispatch.cause_kind == "team_chat"
            and not dispatch.project_ids
            and resolved.context.tool_name != "team_reply_to_chat"
        ):
            raise _dispatch_scope_error(
                dispatch,
                scope_kind="Team mutation tool",
                requested_ids=[resolved.context.tool_name],
                allowed_ids=[],
            )
        targets = _command_dispatch_scope_targets(
            state,
            tool_name=resolved.context.tool_name,
            payload=normalized_payload,
            handled_inbox_item_ids=handled_inbox_item_ids,
        )
        _validate_dispatch_inbox_scope(
            state,
            dispatch=dispatch,
            inbox_item_ids=targets["inbox_item_ids"],
        )
        _validate_dispatch_task_scope(
            state,
            dispatch=dispatch,
            task_ids=targets["task_ids"],
            require_actionable=(
                resolved.context.tool_name not in _DELIVERABLE_SUBMISSION_TOOLS
            ),
        )
        _validate_dispatch_blocker_scope(
            state,
            dispatch=dispatch,
            blocker_ids=targets["blocker_ids"],
        )
        _validate_dispatch_review_scope(
            state,
            dispatch=dispatch,
            review_request_ids=targets["review_request_ids"],
        )
        request_review_routing_project_ids: set[str] = set()
        if resolved.context.tool_name == "team_request_review":
            request_review_routing_project_ids = (
                _validate_request_review_dispatch_scope(
                    state,
                    dispatch=dispatch,
                    payload=normalized_payload,
                )
            )
        _validate_dispatch_project_scope(
            state,
            dispatch=dispatch,
            project_ids=(
                targets["project_ids"]
                - request_review_routing_project_ids
            ),
        )
        _validate_dispatch_managed_repo_scope(
            dispatch=dispatch,
            managed_repo_ids=targets["managed_repo_ids"],
        )
        if resolved.context.tool_name == "team_decide_deliverable":
            deliverable_id = _optional_text(normalized_payload.get("deliverable_id"))
            deliverable = state.deliverables.get(deliverable_id or "")
            task = (
                state.tasks.get(deliverable.task_id)
                if deliverable is not None
                else None
            )
            if (
                _is_role_bounded_operator_chat_dispatch(dispatch)
                and task is not None
            ):
                _validate_dispatch_project_scope(
                    state,
                    dispatch=dispatch,
                    project_ids={task.project_id},
                )
            else:
                matching_notice_ids = {
                    item.id
                    for item in state.inbox.values()
                    if item.id in dispatch.inbox_item_ids
                    and item.recipient_agent_id == dispatch.agent_id
                    and item.state == TeamInboxItemState.PENDING
                    and item.payload.get("deliverable_id") == deliverable_id
                }
                if not matching_notice_ids:
                    raise _dispatch_scope_error(
                        dispatch,
                        scope_kind="inbox item",
                        requested_ids=[
                            deliverable_id or "unknown deliverable notice"
                        ],
                        allowed_ids=dispatch.inbox_item_ids,
                    )
        if resolved.context.tool_name == "team_report_blocker":
            _validate_report_blocker_dispatch_scope(
                state,
                dispatch=dispatch,
                payload=normalized_payload,
            )
        if resolved.context.tool_name == "team_cancel_task":
            _validate_cancel_task_dispatch_scope(
                state,
                dispatch=dispatch,
                payload=normalized_payload,
            )
        if resolved.context.tool_name == "team_link_deliverable_replacement":
            _validate_deliverable_replacement_dispatch_scope(
                state,
                dispatch=dispatch,
                payload=normalized_payload,
            )

    @staticmethod
    def _validate_stand_down_dispatch_completion_authority(
        state: TeamState,
        *,
        resolved: _ResolvedTeamCommandContext,
        normalized_payload: Mapping[str, Any],
    ) -> None:
        if resolved.agent is None:
            return
        active_stand_downs = _active_stand_downs_for_agent(
            state,
            resolved.agent.id,
        )
        if not active_stand_downs:
            return
        dispatch = state.dispatches.get(resolved.dispatch_id or "")
        leased_at = dispatch.leased_at if dispatch is not None else None
        dispatch_predates_stand_downs = (
            dispatch is not None
            and leased_at is not None
            and all(
                _ensure_utc(leased_at) <= _ensure_utc(stand_down.set_at)
                for stand_down in active_stand_downs
            )
        )
        task_updates = normalized_payload.get("updates")
        scoped_support_task_finish = (
            resolved.context.tool_name == "team_update_task"
            and isinstance(task_updates, Mapping)
            and dict(task_updates) == {"state": TeamTaskState.FINISHED.value}
        )
        completion_tool_allowed = (
            resolved.context.tool_name in _STAND_DOWN_DISPATCH_COMPLETION_TOOLS
            or scoped_support_task_finish
        )
        if dispatch_predates_stand_downs and completion_tool_allowed:
            return
        raise TeamCommandValidationError(
            code="agent_stood_down",
            message=(
                f"agent {resolved.agent.id} is stood down: "
                f"{_stand_down_error_detail(active_stand_downs)}"
            ),
            correction_context={
                "agent_id": resolved.agent.id,
                "active_dispatch_id": resolved.dispatch_id,
                "stand_down_ids": [item.id for item in active_stand_downs],
                "dispatch_predates_stand_downs": dispatch_predates_stand_downs,
                "completion_tool_allowed": completion_tool_allowed,
            },
        )

    def _validate_dispatch_freshness(
        self,
        state: TeamState,
        *,
        resolved: _ResolvedTeamCommandContext,
        handled_inbox_item_ids: Sequence[str],
    ) -> None:
        if resolved.agent is None or resolved.runtime is None:
            return
        last_wake_at = getattr(resolved.runtime, "last_wake_at", None)
        if last_wake_at is None:
            return
        handled = set(handled_inbox_item_ids)
        newer_items = sorted(
            (
                item
                for item in state.inbox.values()
                if item.recipient_agent_id == resolved.agent.id
                and item.state == TeamInboxItemState.PENDING
                and item.priority == TeamInboxPriority.HIGH
                and item.id not in handled
                and _ensure_utc(item.created_at) > _ensure_utc(last_wake_at)
            ),
            key=lambda item: (item.created_at, item.id),
        )
        if not newer_items:
            return
        raise TeamCommandValidationError(
            code="stale_dispatch_context",
            message=(
                "new high-priority inbox work arrived after this dispatch wake "
                "snapshot and is intentionally outside this dispatch's immutable "
                "scope; end the current turn without retrying the terminal action. "
                "Dispatch finalization will request a fresh wake so the new item "
                "can be handled under a new dispatch"
            ),
            correction_context={
                "dispatch_id": resolved.dispatch_id,
                "last_wake_at": _ensure_utc(last_wake_at).isoformat(),
                "recovery_action": "end_current_turn",
                "fresh_dispatch_required": True,
                "current_dispatch_scope_unchanged": True,
                "newer_high_priority_inbox_items": [
                    {
                        "id": item.id,
                        "item_type": item.item_type.value,
                        "task_id": item.task_id,
                        "created_at": item.created_at.isoformat(),
                        "kind": item.payload.get("kind"),
                        "summary": item.payload.get("summary")
                        or item.payload.get("message")
                        or item.payload.get("question"),
                    }
                    for item in newer_items
                ],
            },
        )

    def _resolve_team_command_context(
        self,
        state: TeamState,
        context: TeamCommandContext,
        *,
        require_active_dispatch: bool,
    ) -> tuple[_ResolvedTeamCommandContext | None, TeamCommandValidationError | None]:
        try:
            scope = _team_command_context_scope(context)
        except TeamCommandValidationError as exc:
            return None, exc
        if scope == "governance":
            return self._resolve_governance_command_context(
                state,
                context,
                require_active_dispatch=require_active_dispatch,
            )
        if scope == "assignment":
            return self._resolve_assignment_command_context(
                state,
                context,
                require_active_dispatch=require_active_dispatch,
            )
        return self._resolve_agent_command_context(
            state,
            context,
            require_active_dispatch=require_active_dispatch,
        )

    def _resolve_agent_command_context(
        self,
        state: TeamState,
        context: TeamCommandContext,
        *,
        require_active_dispatch: bool,
    ) -> tuple[_ResolvedTeamCommandContext | None, TeamCommandValidationError | None]:
        try:
            agent = _resolve_command_agent(state, context)
        except TeamCommandValidationError as exc:
            return None, exc
        caller_identity = f"agent:{agent.id}"
        runtime = state.agent_runtimes.get(agent.id)
        dispatch_id = context.active_dispatch_id or (
            runtime.active_dispatch_id if runtime else None
        )
        resolved = _ResolvedTeamCommandContext(
            context=context,
            caller_identity=caller_identity,
            scope_kind="dispatch" if dispatch_id is not None else "agent",
            scope_id=dispatch_id or agent.id,
            agent=agent,
            runtime=runtime,
            dispatch_id=dispatch_id,
        )
        if agent.status != AgentStatus.ACTIVE:
            return resolved, TeamCommandValidationError(
                code="agent_not_active",
                message=f"agent {agent.id} is {agent.status.value}",
                correction_context={"agent_id": agent.id, "status": agent.status.value},
            )
        teamless_chat_reply = (
            context.tool_name == "team_reply_to_chat"
            and _normalized_wake_session_class(context.wake_session_class) == "team_chat"
        )
        if agent.team_id is None and not teamless_chat_reply:
            return resolved, TeamCommandValidationError(
                code="teamless_agent",
                message=f"agent {agent.id} is not assigned to a team",
                correction_context={"agent_id": agent.id},
            )
        active_stand_downs = _active_stand_downs_for_agent(state, agent.id)
        if active_stand_downs and (
            not require_active_dispatch
            or runtime is None
            or runtime.active_dispatch_id is None
        ):
            return resolved, TeamCommandValidationError(
                code="agent_stood_down",
                message=f"agent {agent.id} is stood down: {_stand_down_error_detail(active_stand_downs)}",
                correction_context={"agent_id": agent.id},
            )
        if not require_active_dispatch:
            return resolved, None
        if runtime is None or runtime.active_dispatch_id is None:
            return resolved, TeamCommandValidationError(
                code="no_active_dispatch",
                message=f"agent {agent.id} has no active Team dispatch lease",
                correction_context={"agent_id": agent.id},
            )
        if (
            context.active_dispatch_id is not None
            and context.active_dispatch_id != runtime.active_dispatch_id
        ):
            return resolved, TeamCommandValidationError(
                code="dispatch_mismatch",
                message=(
                    f"dispatch_id {context.active_dispatch_id} does not match active "
                    f"Team dispatch {runtime.active_dispatch_id}"
                ),
                correction_context={
                    "agent_id": agent.id,
                    "active_dispatch_id": runtime.active_dispatch_id,
                    "requested_dispatch_id": context.active_dispatch_id,
                },
            )
        durable_dispatch = state.dispatches.get(runtime.active_dispatch_id)
        if durable_dispatch is None:
            return resolved, TeamCommandValidationError(
                code="dispatch_not_found",
                message=(
                    f"active Team dispatch {runtime.active_dispatch_id} has no "
                    "durable scope record"
                ),
                correction_context={
                    "agent_id": agent.id,
                    "active_dispatch_id": runtime.active_dispatch_id,
                },
            )
        if (
            durable_dispatch.agent_id != agent.id
            or durable_dispatch.session_id != agent.session_id
        ):
            return resolved, TeamCommandValidationError(
                code="dispatch_identity_mismatch",
                message=(
                    f"dispatch {durable_dispatch.id} does not belong to "
                    f"agent session {agent.id}/{agent.session_id}"
                ),
                correction_context={
                    "agent_id": agent.id,
                    "session_id": agent.session_id,
                    "dispatch_agent_id": durable_dispatch.agent_id,
                    "dispatch_session_id": durable_dispatch.session_id,
                },
            )
        if durable_dispatch.status != TeamDispatchStatus.LEASED:
            return resolved, TeamCommandValidationError(
                code="dispatch_not_leased",
                message=(
                    f"active Team dispatch {durable_dispatch.id} is "
                    f"{durable_dispatch.status.value}, not leased"
                ),
                correction_context={
                    "agent_id": agent.id,
                    "active_dispatch_id": durable_dispatch.id,
                    "dispatch_status": durable_dispatch.status.value,
                },
            )
        now = utc_now()
        if (
            runtime.wake_leased_until is None
            or _ensure_utc(runtime.wake_leased_until) <= now
            or durable_dispatch.lease_expires_at is None
            or _ensure_utc(durable_dispatch.lease_expires_at) <= now
        ):
            return resolved, TeamCommandValidationError(
                code="dispatch_lease_expired",
                message=f"active Team dispatch {runtime.active_dispatch_id} is not leased",
                correction_context={
                    "agent_id": agent.id,
                    "active_dispatch_id": runtime.active_dispatch_id,
                    "wake_leased_until": (
                        runtime.wake_leased_until.isoformat()
                        if runtime.wake_leased_until is not None
                        else None
                    ),
                    "dispatch_lease_expires_at": (
                        durable_dispatch.lease_expires_at.isoformat()
                        if durable_dispatch.lease_expires_at is not None
                        else None
                    ),
                },
            )
        resolved = _ResolvedTeamCommandContext(
            context=context,
            caller_identity=caller_identity,
            scope_kind="dispatch",
            scope_id=runtime.active_dispatch_id,
            agent=agent,
            runtime=runtime,
            dispatch_id=runtime.active_dispatch_id,
        )
        return resolved, None

    def _resolve_governance_command_context(
        self,
        state: TeamState,
        context: TeamCommandContext,
        *,
        require_active_dispatch: bool,
    ) -> tuple[_ResolvedTeamCommandContext | None, TeamCommandValidationError | None]:
        try:
            agent = _resolve_command_agent(state, context)
        except TeamCommandValidationError as exc:
            return None, exc
        group, group_error = _resolve_command_governance_group(state, context, agent)
        group_id = group.id if group is not None else context.governance_group_id
        runtime = state.governance_runtimes.get(group.id) if group is not None else None
        dispatch_id = context.active_dispatch_id or (
            runtime.active_dispatch_id if runtime else None
        )
        resolved = _ResolvedTeamCommandContext(
            context=context,
            caller_identity=(
                f"governance:{group_id}:{agent.id}"
                if group_id is not None
                else f"governance:{agent.id}"
            ),
            scope_kind="dispatch" if dispatch_id is not None else "governance_group",
            scope_id=dispatch_id or group_id or agent.id,
            agent=agent,
            governance_group_id=group_id,
            dispatch_id=dispatch_id,
        )
        if agent.status != AgentStatus.ACTIVE:
            return resolved, TeamCommandValidationError(
                code="agent_not_active",
                message=f"governance manager {agent.id} is {agent.status.value}",
                correction_context={"agent_id": agent.id, "status": agent.status.value},
            )
        if group_error is not None:
            return resolved, group_error
        if group is None:  # pragma: no cover - defensive guard
            raise RuntimeError("governance command resolution produced no group")
        if require_active_dispatch:
            if runtime is None or runtime.active_dispatch_id is None:
                return resolved, TeamCommandValidationError(
                    code="no_active_governance_dispatch",
                    message=f"governance group {group.id} has no active dispatch lease",
                    correction_context={"governance_group_id": group.id},
                )
            if (
                context.active_dispatch_id is not None
                and context.active_dispatch_id != runtime.active_dispatch_id
            ):
                return resolved, TeamCommandValidationError(
                    code="dispatch_mismatch",
                    message=(
                        f"dispatch_id {context.active_dispatch_id} does not match active "
                        f"governance dispatch {runtime.active_dispatch_id}"
                    ),
                    correction_context={
                        "governance_group_id": group.id,
                        "active_dispatch_id": runtime.active_dispatch_id,
                        "requested_dispatch_id": context.active_dispatch_id,
                    },
                )
            if (
                runtime.wake_leased_until is None
                or _ensure_utc(runtime.wake_leased_until) <= utc_now()
            ):
                return resolved, TeamCommandValidationError(
                    code="dispatch_lease_expired",
                    message=f"active governance dispatch {runtime.active_dispatch_id} is not leased",
                    correction_context={
                        "governance_group_id": group.id,
                        "active_dispatch_id": runtime.active_dispatch_id,
                        "wake_leased_until": (
                            runtime.wake_leased_until.isoformat()
                            if runtime.wake_leased_until is not None
                            else None
                        ),
                    },
                )
            resolved = _ResolvedTeamCommandContext(
                context=context,
                caller_identity=f"governance:{group.id}:{agent.id}",
                scope_kind="dispatch",
                scope_id=runtime.active_dispatch_id,
                agent=agent,
                governance_group_id=group.id,
                dispatch_id=runtime.active_dispatch_id,
            )
        return resolved, None

    def _resolve_assignment_command_context(
        self,
        state: TeamState,
        context: TeamCommandContext,
        *,
        require_active_dispatch: bool,
    ) -> tuple[_ResolvedTeamCommandContext | None, TeamCommandValidationError | None]:
        assignment, assignment_error = _resolve_command_assignment(state, context)
        assignment_id = assignment.id if assignment is not None else context.assignment_id
        resolved = (
            _ResolvedTeamCommandContext(
                context=context,
                caller_identity=(
                    f"assignment:{assignment_id}"
                    if assignment_id is not None
                    else f"worker:{context.ambient_session_id}"
                ),
                scope_kind="assignment",
                scope_id=assignment_id or context.ambient_session_id,
                assignment_id=assignment_id,
            )
            if assignment_id is not None
            else None
        )
        if assignment_error is not None:
            return resolved, assignment_error
        if assignment is None:  # pragma: no cover - defensive guard
            raise RuntimeError("assignment command resolution produced no assignment")
        resolved = _ResolvedTeamCommandContext(
            context=context,
            caller_identity=f"assignment:{assignment.id}",
            scope_kind="assignment",
            scope_id=assignment.id,
            assignment_id=assignment.id,
        )
        if require_active_dispatch:
            return resolved, TeamCommandValidationError(
                code="assignment_dispatch_not_supported",
                message="ephemeral assignment commands do not use active Team dispatch leases",
                correction_context={"assignment_id": assignment.id},
            )
        return resolved, None

    def _validate_command_handled_inbox_items(
        self,
        state: TeamState,
        *,
        resolved: _ResolvedTeamCommandContext,
        handled_inbox_item_ids: Sequence[str],
    ) -> tuple[list[str], list[str]]:
        if not handled_inbox_item_ids:
            return [], []
        if resolved.agent is None:
            raise TeamCommandValidationError(
                code="invalid_inbox_scope",
                message="handled_inbox_item_ids requires an agent caller",
            )
        pending: list[str] = []
        already_handled: list[str] = []
        for inbox_item_id in handled_inbox_item_ids:
            item = state.inbox.get(inbox_item_id)
            if item is None:
                raise TeamCommandValidationError(
                    code="inbox_item_not_found",
                    message=f"inbox item {inbox_item_id} does not exist",
                    correction_context={"inbox_item_id": inbox_item_id},
                )
            if item.recipient_agent_id != resolved.agent.id:
                raise TeamCommandValidationError(
                    code="inbox_item_not_owned",
                    message=f"agent {resolved.agent.id} cannot handle inbox item {inbox_item_id}",
                    correction_context={
                        "inbox_item_id": inbox_item_id,
                        "recipient_agent_id": item.recipient_agent_id,
                        "caller_agent_id": resolved.agent.id,
                    },
                )
            if item.state == TeamInboxItemState.PENDING:
                pending.append(inbox_item_id)
            else:
                already_handled.append(inbox_item_id)
        return pending, already_handled

    def _append_command_failure_activity(
        self,
        tx: TeamStateSqliteCommandTransaction,
        *,
        resolved: _ResolvedTeamCommandContext | None,
        error: TeamCommandValidationError,
    ) -> TeamDispatchActivity | None:
        if resolved is None:
            return None
        context = resolved.context
        if not (resolved.agent or resolved.governance_group_id or resolved.assignment_id):
            return None
        return tx.append_dispatch_activity(
            TeamDispatchActivity(
                id=f"activity-{uuid4().hex}",
                tool_name=context.tool_name,
                caller_session_id=context.ambient_session_id,
                caller_identity=resolved.caller_identity,
                agent_id=resolved.agent.id if resolved.agent is not None else None,
                governance_group_id=resolved.governance_group_id,
                assignment_id=resolved.assignment_id,
                dispatch_id=resolved.dispatch_id,
                request_id=context.request_id,
                correlation_id=context.correlation_id,
                causation_id=context.causation_id,
                status=TeamDispatchActivityStatus.FAILURE,
                progress_classification="none",
                error_code=error.code,
                error_message=error.message,
                correction_context_summary=_command_correction_context_summary(
                    error.correction_context,
                ),
            )
        )

    @staticmethod
    def _command_failure_response(
        *,
        state: TeamState,
        error: TeamCommandValidationError,
        activity: TeamDispatchActivity | None,
    ) -> dict[str, Any]:
        error_payload: dict[str, Any] = {
            "code": error.code,
            "message": error.message,
        }
        if error.correction_context:
            error_payload["correction_context"] = dict(error.correction_context)
        return {
            "success": False,
            "applied": False,
            "progress_made": False,
            "revision": state.revision,
            "activity_id": activity.id if activity is not None else None,
            "action_record_id": None,
            "errors": [error_payload],
            "correction_context": dict(error.correction_context),
        }


_TASK_SCOPE_TOOLS = frozenset(
    {
        "team_update_task",
        "team_submit_deliverable",
        "team_submit_deliverable_for_review",
        "team_request_ephemeral_agent",
        "team_request_ephemeral_agents",
        "team_accept_ephemeral_result",
        "team_reject_ephemeral_result",
    }
)
_DELIVERABLE_SUBMISSION_TOOLS = frozenset(
    {
        "team_submit_deliverable",
        "team_submit_deliverable_for_review",
    }
)
_BLOCKER_SCOPE_TOOLS = frozenset({"team_update_blocker"})
_REVIEW_SCOPE_TOOLS = frozenset({"team_decide_review"})
_STAND_DOWN_DISPATCH_COMPLETION_TOOLS = frozenset(
    {
        "team_accept_ephemeral_result",
        "team_decide_deliverable",
        "team_decide_review",
        "team_link_deliverable_replacement",
        "team_reject_ephemeral_result",
        "team_reply_to_chat",
        "team_submit_deliverable",
        "team_submit_deliverable_for_review",
    }
)
_PROJECT_SCOPE_TOOLS = frozenset(
    {
        "team_create_task",
        "team_create_tasks",
        "team_cancel_task",
        "team_update_goal",
        "team_create_case",
        "team_update_case",
        "team_propose_human_finding_resolution",
        "team_route_human_finding_correction",
        "team_close_post_release_case",
        "team_record_memory",
        "team_archive_memory",
        "team_create_outcome_kind",
        "team_update_outcome_kind",
        "team_archive_outcome_kind",
        "team_publish_review_branch",
        "team_update_feature_branch",
        "team_request_release",
        "team_recover_release",
        "team_retry_release_gate",
        "team_retry_review",
        "team_request_review",
        "team_report_blocker",
        "team_request_status",
        "team_ask_question",
        "team_send_progress_report",
        "team_escalate_operator",
    }
)


def _command_dispatch_scope_targets(
    state: TeamState,
    *,
    tool_name: str,
    payload: Mapping[str, Any],
    handled_inbox_item_ids: Sequence[str],
) -> dict[str, set[str]]:
    targets = {
        "task_ids": set(),
        "blocker_ids": set(),
        "review_request_ids": set(),
        "inbox_item_ids": set(handled_inbox_item_ids),
        "project_ids": set(),
        "managed_repo_ids": set(),
    }
    for inbox_id in _text_sequence(payload.get("inbox_item_ids")):
        targets["inbox_item_ids"].add(inbox_id)
    for inbox_id in _text_sequence(payload.get("handled_inbox_item_ids")):
        targets["inbox_item_ids"].add(inbox_id)

    if tool_name in _TASK_SCOPE_TOOLS:
        targets["task_ids"].update(_existing_task_ids_from_payload(state, payload))
    if tool_name in _BLOCKER_SCOPE_TOOLS:
        blocker_id = _optional_text(payload.get("blocker_id"))
        if blocker_id is not None:
            targets["blocker_ids"].add(blocker_id)
    if tool_name in _REVIEW_SCOPE_TOOLS:
        targets["review_request_ids"].update(
            _existing_review_ids_from_payload(state, payload)
        )
    if tool_name == "team_report_blocker":
        _add_blocker_report_dispatch_scope_targets(
            state,
            payload=payload,
            targets=targets,
        )
    if tool_name in _PROJECT_SCOPE_TOOLS:
        targets["project_ids"].update(
            _project_ids_from_payload_relationships(state, payload)
        )
    targets["managed_repo_ids"].update(
        _values_for_keys(payload, {"managed_repo_id", "managed_repo_ids"})
    )
    return targets


def _validate_cancel_task_dispatch_scope(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    payload: Mapping[str, Any],
) -> None:
    """Authorize cancellation from owned work, operator chat, or an intervention.

    A parent owner, project lead, or Team manager does not own the target task,
    so the task can never appear in that actor's ordinary ``task_ids``. Their
    domain authority is usable when authenticated operator chat froze the
    target project into its role-bounded scope, or when another dispatch
    delivered a task-linked inbox item or blocker.
    """

    task_ids = _existing_task_ids_from_payload(state, payload)
    if len(task_ids) != 1:
        return
    task_id = next(iter(task_ids))
    task = state.tasks.get(task_id)
    if task is None:
        return
    if task_id in dispatch.task_ids:
        _validate_dispatch_task_scope(
            state,
            dispatch=dispatch,
            task_ids={task_id},
        )
        return
    if _is_role_bounded_operator_chat_dispatch(dispatch):
        _validate_dispatch_project_scope(
            state,
            dispatch=dispatch,
            project_ids={task.project_id},
        )
        return

    explicitly_handled_ids = _text_sequence(
        payload.get("handled_inbox_item_ids")
    )
    matching_inbox_ids = {
        item.id
        for item in state.inbox.values()
        if item.id in dispatch.inbox_item_ids
        and item.id in explicitly_handled_ids
        and item.recipient_agent_id == dispatch.agent_id
        and item.state == TeamInboxItemState.PENDING
        and (
            item.task_id == task_id
            or _optional_text(item.payload.get("task_id")) == task_id
        )
    }
    if matching_inbox_ids:
        _validate_dispatch_inbox_scope(
            state,
            dispatch=dispatch,
            inbox_item_ids=matching_inbox_ids,
        )
        return

    matching_blocker_ids = {
        blocker.id
        for blocker in state.blockers.values()
        if blocker.id in dispatch.blocker_ids
        and (
            blocker.task_id == task_id
            or task_id in blocker.affected_task_ids
        )
    }
    if matching_blocker_ids:
        _validate_dispatch_blocker_scope(
            state,
            dispatch=dispatch,
            blocker_ids=matching_blocker_ids,
        )
        return

    raise _dispatch_scope_error(
        dispatch,
        scope_kind="task or delivered task intervention",
        requested_ids=[task_id],
        allowed_ids=[
            *dispatch.task_ids,
            *dispatch.inbox_item_ids,
            *dispatch.blocker_ids,
        ],
    )


def _validate_deliverable_replacement_dispatch_scope(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    payload: Mapping[str, Any],
) -> None:
    """Authorize manager lineage work from project or delivered intervention scope.

    The accepted replacement is often already terminal and belongs to another
    owner, so it cannot be validated as actionable task work for the caller.
    """

    predecessor_id = _optional_text(payload.get("predecessor_deliverable_id"))
    replacement_id = _optional_text(payload.get("replacement_deliverable_id"))
    predecessor = state.deliverables.get(predecessor_id or "")
    replacement = state.deliverables.get(replacement_id or "")
    predecessor_task = (
        state.tasks.get(predecessor.task_id) if predecessor is not None else None
    )
    replacement_task = (
        state.tasks.get(replacement.task_id) if replacement is not None else None
    )
    if (
        predecessor is None
        or replacement is None
        or predecessor_task is None
        or replacement_task is None
        or predecessor_task.project_id != replacement_task.project_id
    ):
        # The domain command reports missing or cross-project records with its
        # usual validation error; scope extraction must not mask that error.
        return

    if predecessor_task.project_id in dispatch.project_ids:
        _validate_dispatch_project_scope(
            state,
            dispatch=dispatch,
            project_ids={predecessor_task.project_id},
        )
        return

    deliverable_ids = {predecessor.id, replacement.id}
    task_ids = {predecessor_task.id, replacement_task.id}
    matching_inbox_ids = {
        item.id
        for item in state.inbox.values()
        if item.id in dispatch.inbox_item_ids
        and item.recipient_agent_id == dispatch.agent_id
        and item.state == TeamInboxItemState.PENDING
        and (
            item.task_id in task_ids
            or _optional_text(item.payload.get("deliverable_id")) in deliverable_ids
            or _optional_text(item.payload.get("predecessor_deliverable_id"))
            in deliverable_ids
            or _optional_text(item.payload.get("replacement_deliverable_id"))
            in deliverable_ids
        )
    }
    if matching_inbox_ids:
        _validate_dispatch_inbox_scope(
            state,
            dispatch=dispatch,
            inbox_item_ids=matching_inbox_ids,
        )
        return

    matching_blocker_ids = {
        blocker.id
        for blocker in state.blockers.values()
        if blocker.id in dispatch.blocker_ids
        and (
            blocker.source_id in deliverable_ids
            or blocker.task_id in task_ids
            or bool(set(blocker.affected_task_ids).intersection(task_ids))
        )
    }
    if matching_blocker_ids:
        _validate_dispatch_blocker_scope(
            state,
            dispatch=dispatch,
            blocker_ids=matching_blocker_ids,
        )
        return

    raise _dispatch_scope_error(
        dispatch,
        scope_kind="project or delivered deliverable intervention",
        requested_ids=[predecessor.id, replacement.id],
        allowed_ids=[
            *dispatch.project_ids,
            *dispatch.inbox_item_ids,
            *dispatch.blocker_ids,
        ],
    )


def _validate_request_review_dispatch_scope(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    payload: Mapping[str, Any],
) -> set[str]:
    deliverable_ids = sorted(_values_for_keys(payload, {"deliverable_id"}))
    deliverable_id = deliverable_ids[0] if len(deliverable_ids) == 1 else None
    deliverable = state.deliverables.get(deliverable_id or "")
    task = (
        state.tasks.get(deliverable.task_id)
        if deliverable is not None
        else None
    )
    if deliverable is None or task is None:
        return set()
    if _is_role_bounded_operator_chat_dispatch(dispatch):
        _validate_dispatch_project_scope(
            state,
            dispatch=dispatch,
            project_ids={task.project_id},
        )
        return {task.project_id}

    matching_notice_ids = {
        item.id
        for item in state.inbox.values()
        if item.id in dispatch.inbox_item_ids
        and item.recipient_agent_id == dispatch.agent_id
        and item.state == TeamInboxItemState.PENDING
        and item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE
        and item.task_id == task.id
        and item.payload.get("deliverable_id") == deliverable.id
    }
    if matching_notice_ids:
        _validate_dispatch_inbox_scope(
            state,
            dispatch=dispatch,
            inbox_item_ids=matching_notice_ids,
        )
        return set()

    if task.id in dispatch.task_ids:
        if task.owner_agent_id != dispatch.agent_id:
            raise TeamCommandValidationError(
                code="dispatch_scope_stale",
                message=(
                    f"scoped task {task.id} is now assigned to "
                    f"{task.owner_agent_id}, not {dispatch.agent_id}"
                ),
                correction_context={
                    "dispatch_id": dispatch.id,
                    "task_id": task.id,
                    "owner_agent_id": task.owner_agent_id,
                },
            )
        # Submission legitimately moves the task out of actionable readiness
        # before its producer can route the resulting deliverable to a reviewer.
        # Immutable task membership plus current ownership remains the authority.
        return set()

    reviewer_ids = sorted(_values_for_keys(payload, {"reviewer_agent_id"}))
    reviewer_agent_id = reviewer_ids[0] if len(reviewer_ids) == 1 else None
    if dispatch.cause_kind == "manager_fallback" and reviewer_agent_id is not None:
        for authority in dispatch.review_routing_authorities:
            if (
                authority.reviewer_agent_id == reviewer_agent_id
                and authority.deliverable_id == deliverable.id
                and authority.task_id == task.id
                and authority.project_id == task.project_id
            ):
                authority_error = (
                    _manager_fallback_review_routing_authority_error(
                        state,
                        dispatch_agent_id=dispatch.agent_id,
                        authority=authority,
                    )
                )
                if authority_error is not None:
                    raise TeamCommandValidationError(
                        code="dispatch_scope_stale",
                        message=(
                            "manager-fallback review routing authority is no "
                            f"longer actionable: {authority_error}"
                        ),
                        correction_context={
                            "dispatch_id": dispatch.id,
                            "reviewer_agent_id": reviewer_agent_id,
                            "deliverable_id": deliverable.id,
                        },
                    )
                return {task.project_id}

    raise _dispatch_scope_error(
        dispatch,
        scope_kind=(
            "task, deliverable handoff inbox item, or manager fallback "
            "review routing authority"
        ),
        requested_ids=[task.id, deliverable.id],
        allowed_ids=[
            *dispatch.task_ids,
            *dispatch.inbox_item_ids,
            *[
                (
                    f"{authority.reviewer_agent_id}:"
                    f"{authority.deliverable_id}"
                )
                for authority in dispatch.review_routing_authorities
            ],
        ],
    )


def _validate_report_blocker_dispatch_scope(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    payload: Mapping[str, Any],
) -> None:
    """Authorize deliverable reports from their exact task or handoff scope."""

    source_kind = _optional_text(payload.get("source_kind"))
    if source_kind != TeamBlockerSourceKind.DELIVERABLE.value:
        return
    source_id = _optional_text(payload.get("source_id"))
    deliverable = state.deliverables.get(source_id or "")
    task = (
        state.tasks.get(deliverable.task_id)
        if deliverable is not None
        else None
    )
    if deliverable is None or task is None:
        # Source validation returns the stable missing-resource error.
        return
    if _is_role_bounded_operator_chat_dispatch(dispatch):
        _validate_dispatch_project_scope(
            state,
            dispatch=dispatch,
            project_ids={task.project_id},
        )
        return

    matching_notice_ids = {
        item.id
        for item in state.inbox.values()
        if item.id in dispatch.inbox_item_ids
        and item.recipient_agent_id == dispatch.agent_id
        and item.state == TeamInboxItemState.PENDING
        and item.item_type == TeamInboxItemType.DELIVERABLE_NOTICE
        and item.task_id == task.id
        and item.payload.get("deliverable_id") == deliverable.id
    }
    if matching_notice_ids:
        _validate_dispatch_inbox_scope(
            state,
            dispatch=dispatch,
            inbox_item_ids=matching_notice_ids,
        )
        return

    # Submission can move the producer's task out of actionable readiness
    # before the same dispatch needs to report a blocker on that handoff.
    _validate_dispatch_task_scope(
        state,
        dispatch=dispatch,
        task_ids={task.id},
        require_actionable=False,
    )


def _add_blocker_report_dispatch_scope_targets(
    state: TeamState,
    *,
    payload: Mapping[str, Any],
    targets: dict[str, set[str]],
) -> None:
    """Bind blocker reports to the immutable source scope of the dispatch."""

    source_kind = _optional_text(payload.get("source_kind"))
    source_id = _optional_text(payload.get("source_id"))
    if source_kind == TeamBlockerSourceKind.TASK.value and source_id in state.tasks:
        targets["task_ids"].add(source_id)
        return
    if source_kind == TeamBlockerSourceKind.DELIVERABLE.value:
        deliverable = state.deliverables.get(source_id or "")
        task = (
            state.tasks.get(deliverable.task_id)
            if deliverable is not None
            else None
        )
        if task is not None:
            targets["project_ids"].add(task.project_id)
        return
    if source_kind == TeamBlockerSourceKind.REVIEW_REQUEST.value:
        if source_id in state.review_requests:
            targets["review_request_ids"].add(source_id)
        return
    if source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE.value:
        release = state.release_packages.get(source_id or "")
        if release is not None:
            targets["project_ids"].add(release.project_id)
        return
    # Explicit reports may optionally be attached to a task. Other source
    # kinds are release- or project-level and remain project scoped.
    if source_kind == TeamBlockerSourceKind.EXPLICIT.value:
        targets["task_ids"].update(_existing_task_ids_from_payload(state, payload))


def _existing_task_ids_from_payload(
    state: TeamState,
    payload: Mapping[str, Any],
) -> set[str]:
    task_ids = {
        value
        for value in _values_for_keys(
            payload,
            {
                "task_id",
                "task_ids",
                "parent_task_id",
                "affected_task_ids",
            },
        )
        if value in state.tasks
    }
    for deliverable_id in _values_for_keys(
        payload,
        {
            "deliverable_id",
            "deliverable_ids",
            "replacement_deliverable_id",
            "supersedes_deliverable_id",
        },
    ):
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is not None:
            task_ids.add(deliverable.task_id)
    for assignment_id in _values_for_keys(payload, {"assignment_id", "assignment_ids"}):
        assignment = state.ephemeral_assignments.get(assignment_id)
        if assignment is not None:
            task_ids.add(assignment.task_id)
    for context_id in _values_for_keys(
        payload,
        {"branch_context_id", "branch_context_ids"},
    ):
        context = state.git_work_contexts.get(context_id)
        if context is not None:
            task_ids.add(context.task_id)
    return task_ids


def _existing_review_ids_from_payload(
    state: TeamState,
    payload: Mapping[str, Any],
) -> set[str]:
    return {
        review_id
        for review_id in _values_for_keys(
            payload,
            {"review_request_id", "review_request_ids"},
        )
        if review_id in state.review_requests
    }


def _project_ids_from_payload_relationships(
    state: TeamState,
    payload: Mapping[str, Any],
) -> set[str]:
    project_ids = {
        project_id
        for project_id in _values_for_keys(payload, {"project_id", "project_ids"})
        if project_id in state.projects
    }
    for task_id in _values_for_keys(
        payload,
        {
            "task_id",
            "task_ids",
            "parent_task_id",
            "originating_task_id",
            "affected_task_ids",
        },
    ):
        task = state.tasks.get(task_id)
        if task is not None:
            project_ids.add(task.project_id)
    for deliverable_id in _values_for_keys(
        payload,
        {
            "deliverable_id",
            "deliverable_ids",
            "replacement_deliverable_id",
            "supersedes_deliverable_id",
            "source_deliverable_ids",
        },
    ):
        deliverable = state.deliverables.get(deliverable_id)
        task = (
            state.tasks.get(deliverable.task_id)
            if deliverable is not None
            else None
        )
        if task is not None:
            project_ids.add(task.project_id)
    for release_id in _values_for_keys(
        payload,
        {
            "release_package_id",
            "release_package_ids",
            "supersedes_release_package_id",
        },
    ):
        release = state.release_packages.get(release_id)
        if release is not None:
            project_ids.add(release.project_id)
    for review_id in _values_for_keys(
        payload,
        {"review_request_id", "review_request_ids"},
    ):
        review = state.review_requests.get(review_id)
        if review is None:
            continue
        if review.deliverable_id is not None:
            deliverable = state.deliverables.get(review.deliverable_id)
            task = (
                state.tasks.get(deliverable.task_id)
                if deliverable is not None
                else None
            )
            if task is not None:
                project_ids.add(task.project_id)
        if review.release_package_id is not None:
            release = state.release_packages.get(review.release_package_id)
            if release is not None:
                project_ids.add(release.project_id)
    for blocker_id in _values_for_keys(payload, {"blocker_id", "blocker_ids"}):
        blocker = state.blockers.get(blocker_id)
        if blocker is None:
            continue
        if blocker.project_id is not None:
            project_ids.add(blocker.project_id)
        if blocker.task_id is not None and blocker.task_id in state.tasks:
            project_ids.add(state.tasks[blocker.task_id].project_id)
    for case_id in _values_for_keys(payload, {"case_id", "case_ids"}):
        case = state.cases.get(case_id)
        if case is not None and case.project_id is not None:
            project_ids.add(case.project_id)
    for finding_id in _values_for_keys(
        payload,
        {"finding_id", "human_review_finding_id"},
    ):
        finding = state.human_review_findings.get(finding_id)
        if finding is None:
            continue
        request = state.human_review_requests.get(
            finding.source_human_review_request_id
        )
        release = (
            state.release_packages.get(request.release_package_id)
            if request is not None
            else None
        )
        if release is not None:
            project_ids.add(release.project_id)
    for assignment_id in _values_for_keys(payload, {"assignment_id", "assignment_ids"}):
        assignment = state.ephemeral_assignments.get(assignment_id)
        if assignment is not None:
            project_ids.add(assignment.project_id)
    for memory_id in _values_for_keys(
        payload,
        {
            "memory_id",
            "memory_ids",
            "supersedes_memory_id",
            "superseded_by_memory_id",
        },
    ):
        memory = state.memories.get(memory_id)
        if memory is not None and memory.project_id is not None:
            project_ids.add(memory.project_id)
    for definition_id in _values_for_keys(
        payload,
        {"definition_id", "definition_ids"},
    ):
        definition = state.outcome_kind_definitions.get(definition_id)
        if definition is not None and definition.project_id is not None:
            project_ids.add(definition.project_id)
    return project_ids


def _values_for_keys(value: object, keys: set[str]) -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in keys:
                if isinstance(item, Sequence) and not isinstance(
                    item,
                    (str, bytes, bytearray),
                ):
                    values.update(_text_sequence(item))
                else:
                    text = _optional_text(item)
                    if text is not None:
                        values.add(text)
            values.update(_values_for_keys(item, keys))
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for item in value:
            values.update(_values_for_keys(item, keys))
    return values


def _text_sequence(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return set()
    return {
        text
        for item in value
        if (text := _optional_text(item)) is not None
    }


def _validate_dispatch_task_scope(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    task_ids: set[str],
    require_actionable: bool = True,
) -> None:
    role_bounded_chat_task_ids = {
        task_id
        for task_id in task_ids
        if (
            _is_role_bounded_operator_chat_dispatch(dispatch)
            and (task := state.tasks.get(task_id)) is not None
            and task.project_id in dispatch.project_ids
        )
    }
    missing = sorted(
        task_ids - set(dispatch.task_ids) - role_bounded_chat_task_ids
    )
    if missing:
        raise _dispatch_scope_error(
            dispatch,
            scope_kind="task",
            requested_ids=missing,
            allowed_ids=dispatch.task_ids,
        )
    assessments = TaskAssessmentService(state)
    for task_id in sorted(task_ids):
        task = state.tasks.get(task_id)
        if task is None:
            raise TeamCommandValidationError(
                code="dispatch_scope_stale",
                message=f"scoped task {task_id} no longer exists",
            )
        if task_id in role_bounded_chat_task_ids:
            # Operator chat freezes the actor's role-visible projects rather
            # than attempting to predict the exact object IDs named in prose.
            # The command's domain policy validates current ownership,
            # supervision, role permission, and lifecycle state.
            continue
        if task.owner_agent_id != dispatch.agent_id:
            raise TeamCommandValidationError(
                code="dispatch_scope_stale",
                message=(
                    f"scoped task {task.id} is now assigned to "
                    f"{task.owner_agent_id}, not {dispatch.agent_id}"
                ),
                correction_context={
                    "dispatch_id": dispatch.id,
                    "task_id": task.id,
                    "owner_agent_id": task.owner_agent_id,
                },
            )
        if not require_actionable:
            # A first product may move the task into acceptance or review waiting
            # while this immutable dispatch still owns the task. The command's
            # lifecycle boundary decides whether another submission is valid.
            continue
        assessment = assessments.assess(task.id)
        if assessment.readiness != TaskAssessmentReadiness.ACTIONABLE:
            raise TeamCommandValidationError(
                code="dispatch_scope_stale",
                message=(
                    f"scoped task {task.id} is {assessment.readiness.value}, "
                    "not actionable"
                ),
                correction_context={
                    "dispatch_id": dispatch.id,
                    "task_id": task.id,
                    "readiness": assessment.readiness.value,
                },
            )


def _validate_dispatch_blocker_scope(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    blocker_ids: set[str],
) -> None:
    role_bounded_chat_blocker_ids = {
        blocker_id
        for blocker_id in blocker_ids
        if (
            _is_role_bounded_operator_chat_dispatch(dispatch)
            and (blocker := state.blockers.get(blocker_id)) is not None
            and bool(project_ids := _blocker_project_ids(state, blocker))
            and project_ids <= set(dispatch.project_ids)
        )
    }
    missing = sorted(
        blocker_ids - set(dispatch.blocker_ids) - role_bounded_chat_blocker_ids
    )
    if missing:
        raise _dispatch_scope_error(
            dispatch,
            scope_kind="blocker",
            requested_ids=missing,
            allowed_ids=dispatch.blocker_ids,
        )
    for blocker_id in sorted(blocker_ids):
        blocker = state.blockers.get(blocker_id)
        if blocker_id in role_bounded_chat_blocker_ids:
            # The blocker command enforces the current owner, delegate, and
            # escalation-line authority after project containment succeeds.
            continue
        assigned = blocker is not None and (
            (
                blocker.route == TeamBlockerRoute.DELEGATED
                and blocker.delegate_agent_id == dispatch.agent_id
            )
            or (
                blocker.route
                in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
                and blocker.owner_agent_id == dispatch.agent_id
            )
        )
        # Assigned blockers whose follow-up is not due are frozen into the
        # dispatch's exact scope but intentionally left unreserved so an
        # unrelated completion cannot count as a stalled blocker attempt.
        if (
            blocker is None
            or blocker.state != TeamBlockerState.OPEN
            or not assigned
            or blocker.active_dispatch_id not in {None, dispatch.id}
        ):
            raise TeamCommandValidationError(
                code="dispatch_scope_stale",
                message=(
                    f"scoped blocker {blocker_id} is no longer open, assigned, "
                    f"and available to dispatch {dispatch.id}"
                ),
                correction_context={
                    "dispatch_id": dispatch.id,
                    "blocker_id": blocker_id,
                },
            )


def _validate_dispatch_review_scope(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    review_request_ids: set[str],
) -> None:
    role_bounded_chat_review_ids = {
        review_id
        for review_id in review_request_ids
        if (
            _is_role_bounded_operator_chat_dispatch(dispatch)
            and (review := state.review_requests.get(review_id)) is not None
            and bool(project_ids := _review_project_ids(state, review))
            and project_ids <= set(dispatch.project_ids)
        )
    }
    missing = sorted(
        review_request_ids
        - set(dispatch.review_request_ids)
        - role_bounded_chat_review_ids
    )
    if missing:
        raise _dispatch_scope_error(
            dispatch,
            scope_kind="review",
            requested_ids=missing,
            allowed_ids=dispatch.review_request_ids,
        )
    for review_id in sorted(review_request_ids):
        review = state.review_requests.get(review_id)
        if review_id in role_bounded_chat_review_ids:
            # The review command remains responsible for reviewer identity and
            # current decision eligibility.
            continue
        if (
            review is None
            or review.reviewer_agent_id != dispatch.agent_id
            or review.state
            not in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
            or review.superseded_by_review_request_id is not None
            or review.retired_at is not None
        ):
            raise TeamCommandValidationError(
                code="dispatch_scope_stale",
                message=(
                    f"scoped review {review_id} is no longer actionable and "
                    f"assigned to {dispatch.agent_id}"
                ),
                correction_context={
                    "dispatch_id": dispatch.id,
                    "review_request_id": review_id,
                },
            )


def _validate_dispatch_inbox_scope(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    inbox_item_ids: set[str],
) -> None:
    role_bounded_chat_inbox_ids = {
        inbox_id
        for inbox_id in inbox_item_ids
        if (
            _is_role_bounded_operator_chat_dispatch(dispatch)
            and (item := state.inbox.get(inbox_id)) is not None
            and item.recipient_agent_id == dispatch.agent_id
            and item.state == TeamInboxItemState.PENDING
            and (
                not (project_ids := _inbox_project_ids(state, item))
                or project_ids <= set(dispatch.project_ids)
            )
        )
    }
    missing = sorted(
        inbox_item_ids - set(dispatch.inbox_item_ids) - role_bounded_chat_inbox_ids
    )
    if missing:
        raise _dispatch_scope_error(
            dispatch,
            scope_kind="inbox item",
            requested_ids=missing,
            allowed_ids=dispatch.inbox_item_ids,
        )
    for inbox_id in sorted(inbox_item_ids):
        item = state.inbox.get(inbox_id)
        if inbox_id in role_bounded_chat_inbox_ids:
            continue
        if (
            item is None
            or item.recipient_agent_id != dispatch.agent_id
            or item.state != TeamInboxItemState.PENDING
        ):
            raise TeamCommandValidationError(
                code="dispatch_scope_stale",
                message=(
                    f"scoped inbox item {inbox_id} is no longer pending and "
                    f"assigned to {dispatch.agent_id}"
                ),
                correction_context={
                    "dispatch_id": dispatch.id,
                    "inbox_item_id": inbox_id,
                },
            )


def _validate_dispatch_project_scope(
    state: TeamState,
    *,
    dispatch: TeamDispatch,
    project_ids: set[str],
) -> None:
    missing = sorted(project_ids - set(dispatch.project_ids))
    if missing:
        raise _dispatch_scope_error(
            dispatch,
            scope_kind="project",
            requested_ids=missing,
            allowed_ids=dispatch.project_ids,
        )
    missing_current = sorted(project_ids - set(state.projects))
    if missing_current:
        raise TeamCommandValidationError(
            code="dispatch_scope_stale",
            message=f"scoped projects no longer exist: {', '.join(missing_current)}",
            correction_context={
                "dispatch_id": dispatch.id,
                "project_ids": missing_current,
            },
        )


def _is_role_bounded_operator_chat_dispatch(dispatch: TeamDispatch) -> bool:
    return dispatch.cause_kind == "team_chat" and bool(dispatch.project_ids)


def _validate_dispatch_managed_repo_scope(
    *,
    dispatch: TeamDispatch,
    managed_repo_ids: set[str],
) -> None:
    missing = sorted(managed_repo_ids - set(dispatch.managed_repo_ids))
    if missing:
        raise _dispatch_scope_error(
            dispatch,
            scope_kind="managed repository",
            requested_ids=missing,
            allowed_ids=dispatch.managed_repo_ids,
        )


def _dispatch_scope_error(
    dispatch: TeamDispatch,
    *,
    scope_kind: str,
    requested_ids: Sequence[str],
    allowed_ids: Sequence[str],
) -> TeamCommandValidationError:
    return TeamCommandValidationError(
        code="dispatch_scope_forbidden",
        message=(
            f"dispatch {dispatch.id} does not authorize {scope_kind} "
            f"{', '.join(requested_ids)}"
        ),
        correction_context={
            "dispatch_id": dispatch.id,
            "scope_kind": scope_kind,
            "requested_ids": list(requested_ids),
            "allowed_ids": list(allowed_ids),
        },
    )


__all__ = ["TeamCommandExecutor"]
