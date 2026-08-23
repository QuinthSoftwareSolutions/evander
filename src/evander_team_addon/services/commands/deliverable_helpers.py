from __future__ import annotations


from evander.models import utc_now
from collections.abc import (
    Mapping,
    Sequence,
)
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4
from .types import (
    TeamCommandValidationError,
    TeamDeliverableArtifactPublisher,
)
from .validators import (
    _require_command_agent_by_id,
    _required_command_text,
    _validate_command_recipient_active,
    _validate_command_same_team,
    _validate_command_task_scope,
)
from ...models import (
    AGENT_OPERATOR_ONLY_FIELDS,
    AgentInboxItem,
    AgentTask,
    Deliverable,
    DeliverableOutcome,
    PersistentAgent,
    ReviewRequest,
    TaskCompletion,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    _unique_text_list,
)
from ..domain.common import (
    _append_unique,
    _generated_id,
    _list_of_mappings,
    _mapping,
    _optional_mapping,
    _optional_text,
    _require_present,
    _required_text,
    _set_task_state,
    _string_list,
)
from ..domain.deliverables import (
    _deliverable_git_refs_from_payload,
    _deliverable_verification_from_payload,
    _deliverable_verification_payload,
    _normalize_deliverable_verification_mapping,
    _open_accepted_rework_deliverables,
    _superseded_deliverable_for_rework,
    _validate_deliverable_outcomes_against_task_contract,
)
from ..domain.git_work_contexts import (
    GitCandidateCommitResolver,
    GitWorkContextValidationError,
    bind_deliverable_git_refs_to_work_context,
    mark_git_work_context_submitted,
)
from ..domain.project_status import _recalculate_project_status
from ..domain.outcome_catalog import snapshot_deliverable_outcomes
from ..domain.review import _review_profile_for_request
from ..domain.task_assessment import (
    TaskAssessmentBoundaryError,
    require_deliverable_submission,
    require_task_deliverable_contract_provenance,
    task_payload_with_assessment,
)
from ..domain.task_supervision import (
    parent_task_for_child,
    queue_parent_task_transition,
    task_acceptance_recipient_agent_id,
)
from ..domain.work_due import _mark_agent_work_due


_DELIVERABLE_GIT_REF_INPUT_FIELDS = frozenset(
    {
        "branch_context_id",
        "agent_branch",
        "commit_sha",
    }
)


def _normalize_deliverable_git_ref_mapping(
    value: object,
    *,
    field_name: str,
) -> dict[str, str | None]:
    payload = _optional_mapping(value, field_name) or {}
    unsupported_fields = sorted(
        str(key) for key in payload if key not in _DELIVERABLE_GIT_REF_INPUT_FIELDS
    )
    if unsupported_fields:
        correction = "; use commit_sha instead of commit" if "commit" in unsupported_fields else ""
        allowed = ", ".join(sorted(_DELIVERABLE_GIT_REF_INPUT_FIELDS))
        raise ValueError(
            f"{field_name} contains unsupported field(s): "
            f"{', '.join(unsupported_fields)}{correction}; "
            f"allowed fields are: {allowed}"
        )
    return {
        "branch_context_id": _optional_text(payload.get("branch_context_id")),
        "agent_branch": _optional_text(payload.get("agent_branch")),
        "commit_sha": _optional_text(payload.get("commit_sha")),
    }


def _normalize_deliverable_submission_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    payload = _mapping(item, field_name)
    git_refs = _normalize_deliverable_git_ref_mapping(
        payload.get("git_refs"),
        field_name=f"{field_name}.git_refs",
    )
    normalized = {
        "id": _optional_text(payload.get("id")),
        "task_id": _required_text(payload.get("task_id"), f"{field_name}.task_id"),
        "summary": _optional_text(payload.get("summary")) or "",
        "supersedes_deliverable_id": _optional_text(payload.get("supersedes_deliverable_id")),
        "branch_context_id": _optional_text(payload.get("branch_context_id")),
        "artifact_paths": _string_list(
            payload.get("artifact_paths"),
            f"{field_name}.artifact_paths",
        ),
        "artifact_refs": _string_list(
            payload.get("artifact_refs"),
            f"{field_name}.artifact_refs",
        ),
        "artifact_space_ref": _optional_text(payload.get("artifact_space_ref")),
        "git_refs": git_refs,
        "outcomes": [
            DeliverableOutcome.model_validate(outcome).model_dump(mode="json")
            for outcome in _list_of_mappings(
                payload.get("outcomes"),
                f"{field_name}.outcomes",
            )
        ],
        "source_deliverable_ids": _string_list(
            payload.get("source_deliverable_ids"),
            f"{field_name}.source_deliverable_ids",
        ),
    }
    completion = payload.get("completion")
    if completion is not None:
        normalized["completion"] = TaskCompletion.model_validate(completion).model_dump(mode="json")
    verification = _normalize_deliverable_verification_mapping(
        payload.get("verification"),
        field_name=f"{field_name}.verification",
    )
    if verification is not None:
        normalized["verification"] = verification
    return normalized


