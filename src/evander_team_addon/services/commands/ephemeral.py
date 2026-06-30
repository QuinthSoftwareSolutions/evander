from __future__ import annotations

from evander.models import utc_now
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any
from .ephemeral_helpers import (
    _normalize_ephemeral_request_mapping,
    _require_parent_assignment_owner,
    _team_accept_ephemeral_result_normalized_payload,
    _team_reject_ephemeral_result_normalized_payload,
    _team_report_ephemeral_result_normalized_payload,
    _team_request_ephemeral_normalized_payload,
    _validate_ephemeral_result_report,
)
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import (
    _require_command_agent,
    _team_command_validation_error_from_exception,
)
from ...git_naming import ephemeral_agent_branch
from ...models import (
    EphemeralAssignmentState,
    TeamOutboxRecord,
    TeamState,
    _unique_text_list,
)
from ..domain.common import (
    EPHEMERAL_WORKER_REPORT_TOOL,
    _optional_text,
    _require_present,
    _required_text,
    _string_list,
)
from ..domain.ephemeral import (
    EPHEMERAL_COMPLETED_EVENT_TYPE,
    _apply_ephemeral_acceptance,
    _create_ephemeral_assignments,
    _create_ephemeral_parent_notice,
    _ephemeral_assignment_event_payload,
    _ephemeral_request_result,
)

from .outbox import _team_event_outbox_records
from .executor import TeamCommandExecutor


