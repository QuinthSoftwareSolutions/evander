from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from evander.models import utc_now

from ...models import (
    AgentStatus,
    TeamBlocker,
    TeamBlockerEventType,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
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
from ..domain.common import DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
from ..domain.preparation_failures import (
    create_rejected_candidate_correction_preparation_recovery_inbox,
)
from ..domain.dependency_composition_recovery import (
    start_dependency_composition_recovery,
)
from ...models import DependencyCompositionRecoveryState
from ..domain.stand_down import _active_stand_downs_for_agent
from ..domain.wake import _append_agent_wake_requested_event
from ..domain.work_due import _mark_agent_work_due
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


@dataclass
class _BlockerUpdateApplication:
    """Apply one blocker action inside the command executor's transaction."""

    state: TeamState
    resolved: _ResolvedTeamCommandContext
    blocker_id: str
    action: str
    note: str
    delegate_agent_id: str | None
    follow_up_seconds: int
    resolution_refs: list[str]

    def apply(self) -> TeamCommandApplyResult:
        self.actor = _require_command_agent(self.resolved, "team_update_blocker")
        self.blocker = self.state.blockers.get(self.blocker_id)
        if self.blocker is None:
            raise TeamCommandValidationError(
                code="blocker_not_found",
                message=f"blocker {self.blocker_id} does not exist",
            )
        if self.blocker.state == TeamBlockerState.RESOLVED and self.action == "resolve":
            return self._already_resolved_result()
        self._validate_open_action_authority()
        self._prepare_action()
        if self.action == "progress":
            self._record_progress()
        elif self.action == "delegate":
            self._delegate()
        elif self.action == "delegate_result":
            self._record_delegate_result()
        elif self.action == "resolve":
            self._resolve()
        else:
            self._escalate()
        return self._command_result()

    def _already_resolved_result(self) -> TeamCommandApplyResult:
        eligible_agent_ids = blocker_resolution_line_agent_ids(
            self.state,
            blocker=self.blocker,
        )
        if self.actor.id not in eligible_agent_ids:
            raise TeamCommandValidationError(
                code="blocker_resolution_line_required",
                message=(
                    f"agent {self.actor.id} is not in blocker {self.blocker.id}'s resolution line"
                ),
            )
        existing_refs = set(self.blocker.terminal_reference_ids)
        missing_refs = sorted(set(self.resolution_refs) - existing_refs)
        if missing_refs:
            raise TeamCommandValidationError(
                code="blocker_resolution_conflict",
                message=(
                    f"blocker {self.blocker.id} is already resolved without "
                    f"references {', '.join(missing_refs)}"
                ),
                correction_context={
                    "blocker_id": self.blocker.id,
                    "terminal_reference_ids": sorted(existing_refs),
                    "attempted_resolution_refs": self.resolution_refs,
                },
            )
        return TeamCommandApplyResult(
            response_payload={
                "blocker_id": self.blocker.id,
                "action": self.action,
                "owner_agent_id": self.blocker.owner_agent_id,
                "delegate_agent_id": self.blocker.delegate_agent_id,
                "state": self.blocker.state.value,
                "route": self.blocker.route.value,
                "handled_blocker_inbox_item_ids": [],
                "blocker_event_ids": [],
                "async_effect_pending": False,
                "progress_made": False,
            },
            progress_classification="none",
            applied=False,
            state_changed=False,
        )

    def _validate_open_action_authority(self) -> None:
        if self.blocker.state != TeamBlockerState.OPEN:
            raise TeamCommandValidationError(
                code="blocker_not_open",
                message=f"blocker {self.blocker.id} is {self.blocker.state.value}",
            )
        self.is_owner = self.blocker.owner_agent_id == self.actor.id
        self.is_delegate = (
            self.blocker.route == TeamBlockerRoute.DELEGATED
            and self.blocker.delegate_agent_id == self.actor.id
        )
        if self.action in {"delegate", "escalate"} and not self.is_owner:
            raise TeamCommandValidationError(
                code="blocker_owner_required",
                message=f"only blocker owner {self.blocker.owner_agent_id} may {self.action}",
            )
        if self.action == "resolve" and not agent_can_resolve_blocker(
            self.state,
            blocker=self.blocker,
            agent_id=self.actor.id,
        ):
            eligible_agent_ids = blocker_resolution_line_agent_ids(
                self.state,
                blocker=self.blocker,
            )
            raise TeamCommandValidationError(
                code="blocker_resolution_line_required",
                message=(
                    f"agent {self.actor.id} is not an active member of blocker "
                    f"{self.blocker.id}'s escalation line; line agents: "
                    f"{', '.join(eligible_agent_ids) or 'none'}"
                ),
            )
        if self.action == "delegate_result" and not self.is_delegate:
            raise TeamCommandValidationError(
                code="blocker_delegate_required",
                message=(
                    f"only active delegate {self.blocker.delegate_agent_id} may report "
                    "a delegated result"
                ),
            )
        if self.action == "progress" and not (self.is_owner or self.is_delegate):
            raise TeamCommandValidationError(
                code="blocker_participant_required",
                message="only the blocker owner or active delegate may record progress",
            )

    def _prepare_action(self) -> None:
        self.now = _ensure_utc(utc_now())
        # A recurring "still blocked" note changes bookkeeping, but it does
        # not make the blocked work advance.  Only a progress update carrying
        # new evidence can reset the dispatch no-progress counter; route and
        # resolution actions are material by construction.
        self.progress_made = self.action != "progress" or bool(self.resolution_refs)
        self.result: dict[str, Any] = {
            "blocker_id": self.blocker.id,
            "created_inbox_item_ids": [],
            "events": [],
            "errors": [],
            "progress_made": self.progress_made,
        }
        self.handled_ids = handle_blocker_inbox_for_agent(
            self.state,
            blocker_id=self.blocker.id,
            agent_id=self.actor.id,
            now=self.now,
        )
        self.event_ids: list[str] = []

    def _record_progress(self) -> None:
        self.blocker.last_progress_at = self.now
        self.blocker.follow_up_at = self.now + timedelta(seconds=self.follow_up_seconds)
        self.blocker.updated_at = self.now
        event = append_blocker_event(
            self.state,
            blocker=self.blocker,
            event_type=TeamBlockerEventType.PROGRESS,
            actor_agent_id=self.actor.id,
            dispatch_id=self.resolved.dispatch_id,
            note=self.note,
            reference_ids=self.resolution_refs,
            metadata={"follow_up_seconds": self.follow_up_seconds},
            now=self.now,
        )
        self.event_ids.append(event.id)
        if self.is_delegate and self.blocker.owner_agent_id is not None:
            create_blocker_inbox(
                self.state,
                blocker=self.blocker,
                recipient_agent_id=self.blocker.owner_agent_id,
                sender_agent_id=self.actor.id,
                kind="blocker_delegate_progress",
                result=self.result,
                now=self.now,
            )

    def _delegate(self) -> None:
        if self.delegate_agent_id is None:
            raise TeamCommandValidationError(
                code="blocker_delegate_missing",
                message="delegate action requires delegate_agent_id",
            )
        delegate = self.state.agents.get(self.delegate_agent_id)
        if delegate is None or delegate.reports_to_agent_id != self.actor.id:
            raise TeamCommandValidationError(
                code="blocker_delegate_not_direct_subordinate",
                message=(
                    f"agent {self.delegate_agent_id} is not a direct subordinate "
                    f"of {self.actor.id}"
                ),
            )
        if (
            delegate.team_id != self.blocker.team_id
            or delegate.status != AgentStatus.ACTIVE
            or _active_stand_downs_for_agent(self.state, delegate.id)
        ):
            raise TeamCommandValidationError(
                code="blocker_delegate_unavailable",
                message=f"agent {delegate.id} is not available for blocker delegation",
            )
        if self.blocker.delegate_agent_id is not None:
            handle_blocker_inbox_for_agent(
                self.state,
                blocker_id=self.blocker.id,
                agent_id=self.blocker.delegate_agent_id,
                now=self.now,
            )
        self.blocker.delegate_agent_id = delegate.id
        self.blocker.route = TeamBlockerRoute.DELEGATED
        self.blocker.follow_up_at = self.now + timedelta(seconds=self.follow_up_seconds)
        self.blocker.active_dispatch_id = None
        self.blocker.updated_at = self.now
        event = append_blocker_event(
            self.state,
            blocker=self.blocker,
            event_type=TeamBlockerEventType.DELEGATED,
            actor_agent_id=self.actor.id,
            from_agent_id=self.actor.id,
            to_agent_id=delegate.id,
            dispatch_id=self.resolved.dispatch_id,
            note=self.note,
            reference_ids=self.resolution_refs,
            metadata={"follow_up_seconds": self.follow_up_seconds},
            now=self.now,
        )
        self.event_ids.append(event.id)
        create_blocker_inbox(
            self.state,
            blocker=self.blocker,
            recipient_agent_id=delegate.id,
            sender_agent_id=self.actor.id,
            kind="blocker_delegated",
            result=self.result,
            now=self.now,
        )

    def _record_delegate_result(self) -> None:
        owner_id = self.blocker.owner_agent_id
        self.blocker.delegate_agent_id = None
        self.blocker.route = TeamBlockerRoute.OWNED
        self.blocker.follow_up_at = None
        self.blocker.active_dispatch_id = None
        self.blocker.last_progress_at = self.now
        self.blocker.updated_at = self.now
        event = append_blocker_event(
            self.state,
            blocker=self.blocker,
            event_type=TeamBlockerEventType.DELEGATE_RESULT,
            actor_agent_id=self.actor.id,
            from_agent_id=self.actor.id,
            to_agent_id=owner_id,
            dispatch_id=self.resolved.dispatch_id,
            note=self.note,
            reference_ids=self.resolution_refs,
            now=self.now,
        )
        self.event_ids.append(event.id)
        if owner_id is not None:
            create_blocker_inbox(
                self.state,
                blocker=self.blocker,
                recipient_agent_id=owner_id,
                sender_agent_id=self.actor.id,
                kind="blocker_delegate_result",
                result=self.result,
                now=self.now,
            )

    def _resolve(self) -> None:
        if (
            self.blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE
            and self.blocker.reason_code.startswith("deliverable_acceptance_deferred:")
        ):
            self._retry_deferred_acceptance()
        elif self.blocker.source_kind == TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST:
            self._resume_human_review()
        else:
            self._resolve_source()

    def _retry_deferred_acceptance(self) -> None:
        from ..domain.review import retry_deferred_deliverable_acceptance
        from ..domain.task_assessment import TaskAssessmentBoundaryError

        before_event_ids = set(self.state.blocker_events)
        try:
            retry_deferred_deliverable_acceptance(
                self.state,
                blocker_id=self.blocker.id,
                actor_agent_id=self.actor.id,
                result=self.result,
                now=self.now,
            )
        except TaskAssessmentBoundaryError as exc:
            raise TeamCommandValidationError(
                code=exc.code,
                message=exc.message,
                correction_context=exc.correction_context,
            ) from None
        self.event_ids.extend(
            event_id
            for event_id in self.state.blocker_events
            if event_id not in before_event_ids
        )

    def _resume_human_review(self) -> None:
        from ..domain.human_reviews import resume_human_review

        before_event_ids = set(self.state.blocker_events)
        try:
            resume_human_review(
                self.state,
                human_review_request_id=self.blocker.source_id,
                result=self.result,
                now=self.now,
            )
        except ValueError as exc:
            raise TeamCommandValidationError(
                code="human_review_recovery_required",
                message=str(exc),
                correction_context={
                    "blocker_id": self.blocker.id,
                    "human_review_request_id": self.blocker.source_id,
                    "reason_code": self.blocker.reason_code,
                },
            ) from None
        self.event_ids.extend(
            event_id
            for event_id in self.state.blocker_events
            if event_id not in before_event_ids
        )

    def _resolve_source(self) -> None:
        restored_task_owner_id = _prepare_blocker_source_for_resolution(
            self.state,
            blocker=self.blocker,
            now=self.now,
        )
        event = resolve_blocker(
            self.state,
            blocker=self.blocker,
            actor_agent_id=self.actor.id,
            dispatch_id=self.resolved.dispatch_id,
            note=self.note,
            reference_ids=self.resolution_refs,
            now=self.now,
        )
        self.event_ids.append(event.id)
        deliverable_acceptance_route_handled = False
        if self.blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
            from ..domain.review import (
                retry_deliverable_acceptance_if_reviewed,
            )

            before_event_ids = set(self.state.blocker_events)
            acceptance_completed = retry_deliverable_acceptance_if_reviewed(
                self.state,
                deliverable_id=self.blocker.source_id,
                actor_agent_id=self.actor.id,
                result=self.result,
                now=self.now,
            )
            deliverable_acceptance_route_handled = bool(
                acceptance_completed
                or self.result.get("deliverable_acceptance_deferred")
            )
            self.event_ids.extend(
                event_id
                for event_id in self.state.blocker_events
                if event_id not in before_event_ids
            )
        if (
            restored_task_owner_id is not None
            and not deliverable_acceptance_route_handled
        ):
            _mark_agent_work_due(
                self.state,
                agent_id=restored_task_owner_id,
                now=self.now,
            )
            _append_agent_wake_requested_event(
                self.state,
                agent_id=restored_task_owner_id,
                result=self.result,
                reason="task_ready",
                now=self.now,
                delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
            )
        create_rejected_candidate_correction_preparation_recovery_inbox(
            self.state,
            blocker=self.blocker,
            result=self.result,
            now=self.now,
        )
        if self.blocker.source_kind == TeamBlockerSourceKind.AGENT_EXECUTION_STALL:
            runtime = self.state.agent_runtimes.get(self.blocker.source_id)
            if runtime is not None:
                runtime.no_progress_count = 0
                runtime.updated_at = self.now
            _mark_agent_work_due(
                self.state,
                agent_id=self.blocker.source_id,
                now=self.now,
            )
            _append_agent_wake_requested_event(
                self.state,
                agent_id=self.blocker.source_id,
                result=self.result,
                reason="blocker_resolved",
                now=self.now,
                delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                idempotency_suffix=self.blocker.id,
            )

    def _escalate(self) -> None:
        before_event_ids = set(self.state.blocker_events)
        transfer_blocker_up(
            self.state,
            blocker=self.blocker,
            actor_agent_id=self.actor.id,
            dispatch_id=self.resolved.dispatch_id,
            reason=self.note,
            result=self.result,
            now=self.now,
        )
        self.event_ids.extend(
            event_id
            for event_id in self.state.blocker_events
            if event_id not in before_event_ids
        )

    def _command_result(self) -> TeamCommandApplyResult:
        self.result.update(
            {
                "action": self.action,
                "owner_agent_id": self.blocker.owner_agent_id,
                "delegate_agent_id": self.blocker.delegate_agent_id,
                "state": self.blocker.state.value,
                "route": self.blocker.route.value,
                "handled_blocker_inbox_item_ids": self.handled_ids,
                "blocker_event_ids": self.event_ids,
                "async_effect_pending": bool(self.result["events"]),
            }
        )
        return TeamCommandApplyResult(
            response_payload=self.result,
            created_domain_ids=[*self.event_ids, *self.result["created_inbox_item_ids"]],
            created_event_ids=self.event_ids,
            progress_classification=("blocker_progress" if self.progress_made else "none"),
        )


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
        reason_code: str = "unspecified",
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
        normalized_reason_code = _required_text(reason_code, "reason_code")
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
            "reason_code": normalized_reason_code,
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
                reason_code=normalized_reason_code,
                priority=normalized_priority,
                reporter_agent_id=agent.id,
                team_id=agent.team_id,
                project_id=source_project_id,
                task_id=source_task_id,
                result=result,
            )
            if created:
                if normalized_source_kind == TeamBlockerSourceKind.TASK:
                    task = state.tasks.get(normalized_source_id)
                    if task is not None and task.state not in {
                        TeamTaskState.FINISHED,
                        TeamTaskState.ACCEPTED,
                        TeamTaskState.CANCELLED,
                    }:
                        task.state = TeamTaskState.BLOCKED
                        task.state_entered_at = utc_now()
                        task.updated_at = task.state_entered_at
                elif normalized_source_kind == TeamBlockerSourceKind.DELIVERABLE:
                    deliverable = state.deliverables.get(normalized_source_id)
                    if deliverable is not None and deliverable.state not in {
                        TeamRecordState.ACCEPTED,
                        TeamRecordState.REJECTED,
                    }:
                        deliverable.state = TeamRecordState.BLOCKED
                elif normalized_source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
                    review = state.review_requests.get(normalized_source_id)
                    if review is not None and review.state not in {
                        TeamRecordState.ACCEPTED,
                        TeamRecordState.REJECTED,
                    }:
                        review.state = TeamRecordState.BLOCKED
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
                created_event_ids=[
                    event.id
                    for event in state.blocker_events.values()
                    if event.blocker_id == blocker.id
                ]
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

    def start_dependency_composition_recovery_command(
        self,
        *,
        session_id: str,
        blocker_id: str,
        resolver_agent_id: str,
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_blocker_id = _required_text(blocker_id, "blocker_id")
        normalized_resolver_id = _required_text(resolver_agent_id, "resolver_agent_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [], "handled_inbox_item_ids"
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_start_dependency_composition_recovery",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="blocker_progress",
        )
        normalized_payload = {
            "blocker_id": normalized_blocker_id,
            "resolver_agent_id": normalized_resolver_id,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(resolved, "team_start_dependency_composition_recovery")
            blocker = state.blockers.get(normalized_blocker_id)
            if blocker is None:
                raise TeamCommandValidationError(
                    code="blocker_not_found",
                    message=f"blocker {normalized_blocker_id} does not exist",
                )
            if not agent_can_resolve_blocker(
                state,
                blocker=blocker,
                agent_id=actor.id,
            ):
                raise TeamCommandValidationError(
                    code="blocker_resolution_line_required",
                    message=(
                        f"agent {actor.id} is not on blocker {blocker.id}'s active escalation line"
                    ),
                )
            resolver = state.agents.get(normalized_resolver_id)
            if resolver is None:
                raise TeamCommandValidationError(
                    code="dependency_recovery_resolver_not_found",
                    message=f"resolver agent {normalized_resolver_id} does not exist",
                )
            recoveries = [
                recovery
                for recovery in state.dependency_composition_recoveries.values()
                if recovery.blocker_id == blocker.id
                and recovery.state
                in {
                    DependencyCompositionRecoveryState.AWAITING_ASSIGNMENT,
                    DependencyCompositionRecoveryState.IN_PROGRESS,
                }
            ]
            if len(recoveries) != 1:
                raise TeamCommandValidationError(
                    code="dependency_composition_recovery_unavailable",
                    message=(
                        f"blocker {blocker.id} has {len(recoveries)} actionable "
                        "dependency composition recoveries"
                    ),
                )
            now = _ensure_utc(utc_now())
            result: dict[str, Any] = {
                "success": True,
                "blocker_id": blocker.id,
                "recovery_id": recoveries[0].id,
                "created_task_ids": [],
                "created_inbox_item_ids": [],
                "blocker_event_ids": [],
                "events": [],
                "progress_made": True,
            }
            try:
                task = start_dependency_composition_recovery(
                    state,
                    recovery=recoveries[0],
                    blocker=blocker,
                    actor=actor,
                    resolver=resolver,
                    dispatch_id=resolved.dispatch_id,
                    result=result,
                    now=now,
                )
            except ValueError as exc:
                raise TeamCommandValidationError(
                    code="dependency_composition_recovery_invalid",
                    message=str(exc),
                    correction_context={
                        "blocker_id": blocker.id,
                        "recovery_id": recoveries[0].id,
                        "resolver_agent_id": resolver.id,
                    },
                ) from None
            result.update(
                {
                    "recovery_task_id": task.id,
                    "resolver_agent_id": resolver.id,
                    "state": recoveries[0].state.value,
                    "async_effect_pending": bool(result["events"]),
                }
            )
            return TeamCommandApplyResult(
                response_payload=result,
                created_domain_ids=[
                    *result["created_task_ids"],
                    *result["created_inbox_item_ids"],
                    *result["blocker_event_ids"],
                ],
                created_event_ids=list(result["blocker_event_ids"]),
                progress_classification="blocker_progress",
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
            return _BlockerUpdateApplication(
                state=state,
                resolved=resolved,
                blocker_id=normalized_blocker_id,
                action=normalized_action,
                note=normalized_note,
                delegate_agent_id=normalized_delegate_id,
                follow_up_seconds=normalized_follow_up,
                resolution_refs=normalized_refs,
            ).apply()

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


def _prepare_blocker_source_for_resolution(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    now: datetime | None = None,
) -> str | None:
    """Restore the source lifecycle instead of blindly resolving its blocker."""

    active_dependency_recoveries = [
        recovery
        for recovery in state.dependency_composition_recoveries.values()
        if recovery.blocker_id == blocker.id
        and recovery.state
        in {
            DependencyCompositionRecoveryState.AWAITING_ASSIGNMENT,
            DependencyCompositionRecoveryState.IN_PROGRESS,
        }
    ]
    if active_dependency_recoveries:
        recovery = sorted(active_dependency_recoveries, key=lambda item: item.id)[0]
        raise TeamCommandValidationError(
            code="dependency_composition_recovery_required",
            message=(
                f"blocker {blocker.id} records an unresolved deterministic Git "
                "composition conflict; start and accept its typed recovery task"
            ),
            correction_context={
                "blocker_id": blocker.id,
                "recovery_id": recovery.id,
                "recovery_task_id": recovery.recovery_task_id,
                "recommended_tool": "team_start_dependency_composition_recovery",
            },
        )

    if blocker.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR:
        raise TeamCommandValidationError(
            code="candidate_correction_recovery_command_required",
            message=(
                "candidate-correction successor blockers require the typed "
                "retry or verified-replacement recovery; "
                "generic blocker resolution cannot bypass that lifecycle"
            ),
            correction_context={
                "blocker_id": blocker.id,
                "source_id": blocker.source_id,
                "recovery_actions": [
                    "retry",
                    "create_verified_replacement",
                ],
            },
        )

    if blocker.source_kind == TeamBlockerSourceKind.TASK:
        task = state.tasks.get(blocker.source_id)
        if task is not None and task.state == TeamTaskState.BLOCKED:
            task.state = TeamTaskState.IN_PROGRESS
            task.state_entered_at = now or utc_now()
            task.updated_at = task.state_entered_at
            return task.owner_agent_id
        return None
    if blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
        deliverable = state.deliverables.get(blocker.source_id)
        if deliverable is not None and deliverable.state == TeamRecordState.BLOCKED:
            deliverable.state = TeamRecordState.PENDING
        return None
    if blocker.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST:
        review = state.review_requests.get(blocker.source_id)
        if (
            review is not None
            and review.state == TeamRecordState.BLOCKED
            and review.decision_at is not None
        ):
            if review.release_package_id is not None:
                raise TeamCommandValidationError(
                    code="release_recovery_command_required",
                    message=(
                        "a blocked formal release review must be recovered with "
                        "team_recover_release or team_retry_release_gate; generic "
                        "blocker resolution cannot rewrite its recorded decision"
                    ),
                    correction_context={
                        "review_request_id": review.id,
                        "release_package_id": review.release_package_id,
                        "recommended_tools": [
                            "team_recover_release",
                            "team_retry_release_gate",
                        ],
                    },
                )
            deliverable = state.deliverables.get(review.deliverable_id or "")
            raise TeamCommandValidationError(
                code="review_recovery_command_required",
                message=(
                    "a blocked formal review must be retried through "
                    "team_retry_review; generic blocker resolution cannot rewrite "
                    "its recorded decision"
                ),
                correction_context={
                    "review_request_id": review.id,
                    "deliverable_id": review.deliverable_id,
                    "task_id": deliverable.task_id if deliverable is not None else None,
                    "recommended_tools": ["team_retry_review"],
                },
            )
        if review is not None and review.state == TeamRecordState.BLOCKED:
            review.state = TeamRecordState.PENDING
        return None
    if blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE:
        raise TeamCommandValidationError(
            code="release_recovery_command_required",
            message=(
                "release blockers require team_recover_release or "
                "team_retry_release_gate; generic blocker resolution cannot "
                "bypass the release lifecycle"
            ),
            correction_context={
                "release_package_id": blocker.source_id,
                "recommended_tools": [
                    "team_recover_release",
                    "team_retry_release_gate",
                ],
            },
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
        project = state.projects.get(task.project_id)
        if task.owner_agent_id != actor.id and not (
            _is_project_lead_for(state, actor.id, task.project_id)
            or (
                project is not None
                and actor.team_id == project.team_id
                and _is_team_manager(state, actor)
            )
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
        project = state.projects.get(task.project_id) if task is not None else None
        if deliverable.producer_agent_id != actor.id and not (
            task is not None
            and (
                _is_project_lead_for(state, actor.id, task.project_id)
                or (
                    project is not None
                    and actor.team_id == project.team_id
                    and _is_team_manager(state, actor)
                )
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
    project = state.projects.get(release.project_id)
    if not (
        _is_project_lead_for(state, actor.id, release.project_id)
        or (
            project is not None
            and actor.team_id == project.team_id
            and _is_team_manager(state, actor)
        )
    ):
        raise TeamCommandValidationError(
            code="blocker_source_not_permitted",
            message=f"agent {actor.id} cannot report a blocker for release {source_id}",
        )
    return release.project_id, None


__all__ = ["TeamBlockerCommandService"]