def _normalize_review_request_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    payload = _mapping(item, field_name)
    return {
        "id": _optional_text(payload.get("id")),
        "deliverable_id": _optional_text(payload.get("deliverable_id")),
        "reviewer_agent_id": _required_text(
            payload.get("reviewer_agent_id"),
            f"{field_name}.reviewer_agent_id",
        ),
        "review_profile_id": _optional_text(payload.get("review_profile_id")),
        "instructions": _optional_text(payload.get("instructions")),
    }


def _normalize_review_decision_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    payload = _mapping(item, field_name)
    criteria = _list_of_mappings(payload.get("criteria"), f"{field_name}.criteria")
    finding_resolutions = _list_of_mappings(
        payload.get("finding_resolutions"),
        f"{field_name}.finding_resolutions",
    )
    return {
        "review_request_id": _required_text(
            payload.get("review_request_id"),
            f"{field_name}.review_request_id",
        ),
        "state": _required_text(payload.get("state"), f"{field_name}.state"),
        "block_kind": _optional_text(payload.get("block_kind")),
        "findings": _string_list(payload.get("findings"), f"{field_name}.findings"),
        "criteria": [
            {
                "criterion_id": _optional_text(criterion.get("criterion_id")),
                "state": _optional_text(criterion.get("state")),
                "finding": _optional_text(criterion.get("finding")),
            }
            for criterion in criteria
        ],
        "finding_resolutions": [
            {
                "finding_id": _optional_text(resolution.get("finding_id")),
                "resolution": _optional_text(resolution.get("resolution")),
                "evidence_refs": _string_list(
                    resolution.get("evidence_refs"),
                    f"{field_name}.finding_resolutions.evidence_refs",
                ),
            }
            for resolution in finding_resolutions
        ],
        "override_routing_gap_reason": _optional_text(payload.get("override_routing_gap_reason")),
    }


