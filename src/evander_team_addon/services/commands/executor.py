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
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    TeamIdempotencyRecord,
    TeamInboxItemState,
    TeamState,
    _unique_text_list,
)
from ..domain.common import (
    _ensure_utc,
    _optional_text,
)
from ..domain.stand_down import (
    _active_stand_downs_for_agent,
    _stand_down_error_detail,
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
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

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
                self._validate_command_handled_inbox_items(
                    state,
                    resolved=resolved,
                    handled_inbox_item_ids=normalized_handled_ids,
                )
                apply_result = apply_command(state, resolved)
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
            for inbox_item_id in normalized_handled_ids:
                item = state.inbox[inbox_item_id]
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = now

            next_state = state.model_copy(
                update={"revision": state.revision + 1, "updated_at": now},
                deep=True,
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
                handled_inbox_item_ids=normalized_handled_ids,
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
        if agent.team_id is None:
            return resolved, TeamCommandValidationError(
                code="teamless_agent",
                message=f"agent {agent.id} is not assigned to a team",
                correction_context={"agent_id": agent.id},
            )
        active_stand_downs = _active_stand_downs_for_agent(state, agent.id)
        if active_stand_downs:
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
        if runtime.wake_leased_until is None or _ensure_utc(runtime.wake_leased_until) <= utc_now():
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
    ) -> None:
        if not handled_inbox_item_ids:
            return
        if resolved.agent is None:
            raise TeamCommandValidationError(
                code="invalid_inbox_scope",
                message="handled_inbox_item_ids requires an agent caller",
            )
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
            if item.state != TeamInboxItemState.PENDING:
                raise TeamCommandValidationError(
                    code="inbox_item_not_pending",
                    message=f"inbox item {inbox_item_id} is {item.state.value}",
                    correction_context={
                        "inbox_item_id": inbox_item_id,
                        "state": item.state.value,
                    },
                )

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


__all__ = ["TeamCommandExecutor"]