class TeamEphemeralCommandService(TeamCommandExecutor):
    def request_ephemeral_agent_command(
        self,
        *,
        session_id: str,
        task_id: str,
        title: str,
        prompt: str,
        request_id: str,
        id: str | None = None,
        worker_session_id: str | None = None,
        model: str | None = None,
        agent_provider: str | None = None,
        execution_backend: str | None = None,
        container_image: str | None = None,
        allowed_tools: Sequence[str] | None = None,
        ttl_hours: int | None = None,
        directory_path: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        return self._request_ephemeral_assignments_command(
            session_id=session_id,
            request_id=request_id,
            assignments=[
                {
                    "id": id,
                    "task_id": task_id,
                    "worker_session_id": worker_session_id,
                    "title": title,
                    "prompt": prompt,
                    "model": model,
                    "agent_provider": agent_provider,
                    "execution_backend": execution_backend,
                    "container_image": container_image,
                    "allowed_tools": list(allowed_tools or []),
                    "ttl_hours": ttl_hours,
                    "directory_path": directory_path,
                }
            ],
            handled_inbox_item_ids=handled_inbox_item_ids,
            correlation_id=correlation_id,
            causation_id=causation_id,
            tool_name="team_request_ephemeral_agent",
            single=True,
        )

    def request_ephemeral_agents_command(
        self,
        *,
        session_id: str,
        assignments: Sequence[Mapping[str, Any]],
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        return self._request_ephemeral_assignments_command(
            session_id=session_id,
            request_id=request_id,
            assignments=assignments,
            handled_inbox_item_ids=handled_inbox_item_ids,
            correlation_id=correlation_id,
            causation_id=causation_id,
            tool_name="team_request_ephemeral_agents",
            single=False,
        )

    def _request_ephemeral_assignments_command(
        self,
        *,
        session_id: str,
        request_id: str,
        assignments: Sequence[Mapping[str, Any]],
        handled_inbox_item_ids: Sequence[str] | None,
        correlation_id: str | None,
        causation_id: str | None,
        tool_name: str,
        single: bool,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_assignments = [
            _normalize_ephemeral_request_mapping(item, field_name=f"assignments[{index}]")
            for index, item in enumerate(assignments)
        ]
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name=tool_name,
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="ephemeral_request",
        )
        normalized_payload = _team_request_ephemeral_normalized_payload(
            assignments=normalized_assignments,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, tool_name)
            if not normalized_assignments:
                raise TeamCommandValidationError(
                    code="empty_ephemeral_assignments",
                    message="assignments must contain at least one assignment",
                    correction_context={"assignments": []},
                )
            result = _ephemeral_request_result()
            try:
                _create_ephemeral_assignments(
                    state,
                    parent=agent,
                    requests=[dict(item) for item in normalized_assignments],
                    dispatch_id=resolved.dispatch_id,
                    result=result,
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="ephemeral_request_failed",
                    correction_context={
                        "caller_agent_id": agent.id,
                        "assignments": list(normalized_assignments),
                    },
                ) from exc

            assignment_ids = list(result["created_ephemeral_assignment_ids"])
            created_assignments = [
                _require_present(
                    state.ephemeral_assignments,
                    assignment_id,
                    "ephemeral assignment",
                )
                for assignment_id in assignment_ids
            ]
            events = [
                _ephemeral_assignment_event_payload(state, assignment_id)
                for assignment_id in assignment_ids
            ]
            outbox_records = _team_event_outbox_records(events)
            outbox_ids = [record.id for record in outbox_records]
            worker_session_ids = [assignment.session_id for assignment in created_assignments]
            worker_branch_names = [
                ephemeral_agent_branch(assignment.session_id, assignment.id)
                for assignment in created_assignments
            ]
            response_payload: dict[str, Any] = {
                "intent_recorded": True,
                "async_effect_pending": True,
                "downstream_effect_completed": False,
                "created_ephemeral_assignment_ids": assignment_ids,
                "assignment_ids": assignment_ids,
                "worker_session_ids": worker_session_ids,
                "worker_branch_names": worker_branch_names,
                "worker_start_intent_ids": assignment_ids,
                "ephemeral_event_ids": assignment_ids,
                "outbox_record_ids": outbox_ids,
                "events": events,
                "progress_made": True,
            }
            if single and created_assignments:
                assignment = created_assignments[0]
                response_payload.update(
                    {
                        "assignment_id": assignment.id,
                        "worker_session_id": assignment.session_id,
                        "worker_branch_name": ephemeral_agent_branch(
                            assignment.session_id,
                            assignment.id,
                        ),
                    }
                )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=[*assignment_ids, *outbox_ids],
                created_event_ids=assignment_ids,
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def accept_ephemeral_result_command(
        self,
        *,
        session_id: str,
        assignment_id: str,
        request_id: str,
        deliverable_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_deliverable_id = _optional_text(deliverable_id)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_accept_ephemeral_result",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="ephemeral_acceptance",
        )
        normalized_payload = _team_accept_ephemeral_result_normalized_payload(
            assignment_id=normalized_assignment_id,
            deliverable_id=normalized_deliverable_id,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_accept_ephemeral_result")
            assignment = _require_present(
                state.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            _require_parent_assignment_owner(
                assignment,
                agent=agent,
                tool_name="team_accept_ephemeral_result",
            )
            try:
                created_deliverable_id = _apply_ephemeral_acceptance(
                    state,
                    assignment=assignment,
                    accepted=True,
                    deliverable_id=normalized_deliverable_id,
                    rejection_reason=None,
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="ephemeral_acceptance_failed",
                    correction_context={
                        "assignment_id": assignment.id,
                        "caller_agent_id": agent.id,
                        "state": assignment.state.value,
                    },
                ) from exc
            return TeamCommandApplyResult(
                response_payload={
                    "assignment_id": assignment.id,
                    "deliverable_id": created_deliverable_id,
                    "accepted_ephemeral_assignment_ids": [assignment.id],
                    "state": assignment.state.value,
                    "progress_made": True,
                },
                created_domain_ids=[
                    item for item in [assignment.id, created_deliverable_id] if item is not None
                ],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def reject_ephemeral_result_command(
        self,
        *,
        session_id: str,
        assignment_id: str,
        request_id: str,
        rejection_reason: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_rejection_reason = _optional_text(rejection_reason)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_reject_ephemeral_result",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="ephemeral_rejection",
        )
        normalized_payload = _team_reject_ephemeral_result_normalized_payload(
            assignment_id=normalized_assignment_id,
            rejection_reason=normalized_rejection_reason,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_reject_ephemeral_result")
            assignment = _require_present(
                state.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            _require_parent_assignment_owner(
                assignment,
                agent=agent,
                tool_name="team_reject_ephemeral_result",
            )
            _apply_ephemeral_acceptance(
                state,
                assignment=assignment,
                accepted=False,
                deliverable_id=None,
                rejection_reason=normalized_rejection_reason,
            )
            return TeamCommandApplyResult(
                response_payload={
                    "assignment_id": assignment.id,
                    "rejected_ephemeral_assignment_ids": [assignment.id],
                    "rejection_reason": assignment.error,
                    "state": assignment.state.value,
                    "progress_made": True,
                },
                created_domain_ids=[assignment.id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def report_ephemeral_result_command(
        self,
        *,
        session_id: str,
        request_id: str,
        assignment_id: str | None = None,
        summary: str | None = None,
        artifact_refs: Sequence[str] | None = None,
        agent_branch: str | None = None,
        commit_sha: str | None = None,
        error: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_assignment_id = _optional_text(assignment_id)
        normalized_summary = _optional_text(summary)
        normalized_artifact_refs = _string_list(artifact_refs or [], "artifact_refs")
        normalized_agent_branch = _optional_text(agent_branch)
        normalized_commit_sha = _optional_text(commit_sha)
        normalized_error = _optional_text(error)
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name=EPHEMERAL_WORKER_REPORT_TOOL,
            request_id=normalized_request_id,
            assignment_id=normalized_assignment_id,
            wake_session_class="ephemeral_worker",
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="ephemeral_result",
        )
        normalized_payload = _team_report_ephemeral_result_normalized_payload(
            assignment_id=normalized_assignment_id,
            summary=normalized_summary,
            artifact_refs=normalized_artifact_refs,
            agent_branch=normalized_agent_branch,
            commit_sha=normalized_commit_sha,
            error=normalized_error,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            if resolved.assignment_id is None:
                raise TeamCommandValidationError(
                    code="assignment_not_resolved",
                    message="ephemeral assignment could not be resolved from the worker session",
                    correction_context={"session_id": normalized_session_id},
                )
            assignment = _require_present(
                state.ephemeral_assignments,
                resolved.assignment_id,
                "ephemeral assignment",
            )
            _validate_ephemeral_result_report(
                assignment,
                summary=normalized_summary,
                agent_branch=normalized_agent_branch,
                error=normalized_error,
            )
            created_inbox_item_ids: list[str] = []
            events: list[dict[str, Any]] = []
            outbox_records: list[TeamOutboxRecord] = []
            if normalized_error is not None:
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = normalized_error
                assignment.updated_at = utc_now()
                reported_success = False
            else:
                assignment.summary = _required_text(normalized_summary, "summary")
                assignment.artifact_refs = list(normalized_artifact_refs)
                assignment.agent_branch = normalized_agent_branch
                assignment.commit_sha = normalized_commit_sha
                assignment.error = None
                assignment.state = EphemeralAssignmentState.COMPLETED
                inbox_id = _create_ephemeral_parent_notice(state, assignment)
                created_inbox_item_ids.append(inbox_id)
                assignment.updated_at = utc_now()
                events.append(
                    _ephemeral_assignment_event_payload(
                        state,
                        assignment.id,
                        event_type=EPHEMERAL_COMPLETED_EVENT_TYPE,
                    )
                )
                outbox_records = _team_event_outbox_records(events)
                reported_success = True
            outbox_ids = [record.id for record in outbox_records]
            return TeamCommandApplyResult(
                response_payload={
                    "assignment_id": assignment.id,
                    "state": assignment.state.value,
                    "reported_success": reported_success,
                    "assignment_error": assignment.error,
                    "created_inbox_item_ids": created_inbox_item_ids,
                    "intent_recorded": reported_success,
                    "async_effect_pending": reported_success,
                    "downstream_effect_completed": False,
                    "completed_event_ids": [assignment.id] if reported_success else [],
                    "ephemeral_event_ids": [assignment.id] if reported_success else [],
                    "outbox_record_ids": outbox_ids,
                    "events": events,
                    "progress_made": True,
                },
                created_domain_ids=[
                    assignment.id,
                    *created_inbox_item_ids,
                    *outbox_ids,
                ],
                created_event_ids=[assignment.id] if reported_success else [],
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=[],
            apply_command=_apply,
            require_active_dispatch=False,
        )


__all__ = ["TeamEphemeralCommandService"]