def _team_submit_deliverable_normalized_payload(
    *,
    deliverable: Mapping[str, Any],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "deliverable": dict(deliverable),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_submit_deliverable_for_review_normalized_payload(
    *,
    deliverable: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "deliverable": dict(deliverable),
        "reviews": [dict(item) for item in reviews],
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_request_review_normalized_payload(
    *,
    review: Mapping[str, Any],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "review": dict(review),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_decide_review_normalized_payload(
    *,
    decision: Mapping[str, Any],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "decision": dict(decision),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _command_deliverable_task_for_submission(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task_id: str,
) -> AgentTask:
    _validate_command_task_scope(state, agent=agent, task_id=task_id)
    task = _require_present(state.tasks, task_id, "task")
    if task.owner_agent_id != agent.id:
        raise TeamCommandValidationError(
            code="task_not_owned",
            message=f"agent {agent.id} cannot deliver task {task.id}",
            correction_context={
                "task_id": task.id,
                "task_owner_agent_id": task.owner_agent_id,
                "caller_agent_id": agent.id,
            },
        )
    return task


def _command_deliverable_id(
    state: TeamState,
    *,
    deliverable: Mapping[str, Any],
    request_id: str,
) -> str:
    explicit_id = _optional_text(deliverable.get("id"))
    if explicit_id is not None:
        return explicit_id
    return _generated_id(
        prefix="deliverable",
        labels=[deliverable.get("task_id"), request_id],
        existing_ids=state.deliverables,
    )


def _command_review_request_id(
    state: TeamState,
    *,
    deliverable_id: str,
    review_request: Mapping[str, Any],
    request_id: str,
    existing_ids: Mapping[str, Any] | set[str] | None = None,
) -> str:
    explicit_id = _optional_text(review_request.get("id"))
    if explicit_id is not None:
        return explicit_id
    return _generated_id(
        prefix="review",
        labels=[
            deliverable_id,
            review_request.get("reviewer_agent_id"),
            review_request.get("review_profile_id") or "review",
            request_id,
        ],
        existing_ids=existing_ids if existing_ids is not None else state.review_requests,
    )


def _plan_command_review_requests_for_deliverable(
    state: TeamState,
    *,
    actor: PersistentAgent,
    deliverable: Deliverable,
    task: AgentTask,
    reviews: Sequence[Mapping[str, Any]],
    request_id: str,
) -> list[dict[str, Any]]:
    deliverable_id = deliverable.id
    reserved_review_ids: set[str] = set(state.review_requests)
    planned: list[dict[str, Any]] = []
    for review_request in reviews:
        reviewer = _require_command_agent_by_id(
            state,
            _required_text(
                review_request.get("reviewer_agent_id"),
                "reviewer_agent_id",
            ),
            field_name="reviewer_agent_id",
        )
        _validate_command_recipient_active(reviewer)
        _validate_command_same_team(actor, reviewer)
        profile_id = _optional_text(review_request.get("review_profile_id"))
        profile, profile_error = _review_profile_for_request(
            state,
            profile_id=profile_id,
            expected_team_id=actor.team_id or "",
            project_id=task.project_id,
            reviewer=reviewer,
            deliverable=deliverable,
        )
        if profile_error is not None:
            raise TeamCommandValidationError(
                code="review_profile_invalid",
                message=profile_error,
                correction_context={
                    "deliverable_id": deliverable_id,
                    "reviewer_agent_id": reviewer.id,
                    "review_profile_id": profile_id,
                    "project_id": task.project_id,
                },
            )
        if profile is not None and profile_id is None:
            profile_id = profile.id
        review_id = _command_review_request_id(
            state,
            deliverable_id=deliverable_id,
            review_request={
                **dict(review_request),
                "review_profile_id": profile_id,
            },
            request_id=request_id,
            existing_ids=reserved_review_ids,
        )
        if review_id in reserved_review_ids:
            raise TeamCommandValidationError(
                code="review_request_already_exists",
                message=f"review request {review_id} already exists",
                correction_context={"review_request_id": review_id},
            )
        reserved_review_ids.add(review_id)
        planned.append(
            {
                **dict(review_request),
                "id": review_id,
                "deliverable_id": deliverable_id,
                "review_profile_id": profile_id,
            }
        )
    return planned


def _publish_command_deliverable_artifacts(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task: AgentTask,
    deliverable_id: str,
    dispatch_id: str,
    artifact_paths: Sequence[str],
    artifact_publisher: TeamDeliverableArtifactPublisher | None,
) -> list[dict[str, Any]]:
    del state
    safe_paths = _safe_deliverable_artifact_paths(artifact_paths)
    if not safe_paths:
        return []
    if artifact_publisher is None:
        raise TeamCommandValidationError(
            code="artifact_publisher_unavailable",
            message="artifact_paths were provided but no host artifact publisher is available",
            correction_context={
                "deliverable_id": deliverable_id,
                "artifact_paths": list(safe_paths),
            },
        )
    try:
        record = artifact_publisher(
            agent=agent,
            team_id=agent.team_id,
            project_id=task.project_id,
            deliverable_id=deliverable_id,
            dispatch_id=dispatch_id,
            artifact_paths=list(safe_paths),
            source_paths=list(safe_paths),
        )
    except Exception as exc:
        raise TeamCommandValidationError(
            code="artifact_publication_failed",
            message=f"failed to publish artifact_paths: {exc}",
            correction_context={
                "deliverable_id": deliverable_id,
                "artifact_paths": list(safe_paths),
            },
        ) from None
    record_payload = dict(record)
    artifact_ref = _optional_text(record_payload.get("artifact_ref"))
    if artifact_ref is None:
        raise TeamCommandValidationError(
            code="artifact_publication_failed",
            message="artifact publisher did not return artifact_ref",
            correction_context={
                "deliverable_id": deliverable_id,
                "artifact_paths": list(safe_paths),
                "published_artifact": record_payload,
            },
        )
    return [record_payload]


def _validate_command_deliverable_submission_pre_publication(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task: AgentTask,
    deliverable: Mapping[str, Any],
    deliverable_id: str,
    dispatch_id: str | None,
    candidate_commit_resolver: GitCandidateCommitResolver | None = None,
) -> None:
    if deliverable_id in state.deliverables:
        raise TeamCommandValidationError(
            code="deliverable_already_exists",
            message=f"deliverable {deliverable_id} already exists",
            correction_context={"deliverable_id": deliverable_id},
        )
    _required_command_text(
        deliverable.get("summary"),
        field_name="summary",
        code="empty_summary",
    )
    supersedes_deliverable_id = _optional_text(deliverable.get("supersedes_deliverable_id"))
    if deliverable_id == supersedes_deliverable_id:
        raise TeamCommandValidationError(
            code="invalid_supersession",
            message="deliverable id cannot match supersedes_deliverable_id",
            correction_context={
                "deliverable_id": deliverable_id,
                "supersedes_deliverable_id": supersedes_deliverable_id,
            },
        )
    open_rework_deliverables = _open_accepted_rework_deliverables(
        state,
        task=task,
    )
    if open_rework_deliverables:
        required_predecessor_ids = [predecessor.id for predecessor in open_rework_deliverables]
        if supersedes_deliverable_id not in required_predecessor_ids:
            raise TeamCommandValidationError(
                code="accepted_deliverable_rework_requires_replacement",
                message=(
                    f"task {task.id} was reopened for accepted-deliverable rework; "
                    "the next deliverable must supersede its exact accepted predecessor"
                ),
                correction_context={
                    "task_id": task.id,
                    "required_supersedes_deliverable_ids": required_predecessor_ids,
                    "supersedes_deliverable_id": supersedes_deliverable_id,
                    "recommended_tool": "team_submit_deliverable_for_review",
                },
            )
    try:
        require_deliverable_submission(
            state,
            task_id=task.id,
            source_deliverable_ids=_string_list(
                deliverable.get("source_deliverable_ids"),
                "source_deliverable_ids",
            ),
        )
    except TaskAssessmentBoundaryError as exc:
        raise TeamCommandValidationError(
            code=exc.code,
            message=exc.message,
            correction_context={
                "deliverable_id": deliverable_id,
                **exc.correction_context,
            },
        ) from None
    try:
        _superseded_deliverable_for_rework(
            state,
            task=task,
            producer_agent_id=agent.id,
            supersedes_deliverable_id=supersedes_deliverable_id,
        )
        if not isinstance(deliverable, dict):
            raise ValueError("deliverable must be mutable for Git context binding")
        bind_deliverable_git_refs_to_work_context(
            state,
            agent_id=agent.id,
            task_id=task.id,
            deliverable=deliverable,
            dispatch_id=dispatch_id,
            candidate_commit_resolver=candidate_commit_resolver,
        )
        git_refs = _deliverable_git_refs_from_payload(deliverable.get("git_refs"))
        require_task_deliverable_contract_provenance(
            state,
            task_id=task.id,
            git_refs=git_refs,
            producer_agent_id=agent.id,
            deliverable_id=deliverable_id,
            require_submitted_context=False,
        )
        _deliverable_verification_from_payload(deliverable.get("verification"))
        outcomes = [
            DeliverableOutcome.model_validate(outcome)
            for outcome in _list_of_mappings(
                deliverable.get("outcomes"),
                "outcomes",
            )
        ]
        outcomes = snapshot_deliverable_outcomes(
            state,
            project_id=task.project_id,
            outcomes=outcomes,
        )
        completion = (
            TaskCompletion.model_validate(deliverable["completion"])
            if deliverable.get("completion") is not None
            else None
        )
        _validate_deliverable_outcomes_against_task_contract(
            task,
            outcomes,
            completion=completion,
        )
    except GitWorkContextValidationError as exc:
        raise TeamCommandValidationError(
            code=exc.code,
            message=exc.message,
            correction_context={
                "deliverable_id": deliverable_id,
                "task_id": task.id,
                **exc.correction_context,
            },
        ) from None
    except TaskAssessmentBoundaryError as exc:
        raise TeamCommandValidationError(
            code=exc.code,
            message=exc.message,
            correction_context={
                "deliverable_id": deliverable_id,
                "task_id": task.id,
                **exc.correction_context,
            },
        ) from None
    except ValueError as exc:
        raise TeamCommandValidationError(
            code="deliverable_validation_failed",
            message=str(exc),
            correction_context={
                "deliverable_id": deliverable_id,
                "task_id": task.id,
                "supersedes_deliverable_id": supersedes_deliverable_id,
            },
        ) from None


def _safe_deliverable_artifact_paths(paths: Sequence[str]) -> list[str]:
    normalized = _unique_text_list(paths, "artifact_paths")
    for value in normalized:
        text = value.replace("\\", "/")
        path = PurePosixPath(text)
        if (
            not text
            or "\x00" in text
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise TeamCommandValidationError(
                code="unsafe_artifact_path",
                message=(
                    "artifact_paths entries must be relative to /artifacts and "
                    "must not contain dot segments"
                ),
                correction_context={"artifact_path": value},
            )
    return [value.replace("\\", "/") for value in normalized]


def _apply_command_deliverable_submission(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task: AgentTask,
    deliverable: Mapping[str, Any],
    deliverable_id: str,
    published_records: Sequence[Mapping[str, Any]],
    suppress_superior_notice: bool,
) -> list[str]:
    if deliverable_id in state.deliverables:
        raise TeamCommandValidationError(
            code="deliverable_already_exists",
            message=f"deliverable {deliverable_id} already exists",
            correction_context={"deliverable_id": deliverable_id},
        )
    summary = _required_command_text(
        deliverable.get("summary"),
        field_name="summary",
        code="empty_summary",
    )
    supersedes_deliverable_id = _optional_text(deliverable.get("supersedes_deliverable_id"))
    if deliverable_id == supersedes_deliverable_id:
        raise TeamCommandValidationError(
            code="invalid_supersession",
            message="deliverable id cannot match supersedes_deliverable_id",
            correction_context={
                "deliverable_id": deliverable_id,
                "supersedes_deliverable_id": supersedes_deliverable_id,
            },
        )
    try:
        superseded_deliverable = _superseded_deliverable_for_rework(
            state,
            task=task,
            producer_agent_id=agent.id,
            supersedes_deliverable_id=supersedes_deliverable_id,
        )
        git_refs = _deliverable_git_refs_from_payload(deliverable.get("git_refs"))
        verification = _deliverable_verification_from_payload(deliverable.get("verification"))
        outcomes = [
            DeliverableOutcome.model_validate(outcome)
            for outcome in _list_of_mappings(
                deliverable.get("outcomes"),
                "outcomes",
            )
        ]
        outcomes = snapshot_deliverable_outcomes(
            state,
            project_id=task.project_id,
            outcomes=outcomes,
        )
        source_deliverable_ids = _string_list(
            deliverable.get("source_deliverable_ids"),
            "source_deliverable_ids",
        )
        completion = (
            TaskCompletion.model_validate(deliverable["completion"])
            if deliverable.get("completion") is not None
            else None
        )
        _validate_deliverable_outcomes_against_task_contract(
            task,
            outcomes,
            completion=completion,
        )
    except ValueError as exc:
        raise TeamCommandValidationError(
            code="deliverable_validation_failed",
            message=str(exc),
            correction_context={
                "deliverable_id": deliverable_id,
                "task_id": task.id,
                "supersedes_deliverable_id": supersedes_deliverable_id,
            },
        ) from None
    artifact_refs = _command_deliverable_artifact_refs(
        deliverable,
        published_records=published_records,
    )
    acceptance_recipient_agent_id = (
        task_acceptance_recipient_agent_id(
            state,
            task=task,
            producer_agent_id=agent.id,
        )
        if not suppress_superior_notice
        else None
    )
    branch_context = (
        state.git_work_contexts.get(git_refs.branch_context_id)
        if git_refs.branch_context_id is not None
        else None
    )
    dependency_composition_manifest = (
        branch_context.dependency_composition_manifest.model_copy(deep=True)
        if branch_context is not None
        and branch_context.dependency_composition_manifest is not None
        else None
    )
    new_deliverable = Deliverable(
        id=deliverable_id,
        task_id=task.id,
        producer_agent_id=agent.id,
        summary=summary,
        artifact_refs=artifact_refs,
        git_refs=git_refs,
        verification=verification,
        outcomes=outcomes,
        source_deliverable_ids=source_deliverable_ids,
        completion=completion,
        state=TeamRecordState.PENDING,
        acceptance_recipient_agent_id=acceptance_recipient_agent_id,
        supersedes_deliverable_id=supersedes_deliverable_id,
        carried_findings=(
            [
                finding.model_copy(deep=True)
                for finding in superseded_deliverable.carried_findings
                if finding.state == "open"
            ]
            if superseded_deliverable is not None
            else []
        ),
        dependency_composition_manifest=dependency_composition_manifest,
        acceptance_policy_version=(2 if suppress_superior_notice else 1),
    )
    state.deliverables[new_deliverable.id] = new_deliverable
    if superseded_deliverable is not None:
        superseded_deliverable.superseded_by_deliverable_id = new_deliverable.id
        superseded_deliverable.supersession_reason = "Rework deliverable submitted."
        superseded_deliverable.superseded_at = new_deliverable.created_at
        superseded_deliverable.superseded_by_actor = f"agent:{agent.id}"
    task.deliverable_ids = _append_unique(task.deliverable_ids, new_deliverable.id)
    now = utc_now()
    mark_git_work_context_submitted(
        state,
        branch_context_id=new_deliverable.git_refs.branch_context_id,
        now=now,
    )
    _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
    task.updated_at = now
    _recalculate_project_status(state, task.project_id)
    created_inbox_ids: list[str] = []
    parent_task = parent_task_for_child(state, task)
    if not suppress_superior_notice and acceptance_recipient_agent_id is not None:
        inbox_id = f"inbox-{uuid4().hex}"
        payload = {
            "kind": "superior_acceptance_requested",
            "deliverable_id": new_deliverable.id,
            "task_id": task.id,
            "producer_agent_id": agent.id,
            "acceptance_recipient_agent_id": acceptance_recipient_agent_id,
            "summary": new_deliverable.summary,
            "artifact_refs": list(new_deliverable.artifact_refs),
            "available_decisions": ["accepted", "rejected"],
            "available_alternatives": ["request_review", "report_blocker"],
        }
        if parent_task is not None:
            payload.update(
                {
                    "transition": "handoff",
                    "parent_task_id": parent_task.id,
                    "child_task_id": task.id,
                }
            )
        verification_payload = _deliverable_verification_payload(new_deliverable)
        if verification_payload is not None:
            payload["verification"] = verification_payload
        state.inbox[inbox_id] = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=acceptance_recipient_agent_id,
            sender_agent_id=agent.id,
            task_id=task.id,
            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
            response_requested=True,
            priority=TeamInboxPriority.NORMAL,
            payload=payload,
            created_at=now,
            updated_at=now,
        )
        created_inbox_ids.append(inbox_id)
        _mark_agent_work_due(
            state,
            agent_id=acceptance_recipient_agent_id,
            now=now,
        )
    if parent_task is not None and (
        suppress_superior_notice or acceptance_recipient_agent_id != parent_task.owner_agent_id
    ):
        parent_notice = queue_parent_task_transition(
            state,
            child_task_id=task.id,
            transition="handoff",
            sender_agent_id=agent.id,
            now=now,
            references={"deliverable_id": new_deliverable.id},
        )
        if parent_notice is not None:
            created_inbox_ids.append(parent_notice.id)
    return created_inbox_ids


def _command_deliverable_artifact_refs(
    deliverable: Mapping[str, Any],
    *,
    published_records: Sequence[Mapping[str, Any]],
) -> list[str]:
    refs = _string_list(deliverable.get("artifact_refs"), "artifact_refs")
    artifact_space_ref = _optional_text(deliverable.get("artifact_space_ref"))
    if artifact_space_ref is not None:
        refs = _append_unique(refs, artifact_space_ref)
    for record in published_records:
        refs = _append_unique(
            refs,
            _required_text(record.get("artifact_ref"), "artifact_ref"),
        )
    return refs


def _review_decision_correction_context(
    state: TeamState,
    *,
    review: ReviewRequest | None,
    reviewer: PersistentAgent | None,
) -> dict[str, Any]:
    deliverable = state.deliverables.get(review.deliverable_id) if review is not None else None
    task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
    return {
        "review": review.model_dump(mode="json") if review is not None else None,
        "deliverable": (deliverable.model_dump(mode="json") if deliverable is not None else None),
        "task": (task_payload_with_assessment(state, task) if task is not None else None),
        "reviewer": (
            reviewer.model_dump(mode="json", exclude=AGENT_OPERATOR_ONLY_FIELDS)
            if reviewer is not None
            else None
        ),
    }


__all__ = [
    "_normalize_deliverable_submission_mapping",
    "_normalize_review_request_mapping",
    "_normalize_review_decision_mapping",
    "_team_submit_deliverable_normalized_payload",
    "_team_submit_deliverable_for_review_normalized_payload",
    "_team_request_review_normalized_payload",
    "_team_decide_review_normalized_payload",
    "_command_deliverable_task_for_submission",
    "_command_deliverable_id",
    "_command_review_request_id",
    "_plan_command_review_requests_for_deliverable",
    "_publish_command_deliverable_artifacts",
    "_validate_command_deliverable_submission_pre_publication",
    "_safe_deliverable_artifact_paths",
    "_apply_command_deliverable_submission",
    "_command_deliverable_artifact_refs",
    "_review_decision_correction_context",
]
