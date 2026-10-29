from __future__ import annotations

from collections.abc import Sequence
from typing import Any
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
from .work_item_helpers import (
    _case_command_fingerprint,
    _normalize_case_update_command_fields,
    _team_case_priority_value,
    _team_case_status_value,
    _team_create_case_normalized_payload,
)
from ...models import (
    TeamCase,
    TeamCaseStatus,
    HumanReviewFinding,
    HumanReviewRequest,
    TeamInboxPriority,
    Project,
    ReleasePackage,
    TeamState,
    _unique_text_list,
)
from ..domain.agent_access import (
    _is_project_lead_for,
    _is_team_manager,
)
from ..domain.case_task import (
    _apply_case_update,
    _restore_case_task_state,
    _snapshot_case_task_state,
)
from ..domain.case_events import append_case_closed_event
from ..domain.common import (
    _UNSET,
    _optional_text,
    _required_text,
)
from ..domain.human_reviews import (
    close_post_release_feedback_case,
    record_human_finding_resolution_proposal,
    route_human_finding_correction,
)

from .executor import TeamCommandExecutor


class TeamCaseCommandService(TeamCommandExecutor):
    def create_case_command(
        self,
        *,
        session_id: str,
        title: str,
        request_id: str,
        case_id: str | None = None,
        project_id: str | None = None,
        description: str | None = None,
        status: TeamCaseStatus | str = TeamCaseStatus.OPEN,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        owner_agent_id: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        tags: Sequence[str] | None = None,
        task_ids: Sequence[str] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_case_id = _optional_text(case_id)
        normalized_project_id = _optional_text(project_id)
        normalized_title = _optional_text(title) or ""
        normalized_description = _optional_text(description) or ""
        normalized_status = _team_case_status_value(status, default=TeamCaseStatus.OPEN)
        normalized_priority = _team_case_priority_value(
            priority,
            default=TeamInboxPriority.NORMAL,
        )
        normalized_owner_id = _optional_text(owner_agent_id)
        normalized_source = _optional_text(source)
        normalized_source_ref = _optional_text(source_ref)
        normalized_tags = _unique_text_list(tags or [], "tags")
        normalized_task_ids = _unique_text_list(task_ids or [], "task_ids")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_create_case",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = _team_create_case_normalized_payload(
            case_id=normalized_case_id,
            project_id=normalized_project_id,
            title=normalized_title,
            description=normalized_description,
            status=normalized_status,
            priority=normalized_priority,
            owner_agent_id=normalized_owner_id,
            source=normalized_source,
            source_ref=normalized_source_ref,
            tags=normalized_tags,
            task_ids=normalized_task_ids,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_create_case")
            if normalized_case_id is not None and normalized_case_id in state.cases:
                raise TeamCommandValidationError(
                    code="case_already_exists",
                    message=f"case {normalized_case_id} already exists",
                    correction_context={"case_id": normalized_case_id},
                )
            update: dict[str, Any] = {
                "title": normalized_title,
                "description": normalized_description,
                "status": normalized_status,
                "priority": normalized_priority,
                "tags": list(normalized_tags),
                "task_ids": list(normalized_task_ids),
            }
            if normalized_case_id is not None:
                update["id"] = normalized_case_id
            if normalized_project_id is not None:
                update["project_id"] = normalized_project_id
            if normalized_owner_id is not None:
                update["owner_agent_id"] = normalized_owner_id
            if normalized_source is not None:
                update["source"] = normalized_source
            if normalized_source_ref is not None:
                update["source_ref"] = normalized_source_ref
            snapshot = _snapshot_case_task_state(state)
            try:
                created_case_id, created = _apply_case_update(
                    state,
                    actor=agent,
                    update=update,
                )
            except ValueError as exc:
                _restore_case_task_state(state, snapshot)
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="case_validation_failed",
                    correction_context={
                        "case_id": normalized_case_id,
                        "project_id": normalized_project_id,
                        "owner_agent_id": normalized_owner_id,
                        "task_ids": list(normalized_task_ids),
                    },
                ) from exc
            if not created:
                _restore_case_task_state(state, snapshot)
                raise TeamCommandValidationError(
                    code="case_already_exists",
                    message=f"case {created_case_id} already exists",
                    correction_context={"case_id": created_case_id},
                )
            return TeamCommandApplyResult(
                response_payload={
                    "case_id": created_case_id,
                    "created_case_ids": [created_case_id],
                    "progress_made": True,
                },
                created_domain_ids=[created_case_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def update_case_command(
        self,
        *,
        session_id: str,
        case_id: str,
        request_id: str | None = None,
        project_id: object = _UNSET,
        title: object = _UNSET,
        description: object = _UNSET,
        status: object = _UNSET,
        priority: object = _UNSET,
        owner_agent_id: object = _UNSET,
        source: object = _UNSET,
        source_ref: object = _UNSET,
        tags: object = _UNSET,
        task_ids: object = _UNSET,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_case_id = _required_text(case_id, "case_id")
        normalized_request_id = _optional_text(request_id)
        normalized_updates = _normalize_case_update_command_fields(
            project_id=project_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            owner_agent_id=owner_agent_id,
            source=source,
            source_ref=source_ref,
            tags=tags,
            task_ids=task_ids,
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_case",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "case_id": normalized_case_id,
            "updates": normalized_updates,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_update_case")
            if normalized_case_id not in state.cases:
                raise TeamCommandValidationError(
                    code="case_not_found",
                    message=f"case {normalized_case_id} does not exist",
                    correction_context={"case_id": normalized_case_id},
                )
            before = _case_command_fingerprint(state, normalized_case_id)
            previous_status = state.cases[normalized_case_id].status
            snapshot = _snapshot_case_task_state(state)
            try:
                updated_case_id, created = _apply_case_update(
                    state,
                    actor=agent,
                    update={"case_id": normalized_case_id, **normalized_updates},
                )
            except ValueError as exc:
                _restore_case_task_state(state, snapshot)
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="case_validation_failed",
                    correction_context={
                        "case_id": normalized_case_id,
                        "updates": dict(normalized_updates),
                    },
                ) from exc
            if created:
                _restore_case_task_state(state, snapshot)
                raise TeamCommandValidationError(
                    code="case_not_found",
                    message=f"case {normalized_case_id} does not exist",
                    correction_context={"case_id": normalized_case_id},
                )
            changed = before != _case_command_fingerprint(state, updated_case_id)
            events: list[dict[str, Any]] = []
            event = append_case_closed_event(
                events,
                state.cases[updated_case_id],
                previous_status=previous_status,
            )
            return TeamCommandApplyResult(
                response_payload={
                    "case_id": updated_case_id,
                    "updated_case_ids": [updated_case_id] if changed else [],
                    "progress_made": changed,
                    "events": events,
                },
                created_domain_ids=[updated_case_id] if changed else [],
                created_event_ids=[str(event["id"])] if event is not None else [],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def propose_human_finding_resolution_command(
        self,
        *,
        session_id: str,
        finding_id: str,
        proposed_resolution: str,
        evidence_refs: Sequence[str],
        proposed_by_deliverable_ids: Sequence[str],
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_finding_id = _required_text(finding_id, "finding_id")
        normalized_resolution = _required_text(
            proposed_resolution,
            "proposed_resolution",
        )
        normalized_evidence_refs = _unique_text_list(
            evidence_refs,
            "evidence_refs",
        )
        normalized_deliverable_ids = _unique_text_list(
            proposed_by_deliverable_ids,
            "proposed_by_deliverable_ids",
        )
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_propose_human_finding_resolution",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "finding_id": normalized_finding_id,
            "proposed_resolution": normalized_resolution,
            "evidence_refs": sorted(normalized_evidence_refs),
            "proposed_by_deliverable_ids": sorted(normalized_deliverable_ids),
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(
                resolved,
                "team_propose_human_finding_resolution",
            )
            finding, _request, _release, project, _case = _human_finding_command_context(
                state,
                finding_id=normalized_finding_id,
            )
            correction_task = state.tasks.get(finding.correction_task_id)
            if correction_task is None:
                raise TeamCommandValidationError(
                    code="human_finding_correction_task_missing",
                    message=(
                        f"human review finding {finding.id} references missing "
                        f"correction task {finding.correction_task_id}"
                    ),
                )
            if not (
                correction_task.owner_agent_id == actor.id
                or _is_project_lead_for(state, actor.id, project.id)
                or (
                    actor.team_id == project.team_id
                    and _is_team_manager(state, actor)
                )
            ):
                raise TeamCommandValidationError(
                    code="human_finding_resolution_not_permitted",
                    message=(
                        f"agent {actor.id} cannot propose a resolution for "
                        f"human review finding {finding.id}"
                    ),
                    correction_context={
                        "finding_id": finding.id,
                        "correction_task_id": correction_task.id,
                        "correction_task_owner_agent_id": (correction_task.owner_agent_id),
                    },
                )
            try:
                updated = record_human_finding_resolution_proposal(
                    state,
                    finding_id=finding.id,
                    proposed_resolution=normalized_resolution,
                    evidence_refs=normalized_evidence_refs,
                    proposed_by_deliverable_ids=normalized_deliverable_ids,
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="human_finding_resolution_invalid",
                    correction_context={"finding_id": finding.id},
                ) from exc
            return TeamCommandApplyResult(
                response_payload={
                    "human_review_finding_id": updated.id,
                    "correction_task_id": updated.correction_task_id,
                    "proposed_by_deliverable_ids": list(updated.proposed_by_deliverable_ids),
                    "progress_made": True,
                },
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )

    def route_human_finding_correction_command(
        self,
        *,
        session_id: str,
        finding_id: str,
        owner_agent_id: str,
        request_id: str,
        target_deliverable_ids: Sequence[str] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_finding_id = _required_text(finding_id, "finding_id")
        normalized_owner_id = _required_text(owner_agent_id, "owner_agent_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_target_ids = (
            _unique_text_list(
                target_deliverable_ids,
                "target_deliverable_ids",
            )
            if target_deliverable_ids is not None
            else None
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_route_human_finding_correction",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "finding_id": normalized_finding_id,
            "owner_agent_id": normalized_owner_id,
            "target_deliverable_ids": normalized_target_ids,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(
                resolved,
                "team_route_human_finding_correction",
            )
            finding, _request, _release, project, case = _human_finding_command_context(
                state,
                finding_id=normalized_finding_id,
            )
            authorized_triage_actor = (
                case.owner_agent_id == actor.id
                or _is_project_lead_for(state, actor.id, project.id)
                or (
                    actor.team_id == project.team_id
                    and _is_team_manager(state, actor)
                )
            )
            if not authorized_triage_actor:
                raise TeamCommandValidationError(
                    code="human_finding_route_not_permitted",
                    message=(
                        f"agent {actor.id} cannot route the correction task for "
                        f"human review finding {finding.id}"
                    ),
                    correction_context={
                        "finding_id": finding.id,
                        "case_id": case.id,
                    },
                )
            result: dict[str, Any] = {"events": []}
            try:
                correction_task = route_human_finding_correction(
                    state,
                    finding_id=finding.id,
                    owner_agent_id=normalized_owner_id,
                    result=result,
                    target_deliverable_ids=normalized_target_ids,
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="human_finding_route_invalid",
                    correction_context={
                        "finding_id": finding.id,
                        "case_id": case.id,
                    },
                ) from exc
            return TeamCommandApplyResult(
                response_payload={
                    **result,
                    "human_review_finding_id": finding.id,
                    "correction_task_id": correction_task.id,
                    "progress_made": True,
                },
                created_domain_ids=[
                    *result.get("created_inbox_item_ids", []),
                ],
                created_event_ids=[
                    str(event.get("id"))
                    for event in result.get("events", [])
                    if isinstance(event, dict) and event.get("id")
                ],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )

    def close_post_release_feedback_case_command(
        self,
        *,
        session_id: str,
        case_id: str,
        reason: str,
        evidence_refs: Sequence[str],
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_case_id = _required_text(case_id, "case_id")
        normalized_reason = _required_text(reason, "reason")
        normalized_evidence_refs = _unique_text_list(
            evidence_refs,
            "evidence_refs",
        )
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_close_post_release_case",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "case_id": normalized_case_id,
            "reason": normalized_reason,
            "evidence_refs": sorted(normalized_evidence_refs),
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            actor = _require_command_agent(
                resolved,
                "team_close_post_release_case",
            )
            case = state.cases.get(normalized_case_id)
            project = (
                state.projects.get(case.project_id)
                if case is not None and case.project_id is not None
                else None
            )
            if case is None or project is None:
                raise TeamCommandValidationError(
                    code="post_release_case_not_found",
                    message=(f"post-release feedback case {normalized_case_id} does not exist"),
                )
            if not (
                case.owner_agent_id == actor.id
                or _is_project_lead_for(state, actor.id, project.id)
                or (
                    actor.team_id == project.team_id
                    and _is_team_manager(state, actor)
                )
            ):
                raise TeamCommandValidationError(
                    code="post_release_case_close_not_permitted",
                    message=(f"agent {actor.id} cannot close post-release feedback case {case.id}"),
                    correction_context={
                        "case_id": case.id,
                        "owner_agent_id": case.owner_agent_id,
                    },
                )
            result: dict[str, Any] = {}
            try:
                closed = close_post_release_feedback_case(
                    state,
                    case_id=case.id,
                    reason=normalized_reason,
                    evidence_refs=normalized_evidence_refs,
                    actor_kind="agent",
                    actor_id=actor.id,
                    result=result,
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="post_release_case_close_invalid",
                    correction_context={"case_id": case.id},
                ) from exc
            return TeamCommandApplyResult(
                response_payload={
                    **result,
                    "case": closed.model_dump(mode="json"),
                    "progress_made": True,
                },
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )


def _human_finding_command_context(
    state: TeamState,
    *,
    finding_id: str,
) -> tuple[
    HumanReviewFinding,
    HumanReviewRequest,
    ReleasePackage,
    Project,
    TeamCase,
]:
    finding = state.human_review_findings.get(finding_id)
    if finding is None:
        raise TeamCommandValidationError(
            code="human_finding_not_found",
            message=f"human review finding {finding_id} does not exist",
        )
    request = state.human_review_requests.get(finding.source_human_review_request_id)
    release = (
        state.release_packages.get(request.release_package_id) if request is not None else None
    )
    project = state.projects.get(release.project_id) if release is not None else None
    correction_task = state.tasks.get(finding.correction_task_id)
    case = (
        state.cases.get(correction_task.case_id)
        if correction_task is not None and correction_task.case_id is not None
        else None
    )
    if request is None or release is None or project is None or case is None:
        raise TeamCommandValidationError(
            code="human_finding_context_invalid",
            message=f"human review finding {finding.id} has incomplete lineage",
            correction_context={"finding_id": finding.id},
        )
    return finding, request, release, project, case


__all__ = ["TeamCaseCommandService"]
