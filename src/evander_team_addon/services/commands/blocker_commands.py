from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from evander.models import utc_now

from ...models import (
    AgentStatus,
    TeamBlockerEventType,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxPriority,
    TeamState,
    _unique_text_list,
)
from ..domain.agent_access import _is_project_lead_for, _is_team_manager
from ..domain.blockers import (
    agent_can_resolve_blocker,
    append_blocker_event,
    blocker_resolution_line_agent_ids,
    create_blocker_inbox,
    handle_blocker_inbox_for_agent,
    open_or_get_blocker,
    resolve_blocker,
    transfer_blocker_up,
)
from ..domain.common import _ensure_utc, _optional_text, _required_text
from ..domain.stand_down import _active_stand_downs_for_agent
from .executor import TeamCommandExecutor
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import _require_command_agent


_BLOCKER_UPDATE_ACTIONS = {
    "progress",
    "delegate",
    "delegate_result",
    "resolve",
    "escalate",
}


class TeamBlockerCommandService(TeamCommandExecutor):
    def report_blocker_command(
        self,
        *,
        session_id: str,
        source_kind: TeamBlockerSourceKind | str,
        source_id: str,
        summary: str,
        request_id: str,
        details: str | None = None,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        project_id: str | None = None,
        task_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_source_kind = (
            source_kind
            if isinstance(source_kind, TeamBlockerSourceKind)
            else TeamBlockerSourceKind(str(source_kind))
        )
        normalized_source_id = _required_text(source_id, "source_id")
        normalized_summary = _required_text(summary, "summary")
        normalized_details = _optional_text(details) or ""
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_priority = (
            priority
            if isinstance(priority, TeamInboxPriority)
            else TeamInboxPriority(str(priority))
        )
        normalized_project_id = _optional_text(project_id)
        normalized_task_id = _optional_text(task_id)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_report_blocker",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="blocker_progress",
        )
        normalized_payload = {
            "source_kind": normalized_source_kind.value,
            "source_id": normalized_source_id,
            "summary": normalized_summary,
            "details": normalized_details,
            "priority": normalized_priority.value,
            "project_id": normalized_project_id,
            "task_id": normalized_task_id,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_report_blocker")
            source_project_id, source_task_id = _validate_report_source(
                state,
                actor_agent_id=agent.id,
                source_kind=normalized_source_kind,
                source_id=normalized_source_id,
                project_id=normalized_project_id,
                task_id=normalized_task_id,
            )
            project = state.projects.get(source_project_id or "")
            if project is not None and project.team_id != agent.team_id:
                raise TeamCommandValidationError(
                    code="blocker_outside_team",
                    message=(
                        f"source {normalized_source_kind.value} {normalized_source_id} "
                        f"belongs to team {project.team_id}, not {agent.team_id}"
                    ),
                )
            result: dict[str, Any] = {
                "created_blocker_ids": [],
                "created_inbox_item_ids": [],
                "events": [],
                "errors": [],
            }
            blocker, created = open_or_get_blocker(
                state,
                source_kind=normalized_source_kind,
                source_id=normalized_source_id,
                summary=normalized_summary,
                details=normalized_details,
                priority=normalized_priority,
                reporter_agent_id=agent.id,
                team_id=agent.team_id,
                project_id=source_project_id,
                task_id=source_task_id,
                result=result,
            )
            result.update(
                {
                    "blocker_id": blocker.id,
                    "blocker_created": created,
                    "owner_agent_id": blocker.owner_agent_id,
                    "route": blocker.route.value,
                    "progress_made": created,
                    "async_effect_pending": bool(result["events"]),
                }
            )
            return TeamCommandApplyResult(
                response_payload=result,
                created_domain_ids=(
                    [blocker.id, *result["created_inbox_item_ids"]] if created else []
                ),
                created_event_ids=[event.id for event in state.blocker_events.values() if event.blocker_id == blocker.id]
                if created
                else [],
                progress_classification="blocker_progress" if created else "none",
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def update_blocker_command(
        self,
        *,
        session_id: str,
        blocker_id: str,
        action: str,
        note: str,
        request_id: str,
        delegate_agent_id: str | None = None,
        follow_up_seconds: int | None = None,
        resolution_refs: Sequence[str] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_blocker_id = _required_text(blocker_id, "blocker_id")
        normalized_action = _required_text(action, "action").casefold()
        if normalized_action not in _BLOCKER_UPDATE_ACTIONS:
            raise ValueError(
                "action must be progress, delegate, delegate_result, resolve, or escalate"
            )
        normalized_note = _required_text(note, "note")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_delegate_id = _optional_text(delegate_agent_id)
        normalized_follow_up = 900 if follow_up_seconds is None else int(follow_up_seconds)
        if normalized_follow_up < 1 or normalized_follow_up > 86400:
            raise ValueError("follow_up_seconds must be between 1 and 86400")
        normalized_refs = _unique_text_list(resolution_refs or [], "resolution_refs")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_blocker",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="blocker_progress",
        )
        normalized_payload = {
            "blocker_id": normalized_blocker_id,
            "action": normalized_action,
            "note": normalized_note,
            "delegate_agent_id": normalized_delegate_id,
            "follow_up_seconds": normalized_follow_up,
            "resolution_refs": normalized_refs,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(resolved, "team_update_blocker")
            blocker = state.blockers.get(normalized_blocker_id)
            if blocker is None:
                raise TeamCommandValidationError(
                    code="blocker_not_found",
                    message=f"blocker {normalized_blocker_id} does not exist",
                )
            if blocker.state != TeamBlockerState.OPEN:
                raise TeamCommandValidationError(
                    code="blocker_not_open",
                    message=f"blocker {blocker.id} is {blocker.state.value}",
                )
            is_owner = blocker.owner_agent_id == actor.id
            is_delegate = (
                blocker.route == TeamBlockerRoute.DELEGATED
                and blocker.delegate_agent_id == actor.id
            )
            if normalized_action in {"delegate", "escalate"} and not is_owner:
                raise TeamCommandValidationError(
                    code="blocker_owner_required",
                    message=f"only blocker owner {blocker.owner_agent_id} may {normalized_action}",
                )
            if normalized_action == "resolve" and not agent_can_resolve_blocker(
                state,
                blocker=blocker,
                agent_id=actor.id,
            ):
                eligible_agent_ids = blocker_resolution_line_agent_ids(
                    state,
                    blocker=blocker,
                )
                raise TeamCommandValidationError(
                    code="blocker_resolution_line_required",
                    message=(
                        f"agent {actor.id} is not an active member of blocker "
                        f"{blocker.id}'s escalation line; line agents: "
                        f"{', '.join(eligible_agent_ids) or 'none'}"
                    ),
                )
            if normalized_action == "delegate_result" and not is_delegate:
                raise TeamCommandValidationError(
                    code="blocker_delegate_required",
                    message=(
                        f"only active delegate {blocker.delegate_agent_id} may report "
                        "a delegated result"
                    ),
                )
            if normalized_action == "progress" and not (is_owner or is_delegate):
                raise TeamCommandValidationError(
                    code="blocker_participant_required",
                    message="only the blocker owner or active delegate may record progress",
                )

            now = _ensure_utc(utc_now())
            result: dict[str, Any] = {
                "blocker_id": blocker.id,
                "created_inbox_item_ids": [],
                "events": [],
                "errors": [],
                "progress_made": True,
            }
            handled_ids = handle_blocker_inbox_for_agent(
                state,
                blocker_id=blocker.id,
                agent_id=actor.id,
                now=now,
            )
            event_ids: list[str] = []
            if normalized_action == "progress":
                blocker.last_progress_at = now
                blocker.follow_up_at = now + timedelta(seconds=normalized_follow_up)
                blocker.updated_at = now
                event = append_blocker_event(
                    state,
                    blocker=blocker,
                    event_type=TeamBlockerEventType.PROGRESS,
                    actor_agent_id=actor.id,
                    dispatch_id=resolved.dispatch_id,
                    note=normalized_note,
                    reference_ids=normalized_refs,
                    metadata={"follow_up_seconds": normalized_follow_up},
                    now=now,
                )
                event_ids.append(event.id)
                if is_delegate and blocker.owner_agent_id is not None:
                    create_blocker_inbox(
                        state,
                        blocker=blocker,
                        recipient_agent_id=blocker.owner_agent_id,
                        sender_agent_id=actor.id,
                        kind="blocker_delegate_progress",
                        result=result,
                        now=now,
                    )
            elif normalized_action == "delegate":
                if normalized_delegate_id is None:
                    raise TeamCommandValidationError(
                        code="blocker_delegate_missing",
                        message="delegate action requires delegate_agent_id",
                    )
                delegate = state.agents.get(normalized_delegate_id)
                if delegate is None or delegate.reports_to_agent_id != actor.id:
                    raise TeamCommandValidationError(
                        code="blocker_delegate_not_direct_subordinate",
                        message=(
                            f"agent {normalized_delegate_id} is not a direct subordinate "
                            f"of {actor.id}"
                        ),
                    )
                if (
                    delegate.team_id != blocker.team_id
                    or delegate.status != AgentStatus.ACTIVE
                    or _active_stand_downs_for_agent(state, delegate.id)
                ):
                    raise TeamCommandValidationError(
                        code="blocker_delegate_unavailable",
                        message=f"agent {delegate.id} is not available for blocker delegation",
                    )
                if blocker.delegate_agent_id is not None:
                    handle_blocker_inbox_for_agent(
                        state,
                        blocker_id=blocker.id,
                        agent_id=blocker.delegate_agent_id,
                        now=now,
                    )
                blocker.delegate_agent_id = delegate.id
                blocker.route = TeamBlockerRoute.DELEGATED
                blocker.follow_up_at = now + timedelta(seconds=normalized_follow_up)
                blocker.active_dispatch_id = None
                blocker.updated_at = now
                event = append_blocker_event(
                    state,
                    blocker=blocker,
                    event_type=TeamBlockerEventType.DELEGATED,
                    actor_agent_id=actor.id,
                    from_agent_id=actor.id,
                    to_agent_id=delegate.id,
                    dispatch_id=resolved.dispatch_id,
                    note=normalized_note,
                    reference_ids=normalized_refs,
                    metadata={"follow_up_seconds": normalized_follow_up},
                    now=now,
                )
                event_ids.append(event.id)
                create_blocker_inbox(
                    state,
                    blocker=blocker,
                    recipient_agent_id=delegate.id,
                    sender_agent_id=actor.id,
                    kind="blocker_delegated",
                    result=result,
                    now=now,
                )
            elif normalized_action == "delegate_result":
                owner_id = blocker.owner_agent_id
                blocker.delegate_agent_id = None
                blocker.route = TeamBlockerRoute.OWNED
                blocker.follow_up_at = None
                blocker.active_dispatch_id = None
                blocker.last_progress_at = now
                blocker.updated_at = now
                event = append_blocker_event(
                    state,
                    blocker=blocker,
                    event_type=TeamBlockerEventType.DELEGATE_RESULT,
                    actor_agent_id=actor.id,
                    from_agent_id=actor.id,
                    to_agent_id=owner_id,
                    dispatch_id=resolved.dispatch_id,
                    note=normalized_note,
                    reference_ids=normalized_refs,
                    now=now,
                )
                event_ids.append(event.id)
                if owner_id is not None:
                    create_blocker_inbox(
                        state,
                        blocker=blocker,
                        recipient_agent_id=owner_id,
                        sender_agent_id=actor.id,
                        kind="blocker_delegate_result",
                        result=result,
                        now=now,
                    )
            elif normalized_action == "resolve":
                event = resolve_blocker(
                    state,
                    blocker=blocker,
                    actor_agent_id=actor.id,
                    dispatch_id=resolved.dispatch_id,
                    note=normalized_note,
                    reference_ids=normalized_refs,
                    now=now,
                )
                event_ids.append(event.id)
            else:  # escalate
                before_event_ids = set(state.blocker_events)
                transfer_blocker_up(
                    state,
                    blocker=blocker,
                    actor_agent_id=actor.id,
                    dispatch_id=resolved.dispatch_id,
                    reason=normalized_note,
                    result=result,
                    now=now,
                )
                event_ids.extend(
                    event_id
                    for event_id in state.blocker_events
                    if event_id not in before_event_ids
                )

            result.update(
                {
                    "action": normalized_action,
                    "owner_agent_id": blocker.owner_agent_id,
                    "delegate_agent_id": blocker.delegate_agent_id,
                    "state": blocker.state.value,
                    "route": blocker.route.value,
                    "handled_blocker_inbox_item_ids": handled_ids,
                    "blocker_event_ids": event_ids,
                    "async_effect_pending": bool(result["events"]),
                }
            )
            return TeamCommandApplyResult(
                response_payload=result,
                created_domain_ids=[*event_ids, *result["created_inbox_item_ids"]],
                created_event_ids=event_ids,
                progress_classification="blocker_progress",
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


def _validate_report_source(
    state: TeamState,
    *,
    actor_agent_id: str,
    source_kind: TeamBlockerSourceKind,
    source_id: str,
    project_id: str | None,
    task_id: str | None,
) -> tuple[str | None, str | None]:
    actor = state.agents[actor_agent_id]
    if source_kind == TeamBlockerSourceKind.EXPLICIT:
        task = state.tasks.get(task_id or "")
        if task_id is not None and task is None:
            raise TeamCommandValidationError(
                code="task_not_found",
                message=f"task {task_id} does not exist",
            )
        effective_project_id = task.project_id if task is not None else project_id
        project = state.projects.get(effective_project_id or "")
        if effective_project_id is not None and project is None:
            raise TeamCommandValidationError(
                code="project_not_found",
                message=f"project {effective_project_id} does not exist",
            )
        if project is not None and project.team_id != actor.team_id:
            raise TeamCommandValidationError(
                code="blocker_outside_team",
                message=f"project {project.id} is outside agent {actor.id}'s team",
            )
        return effective_project_id, task.id if task is not None else None
    if source_kind == TeamBlockerSourceKind.TASK:
        task = state.tasks.get(source_id)
        if task is None:
            raise TeamCommandValidationError(
                code="task_not_found", message=f"task {source_id} does not exist"
            )
        if task.owner_agent_id != actor.id and not (
            _is_project_lead_for(state, actor.id, task.project_id)
            or _is_team_manager(state, actor)
        ):
            raise TeamCommandValidationError(
                code="blocker_source_not_permitted",
                message=f"agent {actor.id} cannot report a blocker for task {task.id}",
            )
        return task.project_id, task.id
    if source_kind == TeamBlockerSourceKind.DELIVERABLE:
        deliverable = state.deliverables.get(source_id)
        if deliverable is None:
            raise TeamCommandValidationError(
                code="deliverable_not_found",
                message=f"deliverable {source_id} does not exist",
            )
        task = state.tasks.get(deliverable.task_id)
        if deliverable.producer_agent_id != actor.id and not (
            task is not None
            and (
                _is_project_lead_for(state, actor.id, task.project_id)
                or _is_team_manager(state, actor)
            )
        ):
            raise TeamCommandValidationError(
                code="blocker_source_not_permitted",
                message=f"agent {actor.id} cannot report a blocker for deliverable {source_id}",
            )
        return task.project_id if task is not None else project_id, deliverable.task_id
    if source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
        review = state.review_requests.get(source_id)
        if review is None:
            raise TeamCommandValidationError(
                code="review_request_not_found",
                message=f"review request {source_id} does not exist",
            )
        if review.reviewer_agent_id != actor.id:
            raise TeamCommandValidationError(
                code="blocker_source_not_permitted",
                message=f"only reviewer {review.reviewer_agent_id} may report this blocker",
            )
        if review.deliverable_id is not None:
            deliverable = state.deliverables.get(review.deliverable_id)
            task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
            return task.project_id if task is not None else project_id, task.id if task else None
        release = state.release_packages.get(review.release_package_id or "")
        return release.project_id if release is not None else project_id, None
    release = state.release_packages.get(source_id)
    if release is None:
        raise TeamCommandValidationError(
            code="release_package_not_found",
            message=f"release package {source_id} does not exist",
        )
    if not (
        _is_project_lead_for(state, actor.id, release.project_id)
        or _is_team_manager(state, actor)
    ):
        raise TeamCommandValidationError(
            code="blocker_source_not_permitted",
            message=f"agent {actor.id} cannot report a blocker for release {source_id}",
        )
    return release.project_id, None


__all__ = ["TeamBlockerCommandService"]
