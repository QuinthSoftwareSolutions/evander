from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from evander.models import utc_now
from .git_release_helpers import (
    _empty_git_command_result,
    _git_command_validation_error,
    _release_command_next_action_hints,
    _team_decide_initial_release_normalized_payload,
    _team_publish_review_branch_normalized_payload,
    _team_recover_release_normalized_payload,
    _team_retry_release_gate_normalized_payload,
    _team_request_release_normalized_payload,
    _team_update_feature_branch_normalized_payload,
)
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import _require_command_agent
from ...git_naming import validate_branch_name
from ...models import (
    TeamState,
    _unique_text_list,
    release_package_payload_with_derived,
)
from ..domain.common import (
    _optional_text,
    _required_text,
    _string_list,
)
from ..domain.git_common import _normalize_agent_branch_ref
from ..domain.git_promotion import (
    _apply_git_feature_update,
    _apply_git_review_promotion,
)
from ..domain.initial_release import (
    initial_release_decision_permitted,
    mark_initial_release_not_planned,
    reopen_initial_release_decisions,
)
from ..domain.project_status import _recalculate_project_status
from ..domain.release_packages import (
    _apply_git_release_recovery,
    _apply_git_release_request,
    _failed_release_promotion_for_recovery,
    _release_request_permitted,
)
from ..domain.release_gates import retry_release_gate

from .outbox import _team_event_outbox_records
from .executor import TeamCommandExecutor


class TeamGitReleaseCommandService(TeamCommandExecutor):
    def publish_review_branch_command(
        self,
        *,
        session_id: str,
        request_id: str,
        assignment_id: str | None = None,
        deliverable_id: str | None = None,
        source_branch: str | None = None,
        commit_sha: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_assignment_id = _optional_text(assignment_id)
        normalized_deliverable_id = _optional_text(deliverable_id)
        normalized_source_branch = _optional_text(source_branch)
        normalized_commit_sha = _optional_text(commit_sha)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_publish_review_branch",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="git_review_promotion",
        )
        normalized_payload = _team_publish_review_branch_normalized_payload(
            assignment_id=normalized_assignment_id,
            deliverable_id=normalized_deliverable_id,
            source_branch=normalized_source_branch,
            commit_sha=normalized_commit_sha,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_publish_review_branch")
            has_assignment = normalized_assignment_id is not None
            has_deliverable = normalized_deliverable_id is not None
            if has_assignment == has_deliverable:
                raise TeamCommandValidationError(
                    code="invalid_review_promotion_shape",
                    message=(
                        "team_publish_review_branch requires exactly one of "
                        "assignment_id or deliverable_id"
                    ),
                    correction_context={
                        "assignment_id": normalized_assignment_id,
                        "deliverable_id": normalized_deliverable_id,
                    },
                )
            if has_assignment and (
                normalized_source_branch is not None or normalized_commit_sha is not None
            ):
                raise TeamCommandValidationError(
                    code="invalid_review_promotion_shape",
                    message=(
                        "assignment_id review promotions must not include "
                        "source_branch or commit_sha"
                    ),
                    correction_context={
                        "assignment_id": normalized_assignment_id,
                        "source_branch": normalized_source_branch,
                        "commit_sha": normalized_commit_sha,
                    },
                )
            request: dict[str, Any] = {}
            if normalized_assignment_id is not None:
                request["assignment_id"] = normalized_assignment_id
            if normalized_deliverable_id is not None:
                request["deliverable_id"] = normalized_deliverable_id
            if normalized_source_branch is not None:
                request["source_branch"] = normalized_source_branch
            if normalized_commit_sha is not None:
                request["commit_sha"] = normalized_commit_sha
            intent_result = _empty_git_command_result()
            promotion_id = _apply_git_review_promotion(
                state,
                actor=agent,
                request=request,
                result=intent_result,
            )
            if promotion_id is None:
                raise _git_command_validation_error(
                    intent_result,
                    code="git_review_promotion_invalid",
                )
            promotion = state.git_promotions[promotion_id]
            outbox_records = _team_event_outbox_records(intent_result["events"])
            outbox_ids = [record.id for record in outbox_records]
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": True,
                    "downstream_effect_completed": False,
                    "promotion_id": promotion_id,
                    "promotion_event_id": promotion_id,
                    "promotion_event_ids": [promotion_id],
                    "git_promotion_event_ids": [promotion_id],
                    "target_review_branch": promotion.target_branch,
                    "target_branch": promotion.target_branch,
                    "source_branch": promotion.source_branch,
                    "commit_sha": promotion.commit_sha,
                    "project_id": promotion.project_id,
                    "task_id": promotion.task_id,
                    "deliverable_id": promotion.deliverable_id,
                    "assignment_id": normalized_assignment_id,
                    "outbox_record_ids": outbox_ids,
                    "events": list(intent_result["events"]),
                    "next_action_hints": [
                        "Review branch promotion intent recorded; wait for the async Git promotion result before assuming the branch exists."
                    ],
                    "progress_made": True,
                },
                created_domain_ids=[promotion_id, *outbox_ids],
                created_event_ids=[promotion_id],
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )

    def update_feature_branch_command(
        self,
        *,
        session_id: str,
        task_id: str,
        source_review_branches: Sequence[str],
        request_id: str,
        feature_branch: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_sources = _string_list(
            source_review_branches,
            "source_review_branches",
        )
        normalized_feature_branch = _optional_text(feature_branch)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_update_feature_branch",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="git_feature_promotion",
        )
        normalized_payload = _team_update_feature_branch_normalized_payload(
            task_id=normalized_task_id,
            source_review_branches=normalized_sources,
            feature_branch=normalized_feature_branch,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_update_feature_branch")
            intent_result = _empty_git_command_result()
            promotion_id = _apply_git_feature_update(
                state,
                actor=agent,
                update={
                    "task_id": normalized_task_id,
                    "source_review_branches": list(normalized_sources),
                    "feature_branch": normalized_feature_branch,
                },
                result=intent_result,
            )
            if promotion_id is None:
                raise _git_command_validation_error(
                    intent_result,
                    code="git_feature_update_invalid",
                )
            promotion = state.git_promotions[promotion_id]
            outbox_records = _team_event_outbox_records(intent_result["events"])
            outbox_ids = [record.id for record in outbox_records]
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": True,
                    "downstream_effect_completed": False,
                    "promotion_id": promotion_id,
                    "promotion_event_id": promotion_id,
                    "promotion_event_ids": [promotion_id],
                    "git_promotion_event_ids": [promotion_id],
                    "target_feature_branch": promotion.target_branch,
                    "target_branch": promotion.target_branch,
                    "source_review_branches": list(normalized_sources),
                    "project_id": promotion.project_id,
                    "task_id": promotion.task_id,
                    "outbox_record_ids": outbox_ids,
                    "events": list(intent_result["events"]),
                    "next_action_hints": [
                        "Feature branch promotion intent recorded; wait for the async Git promotion result before requesting release from this branch."
                    ],
                    "progress_made": True,
                },
                created_domain_ids=[promotion_id, *outbox_ids],
                created_event_ids=[promotion_id],
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def decide_initial_release_command(
        self,
        *,
        session_id: str,
        deliverable_ids: Sequence[str],
        decision: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        normalized_payload = _team_decide_initial_release_normalized_payload(
            deliverable_ids=deliverable_ids,
            decision=decision,
            reason=reason,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_decide_initial_release",
            request_id=normalized_request_id,
            correlation_id=normalized_payload["correlation_id"],
            causation_id=normalized_payload["causation_id"],
            progress_classification="initial_release_decision",
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_decide_initial_release")
            selected_ids = list(normalized_payload["deliverable_ids"])
            unauthorized_ids = [
                deliverable_id
                for deliverable_id in selected_ids
                if not initial_release_decision_permitted(
                    state,
                    actor=agent,
                    deliverable_id=deliverable_id,
                )
            ]
            if unauthorized_ids:
                raise TeamCommandValidationError(
                    code="initial_release_decision_forbidden",
                    message=(
                        "initial release decisions require the project lead or "
                        "top-level team manager for: " + ", ".join(unauthorized_ids)
                    ),
                    correction_context={"deliverable_ids": unauthorized_ids},
                )
            now = utc_now()
            actor = f"agent:{agent.id}"
            try:
                if normalized_payload["decision"] == "not_planned":
                    updated_ids = mark_initial_release_not_planned(
                        state,
                        deliverable_ids=selected_ids,
                        decided_by_actor=actor,
                        reason=normalized_payload["reason"],
                        now=now,
                    )
                else:
                    updated_ids = reopen_initial_release_decisions(
                        state,
                        deliverable_ids=selected_ids,
                        reopened_by_actor=actor,
                        reason=normalized_payload["reason"],
                        now=now,
                    )
            except ValueError as exc:
                raise TeamCommandValidationError(
                    code="initial_release_decision_invalid",
                    message=str(exc),
                    correction_context={"deliverable_ids": selected_ids},
                ) from None
            return TeamCommandApplyResult(
                response_payload={
                    "decision": normalized_payload["decision"],
                    "deliverable_ids": updated_ids,
                    "reason": normalized_payload["reason"],
                    "progress_made": bool(updated_ids),
                },
                created_domain_ids=list(updated_ids),
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )

    def request_release_command(
        self,
        *,
        session_id: str,
        project_id: str,
        deliverable_ids: Sequence[str],
        request_id: str,
        id: str | None = None,
        supersedes_release_package_id: str | None = None,
        post_release_feedback_case_id: str | None = None,
        release_branch: str | None = None,
        canonical_branch: str | None = None,
        summary: str | None = None,
        verification_waivers: Sequence[Mapping[str, Any]] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_project_id = _required_text(project_id, "project_id")
        normalized_release_id = _optional_text(id)
        normalized_predecessor_id = _optional_text(supersedes_release_package_id)
        normalized_post_release_case_id = _optional_text(post_release_feedback_case_id)
        normalized_deliverable_ids = _string_list(
            deliverable_ids,
            "deliverable_ids",
        )
        normalized_release_branch = _optional_text(release_branch)
        normalized_canonical_branch = _optional_text(canonical_branch)
        normalized_summary = _optional_text(summary)
        normalized_verification_waivers = [dict(item) for item in (verification_waivers or [])]
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_request_release",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="git_release_request",
        )
        normalized_payload = _team_request_release_normalized_payload(
            release_id=normalized_release_id,
            supersedes_release_package_id=normalized_predecessor_id,
            post_release_feedback_case_id=normalized_post_release_case_id,
            project_id=normalized_project_id,
            deliverable_ids=normalized_deliverable_ids,
            release_branch=normalized_release_branch,
            canonical_branch=normalized_canonical_branch,
            summary=normalized_summary,
            verification_waivers=normalized_verification_waivers,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_request_release")
            if normalized_release_branch is not None:
                try:
                    validate_branch_name(normalized_release_branch)
                except ValueError as exc:
                    raise TeamCommandValidationError(
                        code="invalid_release_branch",
                        message=f"release_branch is invalid: {exc}",
                        correction_context={"release_branch": normalized_release_branch},
                    ) from None
            if normalized_canonical_branch is not None:
                try:
                    validate_branch_name(normalized_canonical_branch)
                except ValueError as exc:
                    raise TeamCommandValidationError(
                        code="invalid_canonical_branch",
                        message=f"canonical_branch is invalid: {exc}",
                        correction_context={"canonical_branch": normalized_canonical_branch},
                    ) from None
            intent_result = _empty_git_command_result()
            projects_to_recalc: set[str] = set()
            project = state.projects.get(normalized_project_id)
            if project is None:
                raise TeamCommandValidationError(
                    code="project_not_found",
                    message=(
                        f"git release request references missing project {normalized_project_id}"
                    ),
                    correction_context={"project_id": normalized_project_id},
                )
            release_id = _apply_git_release_request(
                state,
                actor=agent,
                request={
                    "id": normalized_release_id,
                    "supersedes_release_package_id": normalized_predecessor_id,
                    "post_release_feedback_case_id": (normalized_post_release_case_id),
                    "project_id": normalized_project_id,
                    "deliverable_ids": list(normalized_deliverable_ids),
                    "release_branch": normalized_release_branch,
                    "canonical_branch": normalized_canonical_branch,
                    "summary": normalized_summary,
                    "verification_waivers": normalized_verification_waivers,
                    "release_requested_by_actor": f"agent:{agent.id}",
                },
                result=intent_result,
                projects_to_recalc=projects_to_recalc,
            )
            if release_id is None:
                raise _git_command_validation_error(
                    intent_result,
                    code="git_release_request_invalid",
                )
            for recalc_id in sorted(projects_to_recalc):
                _recalculate_project_status(state, recalc_id)
            release = state.release_packages[release_id]
            promotion_event_ids = list(intent_result["git_promotion_event_ids"])
            outbox_records = _team_event_outbox_records(intent_result["events"])
            outbox_ids = [record.id for record in outbox_records]
            release_payload = release_package_payload_with_derived(state, release)
            created = bool(intent_result.get("created", True))
            progress_made = created or bool(promotion_event_ids or outbox_ids)
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": bool(promotion_event_ids),
                    "downstream_effect_completed": False,
                    "release_package_id": release_id,
                    "release_package_ids": [release_id],
                    "created": created,
                    "duplicate": bool(intent_result.get("duplicate", False)),
                    "existing_release_package_id": intent_result.get("existing_release_package_id"),
                    "promotion_event_ids": promotion_event_ids,
                    "git_promotion_event_ids": promotion_event_ids,
                    "release_branch": release.release_branch,
                    "target_release_branch": release.release_branch,
                    "canonical_branch": release.canonical_branch,
                    "release_package": release_payload,
                    "outbox_record_ids": outbox_ids,
                    "events": list(intent_result["events"]),
                    "next_action_hints": list(intent_result.get("next_action_hints") or [])
                    or _release_command_next_action_hints(
                        state,
                        release,
                        promotion_event_ids=promotion_event_ids,
                    ),
                    "progress_made": progress_made,
                },
                created_domain_ids=(
                    [release_id, *promotion_event_ids, *outbox_ids] if progress_made else []
                ),
                created_event_ids=promotion_event_ids,
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
            reject_stale_high_priority_inbox=True,
        )

    def recover_release_command(
        self,
        *,
        session_id: str,
        release_package_id: str,
        agent_branch: str,
        commit_sha: str,
        request_id: str,
        summary: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_release_id = _required_text(
            release_package_id,
            "release_package_id",
        )
        normalized_agent_branch = _required_text(agent_branch, "agent_branch")
        normalized_commit_sha = _required_text(commit_sha, "commit_sha")
        normalized_summary = _optional_text(summary)
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_recover_release",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="git_release_recovery",
        )
        normalized_payload = _team_recover_release_normalized_payload(
            release_package_id=normalized_release_id,
            agent_branch=normalized_agent_branch,
            commit_sha=normalized_commit_sha,
            summary=normalized_summary,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_recover_release")
            intent_result = _empty_git_command_result()
            release_id = _apply_git_release_recovery(
                state,
                actor=agent,
                request={
                    "release_package_id": normalized_release_id,
                    "agent_branch": normalized_agent_branch,
                    "commit_sha": normalized_commit_sha,
                    "summary": normalized_summary,
                },
                result=intent_result,
            )
            if release_id is None:
                raise _git_command_validation_error(
                    intent_result,
                    code="git_release_recovery_invalid",
                )
            release = state.release_packages[release_id]
            failed_promotion = _failed_release_promotion_for_recovery(state, release)
            outbox_records = _team_event_outbox_records(intent_result["events"])
            outbox_ids = [record.id for record in outbox_records]
            recovery_event_ids = list(intent_result["release_recovery_event_ids"])
            if not recovery_event_ids:
                recovery_event_ids = [release_id]
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": True,
                    "downstream_effect_completed": False,
                    "release_package_id": release_id,
                    "release_recovery_event_ids": recovery_event_ids,
                    "promotion_id": failed_promotion.id if failed_promotion else None,
                    "promotion_event_ids": ([failed_promotion.id] if failed_promotion else []),
                    "repaired_review_manifest_ids": list(
                        intent_result.get("repaired_review_manifest_ids") or []
                    ),
                    "agent_branch": _normalize_agent_branch_ref(normalized_agent_branch),
                    "target_branch": (
                        release.release_branch
                        or (failed_promotion.target_branch if failed_promotion else None)
                    ),
                    "release_package": release_package_payload_with_derived(
                        state,
                        release,
                    ),
                    "outbox_record_ids": outbox_ids,
                    "events": list(intent_result["events"]),
                    "next_action_hints": [
                        "Release recovery intent recorded; wait for the async Git recovery effect before retrying or marking the release published."
                    ],
                    "progress_made": True,
                },
                created_domain_ids=[release_id, *outbox_ids],
                created_event_ids=recovery_event_ids,
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def retry_release_gate_command(
        self,
        *,
        session_id: str,
        release_package_id: str,
        gate_kind: str,
        gate_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        normalized_payload = _team_retry_release_gate_normalized_payload(
            release_package_id=release_package_id,
            gate_kind=gate_kind,
            gate_id=gate_id,
            reason=reason,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_retry_release_gate",
            request_id=normalized_request_id,
            correlation_id=normalized_payload["correlation_id"],
            causation_id=normalized_payload["causation_id"],
            progress_classification="release_gate_retry",
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_retry_release_gate")
            release = state.release_packages.get(normalized_payload["release_package_id"])
            if release is None:
                raise TeamCommandValidationError(
                    code="missing_release_package",
                    message=(
                        f"release package {normalized_payload['release_package_id']} does not exist"
                    ),
                )
            project = state.projects.get(release.project_id)
            if project is None or not _release_request_permitted(
                state,
                actor=agent,
                project=project,
            ):
                raise TeamCommandValidationError(
                    code="release_gate_retry_forbidden",
                    message=(
                        f"agent {agent.id} is not permitted to retry release gates on "
                        f"project {release.project_id}"
                    ),
                )
            intent_result: dict[str, Any] = {
                "events": [],
                "created_inbox_item_ids": [],
                "created_review_request_ids": [],
                "release_verification_run_ids": [],
            }
            try:
                retry_release_gate(
                    state,
                    release=release,
                    gate_kind=normalized_payload["gate_kind"],
                    gate_id=normalized_payload["gate_id"],
                    reason=normalized_payload["reason"],
                    requested_by=f"agent:{agent.id}",
                    result=intent_result,
                )
            except ValueError as exc:
                raise TeamCommandValidationError(
                    code="release_gate_retry_invalid",
                    message=str(exc),
                ) from exc
            _recalculate_project_status(state, release.project_id)
            outbox_records = _team_event_outbox_records(intent_result["events"])
            outbox_ids = [record.id for record in outbox_records]
            replacement_id = str(intent_result["replacement_gate_id"])
            return TeamCommandApplyResult(
                response_payload={
                    "intent_recorded": True,
                    "async_effect_pending": normalized_payload["gate_kind"] == "verification",
                    "downstream_effect_completed": False,
                    "release_package_id": release.id,
                    "gate_kind": normalized_payload["gate_kind"],
                    "retried_gate_id": intent_result["retried_gate_id"],
                    "replacement_gate_id": replacement_id,
                    "retry_reason": normalized_payload["reason"],
                    "retry_requested_by": intent_result["retry_requested_by"],
                    "release_package": release_package_payload_with_derived(state, release),
                    "outbox_record_ids": outbox_ids,
                    "events": list(intent_result["events"]),
                    "progress_made": True,
                },
                created_domain_ids=[replacement_id, *outbox_ids],
                created_event_ids=[
                    str(event.get("idempotency_key") or replacement_id)
                    for event in intent_result["events"]
                ],
                outbox_records=outbox_records,
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamGitReleaseCommandService"]
