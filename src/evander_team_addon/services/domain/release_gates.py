from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from ...models import (
    AgentStatus,
    AgentTask,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ReleaseCandidateDeliverableEvidence,
    ReleaseCandidateManifest,
    ReleaseCandidateReviewEvidence,
    ReleaseGateFailure,
    ReleasePackage,
    ReleaseVerificationCommandResult,
    ReleaseVerificationRun,
    ReviewRequest,
    ReviewProfile,
    HumanReviewRequirementSnapshot,
    HumanReviewFindingSnapshot,
    TeamBlockerSourceKind,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TaskDeliverableContract,
    TeamTaskKind,
    TeamTaskState,
    agent_has_project_assignment,
    project_lead_agent_id,
)
from .case_task import _set_task_git_base_branch
from .common import _append_unique, _required_text, utc_now
from .blockers import (
    open_or_get_blocker,
    open_release_blocker,
    resolve_active_blockers_for_source,
)
from .review_handoff import queue_actionable_inbox_item
from .outcome_catalog import snapshot_review_profile_outcomes
from .human_reviews import (
    ensure_human_review_request,
    human_review_request_for_release,
)
from .release_packages import allowed_release_parent_task_ids
from .task_assessment import deliverable_source_lineage, require_release_scope


RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE = "team.release.verification_requested"

_TERMINAL_CANDIDATE_CORRECTION_TASK_STATES = {
    TeamTaskState.FINISHED,
    TeamTaskState.ACCEPTED,
    TeamTaskState.REJECTED,
    TeamTaskState.CANCELLED,
}


def _candidate_correction_git_base(release: ReleasePackage) -> str | None:
    """Return the branch that contains the exact rejected candidate."""

    manifest_release_branch = (
        release.manifest.release_branch if release.manifest is not None else None
    )
    return (
        release.release_branch
        or manifest_release_branch
        or release.git_base_branch
    )


def _reconcile_candidate_correction_task_git_base(
    state: TeamState,
    *,
    task: AgentTask,
    release: ReleasePackage,
    result: dict[str, Any],
    now: datetime,
) -> None:
    """Pin an active, unprepared correction task to its rejected candidate."""

    if task.state in _TERMINAL_CANDIDATE_CORRECTION_TASK_STATES:
        return
    candidate_base = _candidate_correction_git_base(release)
    if candidate_base is None or task.git_base_branch == candidate_base:
        return
    if _set_task_git_base_branch(
        state,
        task=task,
        git_base_branch=candidate_base,
    ):
        task.updated_at = now
        result.setdefault("updated_task_ids", []).append(task.id)


def _release_review_scope_error(
    *,
    project: Any,
    reviewer: Any,
    profile: Any,
    reviewer_agent_id: str,
    review_profile_id: str,
) -> str | None:
    if reviewer is None or reviewer.team_id != project.team_id:
        return f"release reviewer {reviewer_agent_id} is missing or outside team {project.team_id}"
    if profile is None or profile.team_id != project.team_id or profile.archived:
        return f"release review profile {review_profile_id} is unavailable"
    if profile.project_id not in {None, project.id}:
        return f"release review profile {profile.id} does not apply to project {project.id}"
    return None


def _create_release_review(
    state: TeamState,
    *,
    release: ReleasePackage,
    reviewer: PersistentAgent,
    profile: ReviewProfile,
    candidate_commit_sha: str,
    instructions: str,
    result: dict[str, Any],
    now: datetime,
    supersedes: ReviewRequest | None = None,
    retry_reason: str | None = None,
    retry_requested_by: str | None = None,
) -> ReviewRequest:
    """Create the durable review record shared by initial and retry handoffs."""

    review = ReviewRequest(
        id=f"release-review-{release.id}-{uuid4().hex}",
        release_package_id=release.id,
        candidate_commit_sha=candidate_commit_sha,
        reviewer_agent_id=reviewer.id,
        review_profile_id=profile.id,
        criteria_snapshot=[criterion.model_copy(deep=True) for criterion in profile.criteria],
        required_outcome_kind_definitions=snapshot_review_profile_outcomes(
            state,
            profile=profile,
            project_id=release.project_id,
        ),
        reviewed_commit_sha=candidate_commit_sha,
        state=TeamRecordState.PENDING,
        supersedes_review_request_id=supersedes.id if supersedes is not None else None,
        retry_reason=retry_reason,
        retry_requested_by=retry_requested_by,
        instructions=instructions,
        created_at=now,
    )
    if supersedes is not None:
        supersedes.superseded_by_review_request_id = review.id
    state.review_requests[review.id] = review
    release.review_request_ids = _append_unique(release.review_request_ids, review.id)
    result.setdefault("created_review_request_ids", []).append(review.id)
    return review


def _queue_release_review_handoff(
    state: TeamState,
    *,
    release: ReleasePackage,
    project: Project,
    reviewer: PersistentAgent,
    profile: ReviewProfile,
    review: ReviewRequest,
    result: dict[str, Any],
    now: datetime,
) -> None:
    """Deliver a release review through the common review inbox lifecycle."""

    payload: dict[str, Any] = {
        "kind": "release_review_request",
        "review_request_id": review.id,
        "release_package_id": release.id,
        "candidate_commit_sha": review.candidate_commit_sha,
        "release_manifest": (
            release.manifest.model_dump(mode="json") if release.manifest is not None else None
        ),
        "review_profile": {
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "criteria": [criterion.model_dump(mode="json") for criterion in profile.criteria],
            "required_outcome_kinds": list(profile.required_outcome_kinds),
            "outcome_kind_definitions": [
                definition.model_dump(mode="json")
                for definition in review.required_outcome_kind_definitions
            ],
        },
    }
    if review.supersedes_review_request_id is not None:
        payload["supersedes_review_request_id"] = review.supersedes_review_request_id
    if review.retry_reason is not None:
        payload["retry_reason"] = review.retry_reason
    if review.retry_requested_by is not None:
        payload["retry_requested_by"] = review.retry_requested_by
    queue_actionable_inbox_item(
        state,
        recipient_agent_id=reviewer.id,
        sender_agent_id=project.lead_agent_id,
        priority=TeamInboxPriority.HIGH,
        payload=payload,
        result=result,
        now=now,
    )


def begin_release_gates(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
    candidate_commit_sha: str,
    candidate_tree_hash: str | None,
    result: dict[str, Any],
) -> None:
    project = state.projects.get(release.project_id)
    if project is None:
        _reject_release(
            state,
            release,
            stage="configuration",
            kind="configuration",
            error=f"release project {release.project_id} does not exist",
            result=result,
        )
        return
    policy = project.release_policy
    if policy is None:
        _reject_release(
            state,
            release,
            stage="configuration",
            kind="configuration",
            error=(
                "git release policy is not configured; recreate the release after configuring "
                "verification and review gates"
            ),
            result=result,
        )
        return
    if release.candidate_commit_sha not in {None, candidate_commit_sha}:
        _reject_release(
            state,
            release,
            stage="configuration",
            kind="configuration",
            error=(
                f"release candidate is already pinned to {release.candidate_commit_sha}, not "
                f"{candidate_commit_sha}"
            ),
            result=result,
        )
        return

    release.candidate_commit_sha = candidate_commit_sha
    release.candidate_tree_hash = candidate_tree_hash
    predecessor = state.release_packages.get(
        release.supersedes_release_package_id or ""
    )
    if (
        predecessor is not None
        and (
            (
                candidate_tree_hash is not None
                and candidate_tree_hash == predecessor.candidate_tree_hash
            )
            or (
                candidate_tree_hash is None
                and candidate_commit_sha == predecessor.candidate_commit_sha
            )
        )
    ):
        _reject_release(
            state,
            release,
            stage="human_review",
            kind="candidate",
            error=(
                f"successor release candidate is unchanged from predecessor "
                f"{predecessor.id}"
            ),
            result=result,
        )
        return
    unchanged_release = _unchanged_failed_release(
        state,
        release=release,
        candidate_commit_sha=candidate_commit_sha,
        candidate_tree_hash=candidate_tree_hash,
    )
    if unchanged_release is not None:
        _reject_release(
            state,
            release,
            stage="verification",
            kind="candidate",
            error=(
                f"release candidate content is unchanged from failed release "
                f"{unchanged_release.id}; submit changed reviewed content"
            ),
            result=result,
        )
        return
    try:
        release.manifest = _candidate_manifest(
            state,
            release=release,
            release_promotion=release_promotion,
        )
    except ValueError as exc:
        _reject_release(
            state,
            release,
            stage="configuration",
            kind="configuration",
            error=str(exc),
            result=result,
        )
        return
    release.gate_failure = None
    release.updated_at = utc_now()

    if policy.verification_commands:
        run_id = _verification_run_id(release.id, candidate_commit_sha, 1)
        run = state.release_verification_runs.get(run_id)
        if run is None:
            run = ReleaseVerificationRun(
                id=run_id,
                release_package_id=release.id,
                candidate_commit_sha=candidate_commit_sha,
                session_id=f"team-release-verification-{uuid4().hex}",
                attempt_number=1,
                command_results=[
                    ReleaseVerificationCommandResult(name=command.name)
                    for command in policy.verification_commands
                ],
            )
            state.release_verification_runs[run.id] = run
        release.verification_run_ids = _append_unique(
            release.verification_run_ids,
            run.id,
        )
        _append_verification_requested_event(
            result,
            release=release,
            run=run,
        )
        result.setdefault("release_verification_run_ids", []).append(run.id)
        return

    advance_release_gates(
        state,
        release=release,
        release_promotion=release_promotion,
        result=result,
    )


def record_release_verification_result(
    state: TeamState,
    *,
    release_package_id: str,
    verification_run_id: str,
    candidate_commit_sha: str,
    verification_state: str,
    failure_kind: str | None,
    command_results: list[ReleaseVerificationCommandResult],
    error: str | None,
    result: dict[str, Any],
) -> None:
    release = state.release_packages.get(release_package_id)
    run = state.release_verification_runs.get(verification_run_id)
    if release is None or run is None:
        raise ValueError("release package or verification run does not exist")
    if release.candidate_commit_sha != candidate_commit_sha:
        raise ValueError(
            f"release {release.id} is pinned to {release.candidate_commit_sha}, not "
            f"{candidate_commit_sha}"
        )
    if run.candidate_commit_sha != candidate_commit_sha:
        raise ValueError(
            f"verification run {run.id} is pinned to {run.candidate_commit_sha}, not "
            f"{candidate_commit_sha}"
        )
    if run.state in {"passed", "failed", "timed_out"}:
        result["verification_state"] = run.state
        return
    if verification_state not in {"passed", "failed", "timed_out"}:
        raise ValueError(f"invalid terminal release verification state {verification_state}")
    normalized_failure_kind = failure_kind
    if verification_state == "timed_out":
        normalized_failure_kind = "timeout"
    elif verification_state == "failed" and normalized_failure_kind is None:
        normalized_failure_kind = "candidate"
    if verification_state == "passed" and normalized_failure_kind is not None:
        raise ValueError("passed release verification cannot have failure_kind")
    if verification_state != "passed" and normalized_failure_kind not in {
        "candidate",
        "infrastructure",
        "timeout",
    }:
        raise ValueError("failed release verification requires a valid failure_kind")

    now = utc_now()
    run.state = verification_state
    run.failure_kind = normalized_failure_kind
    run.command_results = command_results
    run.current_command_index = len(command_results)
    run.error = error
    run.updated_at = now
    run.finished_at = now
    result["verification_state"] = verification_state
    if verification_state != "passed":
        message = error or f"release verification {verification_state}"
        if normalized_failure_kind == "candidate":
            _reject_release(
                state,
                release,
                stage="verification",
                kind="candidate",
                source_id=run.id,
                error=message,
                result=result,
                route_to_team=False,
            )
            _materialize_candidate_verification_correction(
                state,
                release=release,
                run=run,
                error=message,
                result=result,
                now=now,
            )
        else:
            _block_release_gate(
                state,
                release,
                stage="verification",
                kind=("timeout" if normalized_failure_kind == "timeout" else "infrastructure"),
                source_id=run.id,
                error=message,
                result=result,
            )
        return

    release_promotion = _release_promotion(state, release)
    if release_promotion is None:
        _reject_release(
            state,
            release,
            stage="configuration",
            kind="configuration",
            error="release promotion record is missing",
            result=result,
        )
        return
    advance_release_gates(
        state,
        release=release,
        release_promotion=release_promotion,
        result=result,
    )


def _materialize_candidate_verification_correction(
    state: TeamState,
    *,
    release: ReleasePackage,
    run: ReleaseVerificationRun,
    error: str,
    result: dict[str, Any],
    now: datetime,
) -> None:
    """Create candidate-rooted corrective work with complete verifier evidence."""

    project = state.projects.get(release.project_id)
    if project is None:
        return
    failed_command_results = [
        command_result.model_dump(mode="json", exclude_none=True)
        for command_result in run.command_results
        if command_result.state in {"failed", "timed_out"}
    ]
    owner_agent_id, assignment_reason = _candidate_verification_correction_owner(
        state,
        release=release,
        run=run,
        error=error,
    )
    if owner_agent_id is None:
        result["verification_correction_assignment_reason"] = assignment_reason
        return

    task_id = _candidate_verification_correction_task_id(release.id, run.id)
    task = state.tasks.get(task_id)
    description = _candidate_verification_correction_description(
        release=release,
        run=run,
        error=error,
        assignment_reason=assignment_reason,
    )
    if task is None:
        task = AgentTask(
            id=task_id,
            owner_agent_id=owner_agent_id,
            project_id=release.project_id,
            git_base_branch=_candidate_correction_git_base(release),
            priority=0,
            state=TeamTaskState.PENDING,
            task_kind=TeamTaskKind.SUPPORT,
            integration_boundary=False,
            deliverable_contract=TaskDeliverableContract(
                require_git_provenance=True,
            ),
            title=f"Correct verification failure in {release.id}",
            description=description,
            created_by_actor="system:release-verification-correction",
            created_at=now,
            updated_at=now,
            state_entered_at=now,
        )
        state.tasks[task.id] = task
        result.setdefault("created_task_ids", []).append(task.id)
    elif (
        task.project_id != release.project_id
        or task.created_by_actor != "system:release-verification-correction"
    ):
        raise ValueError(
            f"reserved release verification correction task id {task_id} is already in use"
        )
    elif task.state not in _TERMINAL_CANDIDATE_CORRECTION_TASK_STATES:
        _reconcile_candidate_correction_task_git_base(
            state,
            task=task,
            release=release,
            result=result,
            now=now,
        )
        _require_candidate_correction_deliverable(task, now=now)
    run.correction_task_id = task.id

    queue_actionable_inbox_item(
        state,
        recipient_agent_id=task.owner_agent_id,
        sender_agent_id=project.lead_agent_id,
        priority=TeamInboxPriority.HIGH,
        task_id=task.id,
        payload={
            "kind": "release_verification_correction_work",
            "project_id": project.id,
            "release_package_id": release.id,
            "verification_run_id": run.id,
            "task_id": task.id,
            "candidate_commit_sha": run.candidate_commit_sha,
            "candidate_tree_hash": release.candidate_tree_hash,
            "release_branch": release.release_branch,
            "assignment_reason": assignment_reason,
            "summary": (
                "Correct the failed verification on the exact rejected release candidate."
            ),
            "error": error,
            "failed_command_results": failed_command_results,
            "next_action": (
                "Work only in the task worktree rooted at candidate_commit_sha and "
                "candidate_tree_hash. Do not merge or rebase the current project base "
                "branch into this correction. Fix the verifier failure and submit the "
                "changed Git work as the correction deliverable. After that deliverable "
                "is accepted, use it to "
                "create a replacement release that supersedes this rejected package. Do "
                "not retry or resubmit the unchanged candidate. Do not finish this task "
                "directly; its accepted Git deliverable is required to continue the release "
                "loop."
            ),
        },
        result=result,
        now=now,
    )
    result["verification_correction_task_id"] = task.id
    result["verification_correction_task_owner_agent_id"] = task.owner_agent_id
    result["verification_correction_assignment_reason"] = assignment_reason


def _candidate_verification_correction_owner(
    state: TeamState,
    *,
    release: ReleasePackage,
    run: ReleaseVerificationRun,
    error: str,
) -> tuple[str | None, str]:
    project = state.projects.get(release.project_id)
    if project is None:
        return None, "project_missing"

    failure_text = "\n".join(
        value
        for value in [
            error,
            *(
                command_result.log_excerpt or ""
                for command_result in run.command_results
                if command_result.state in {"failed", "timed_out"}
            ),
        ]
        if value
    ).replace("\\", "/").casefold()
    source_owner_ids: set[str] = set()
    manifest = release.manifest
    if manifest is not None and failure_text:
        for evidence in manifest.deliverables:
            review_manifest = evidence.review_manifest
            if review_manifest is None:
                continue
            changed_paths = _verification_manifest_path_names(
                review_manifest.changed_paths
            )
            if not any(path.casefold() in failure_text for path in changed_paths):
                continue
            deliverable = state.deliverables.get(evidence.deliverable_id)
            if deliverable is not None:
                source_owner_ids.add(deliverable.producer_agent_id)
    if len(source_owner_ids) == 1:
        source_owner_id = next(iter(source_owner_ids))
        if _eligible_verification_correction_owner(
            state,
            agent_id=source_owner_id,
            project_id=release.project_id,
        ):
            return source_owner_id, "single_failed_path_source_owner"

    if release.owner_agent_id is not None and _eligible_verification_correction_owner(
        state,
        agent_id=release.owner_agent_id,
        project_id=release.project_id,
    ):
        return release.owner_agent_id, "release_owner"

    lead_agent_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    if _eligible_verification_correction_owner(
        state,
        agent_id=lead_agent_id,
        project_id=project.id,
        allow_project_lead=True,
    ):
        return lead_agent_id, "project_lead_fallback"

    team = state.teams.get(project.team_id)
    manager_agent_id = team.manager_agent_id if team is not None else None
    manager = (
        state.agents.get(manager_agent_id)
        if manager_agent_id is not None
        else None
    )
    if (
        manager is not None
        and manager.status == AgentStatus.ACTIVE
        and manager.team_id == project.team_id
    ):
        return manager.id, "team_manager_fallback"
    return None, "no_eligible_owner"


def _require_candidate_correction_deliverable(
    task: AgentTask,
    *,
    now: datetime,
) -> None:
    """Make an active candidate correction resolve through reviewed Git work.

    Both verification and agent-review rejections need an accepted correction
    deliverable before a successor release may be requested.  Keeping that
    requirement on the task closes the direct-finish terminal route before a
    task can strand the rejected release.
    """

    expected_contract = TaskDeliverableContract(require_git_provenance=True)
    if (
        not task.integration_boundary
        and task.deliverable_contract is not None
        and task.deliverable_contract.require_git_provenance
    ):
        return
    task.integration_boundary = False
    task.deliverable_contract = expected_contract
    task.updated_at = now


def _eligible_verification_correction_owner(
    state: TeamState,
    *,
    agent_id: str,
    project_id: str,
    allow_project_lead: bool = False,
) -> bool:
    agent = state.agents.get(agent_id)
    project = state.projects.get(project_id)
    return bool(
        agent is not None
        and agent.status == AgentStatus.ACTIVE
        and project is not None
        and agent.team_id == project.team_id
        and (
            agent_has_project_assignment(state, agent.id, project_id)
            or (allow_project_lead and project.lead_agent_id == agent.id)
        )
    )


def _verification_manifest_path_names(rows: list[str]) -> set[str]:
    paths: set[str] = set()
    for row in rows:
        fields = row.split("\t")
        for value in fields[1:] if len(fields) > 1 else fields:
            normalized = value.strip().replace("\\", "/").removeprefix("./")
            if normalized:
                paths.add(normalized)
    return paths


def _candidate_verification_correction_task_id(
    release_package_id: str,
    verification_run_id: str,
) -> str:
    token = sha256(
        f"{release_package_id}:{verification_run_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"release-verification-correction-{token}"


def _candidate_verification_correction_description(
    *,
    release: ReleasePackage,
    run: ReleaseVerificationRun,
    error: str,
    assignment_reason: str,
) -> str:
    failed_commands: list[str] = []
    for command_result in run.command_results:
        if command_result.state not in {"failed", "timed_out"}:
            continue
        details = [
            f"state={command_result.state}",
            *(
                [f"exit_code={command_result.exit_code}"]
                if command_result.exit_code is not None
                else []
            ),
            *(
                [f"failure_cause={command_result.failure_cause.value}"]
                if command_result.failure_cause is not None
                else []
            ),
        ]
        failed_commands.append(
            f"- {command_result.name}: {', '.join(details)}"
            + (
                f"\n  log excerpt:\n  {command_result.log_excerpt}"
                if command_result.log_excerpt
                else ""
            )
        )
    command_evidence = "\n".join(failed_commands) or "- No failed command result was recorded."
    return (
        "The release verification pipeline rejected a pinned candidate. Correct the "
        "failure without moving backward across the release boundary.\n\n"
        f"Release package: {release.id}\n"
        f"Verification run: {run.id}\n"
        f"Release branch: {release.release_branch or '<missing>'}\n"
        f"Candidate commit: {run.candidate_commit_sha}\n"
        f"Candidate tree: {release.candidate_tree_hash or '<missing>'}\n"
        f"Assignment reason: {assignment_reason}\n"
        f"Failure: {error}\n\n"
        f"Failed command evidence:\n{command_evidence}\n\n"
        "The task worktree must be created from the exact candidate commit and tree "
        "above. Do not merge or rebase the current project base branch into this "
        "correction. Make only the correction required by the verifier evidence and "
        "submit the changed Git work as the correction deliverable. After acceptance, "
        "create a replacement "
        "release that supersedes the rejected package. Do not retry or resubmit the "
        "unchanged candidate. Do not finish this task directly; its accepted Git "
        "deliverable is required to continue the release loop."
    )


def materialize_release_review_rejection_correction(
    state: TeamState,
    *,
    release: ReleasePackage,
    review: ReviewRequest,
    findings: list[str],
    result: dict[str, Any],
    now: datetime,
) -> None:
    """Create the one candidate-rooted task for a rejected agent release review.

    A release-review rejection is an immutable candidate outcome, just like a
    candidate verification failure.  The task link lives on the release package
    (rather than on an individual review) so concurrent reviewer rejections
    converge on one correction boundary and one source snapshot.
    """

    if review.release_package_id != release.id:
        raise ValueError(
            f"release review {review.id} does not belong to release {release.id}"
        )
    if review.candidate_commit_sha != release.candidate_commit_sha:
        raise ValueError(
            f"release review {review.id} is not pinned to release {release.id} candidate"
        )
    project = state.projects.get(release.project_id)
    if project is None:
        return
    owner_agent_id, assignment_reason = _release_review_correction_owner(
        state,
        release=release,
    )
    if owner_agent_id is None:
        result["release_review_correction_assignment_reason"] = assignment_reason
        return

    task_id = _release_review_correction_task_id(release.id)
    task = state.tasks.get(task_id)
    rejected_reviews = sorted(
        (
            candidate
            for candidate in state.review_requests.values()
            if candidate.release_package_id == release.id
            and candidate.candidate_commit_sha == release.candidate_commit_sha
            and candidate.state == TeamRecordState.REJECTED
            and candidate.superseded_by_review_request_id is None
            and candidate.retired_at is None
        ),
        key=lambda candidate: (candidate.decision_at or candidate.created_at, candidate.id),
    )
    if not any(candidate.id == review.id for candidate in rejected_reviews):
        rejected_reviews.append(review)
    findings_by_review_id = {
        candidate.id: (
            list(findings)
            if candidate.id == review.id
            else list(candidate.findings)
        )
        for candidate in rejected_reviews
    }
    rejected_review_ids = [candidate.id for candidate in rejected_reviews]
    aggregated_findings = [
        finding
        for candidate in rejected_reviews
        for finding in findings_by_review_id[candidate.id]
    ]
    description = _release_review_correction_description(
        release=release,
        reviews=rejected_reviews,
        findings_by_review_id=findings_by_review_id,
        assignment_reason=assignment_reason,
    )
    if task is None:
        task = AgentTask(
            id=task_id,
            owner_agent_id=owner_agent_id,
            project_id=release.project_id,
            git_base_branch=_candidate_correction_git_base(release),
            priority=0,
            state=TeamTaskState.PENDING,
            task_kind=TeamTaskKind.SUPPORT,
            integration_boundary=False,
            deliverable_contract=TaskDeliverableContract(
                require_git_provenance=True,
            ),
            title=f"Correct rejected release review in {release.id}",
            description=description,
            created_by_actor="system:release-review-correction",
            created_at=now,
            updated_at=now,
            state_entered_at=now,
        )
        state.tasks[task.id] = task
        result.setdefault("created_task_ids", []).append(task.id)
    elif (
        task.project_id != release.project_id
        or task.created_by_actor != "system:release-review-correction"
    ):
        raise ValueError(
            f"reserved release review correction task id {task_id} is already in use"
        )
    else:
        if task.state not in _TERMINAL_CANDIDATE_CORRECTION_TASK_STATES:
            _reconcile_candidate_correction_task_git_base(
                state,
                task=task,
                release=release,
                result=result,
                now=now,
            )
            _require_candidate_correction_deliverable(task, now=now)
        task.description = description
        task.updated_at = now

    release.review_correction_task_id = task.id
    existing_inbox = next(
        (
            item
            for item in state.inbox.values()
            if item.task_id == task.id
            and item.payload.get("kind") == "release_review_rejection_correction_work"
            and (
                item.payload.get("review_request_id") == review.id
                or review.id in item.payload.get("review_request_ids", [])
            )
        ),
        None,
    )
    if existing_inbox is None:
        queue_actionable_inbox_item(
            state,
            recipient_agent_id=task.owner_agent_id,
            sender_agent_id=review.reviewer_agent_id,
            priority=TeamInboxPriority.HIGH,
            task_id=task.id,
            payload={
                "kind": "release_review_rejection_correction_work",
                "project_id": project.id,
                "release_package_id": release.id,
                "review_request_id": review.id,
                "review_request_ids": rejected_review_ids,
                "rejected_reviews": [
                    {
                        "review_request_id": candidate.id,
                        "reviewer_agent_id": candidate.reviewer_agent_id,
                        "findings": findings_by_review_id[candidate.id],
                    }
                    for candidate in rejected_reviews
                ],
                "task_id": task.id,
                "candidate_commit_sha": release.candidate_commit_sha,
                "candidate_tree_hash": release.candidate_tree_hash,
                "release_branch": release.release_branch,
                "assignment_reason": assignment_reason,
                "summary": "Correct the rejected release review on the exact candidate.",
                "findings": aggregated_findings,
                "next_action": (
                    "Work only in the task worktree rooted at candidate_commit_sha and "
                    "candidate_tree_hash. Do not merge or rebase the current project base "
                    "branch into this correction. Fix the release-review finding and submit "
                    "the changed Git work as the correction deliverable. After that "
                    "deliverable is accepted, use the accepted "
                    "correction to create a replacement release that supersedes this "
                    "rejected package. Do not retry or resubmit the unchanged candidate. "
                    "Do not finish this task directly; its accepted Git deliverable is "
                    "required to continue the release loop."
                ),
            },
            result=result,
            now=now,
        )
    result["release_review_correction_task_id"] = task.id
    result["release_review_correction_task_owner_agent_id"] = task.owner_agent_id
    result["release_review_correction_assignment_reason"] = assignment_reason


def _release_review_correction_owner(
    state: TeamState,
    *,
    release: ReleasePackage,
) -> tuple[str | None, str]:
    project = state.projects.get(release.project_id)
    if project is None:
        return None, "project_missing"
    if release.owner_agent_id is not None and _eligible_verification_correction_owner(
        state,
        agent_id=release.owner_agent_id,
        project_id=release.project_id,
    ):
        return release.owner_agent_id, "release_owner"

    lead_agent_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    if _eligible_verification_correction_owner(
        state,
        agent_id=lead_agent_id,
        project_id=project.id,
        allow_project_lead=True,
    ):
        return lead_agent_id, "project_lead_fallback"

    team = state.teams.get(project.team_id)
    manager_agent_id = team.manager_agent_id if team is not None else None
    manager = state.agents.get(manager_agent_id) if manager_agent_id is not None else None
    if (
        manager is not None
        and manager.status == AgentStatus.ACTIVE
        and manager.team_id == project.team_id
    ):
        return manager.id, "team_manager_fallback"
    return None, "no_eligible_owner"


def _release_review_correction_task_id(release_package_id: str) -> str:
    token = sha256(release_package_id.encode("utf-8")).hexdigest()[:24]
    return f"release-review-correction-{token}"


def _release_review_correction_description(
    *,
    release: ReleasePackage,
    reviews: list[ReviewRequest],
    findings_by_review_id: dict[str, list[str]],
    assignment_reason: str,
) -> str:
    review_evidence = "\n".join(
        (
            f"- {review.id} (reviewer {review.reviewer_agent_id}):\n"
            + (
                "\n".join(
                    f"  - {finding}"
                    for finding in findings_by_review_id[review.id]
                )
                or "  - Rejected without a textual finding."
            )
        )
        for review in reviews
    ) or (
        "- The release was rejected without a current release-review record."
    )
    return (
        "An agent release review rejected a pinned candidate. Correct the finding without "
        "moving backward across the release boundary.\n\n"
        f"Release package: {release.id}\n"
        f"Release reviews: {', '.join(review.id for review in reviews)}\n"
        f"Release branch: {release.release_branch or '<missing>'}\n"
        f"Candidate commit: {release.candidate_commit_sha or '<missing>'}\n"
        f"Candidate tree: {release.candidate_tree_hash or '<missing>'}\n"
        f"Assignment reason: {assignment_reason}\n\n"
        f"Review findings:\n{review_evidence}\n\n"
        "The task worktree must be created from the exact candidate commit and tree "
        "above. Do not merge or rebase the current project base branch into this "
        "correction. Make only the correction required by the review evidence and submit "
        "the changed Git work as the correction deliverable. After acceptance, create a "
        "replacement release that supersedes the rejected package. Do not retry or "
        "resubmit the unchanged candidate."
    )


def advance_release_gates(
    state: TeamState,
    *,
    release: ReleasePackage,
    result: dict[str, Any],
    release_promotion: GitPromotionRecord | None = None,
) -> None:
    """Idempotently create only the next missing release gate in policy order."""

    manifest = release.manifest
    if manifest is None or release.candidate_commit_sha is None:
        _reject_release(
            state,
            release,
            stage="configuration",
            kind="configuration",
            error="release gate configuration is incomplete",
            result=result,
        )
        return
    if release.state in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}:
        return
    if manifest.verification_commands:
        current_run = _current_verification_run(state, release)
        if current_run is None or current_run.state != "passed":
            return
    promotion = release_promotion or _release_promotion(state, release)
    if promotion is None:
        _reject_release(
            state,
            release,
            stage="configuration",
            kind="configuration",
            error="release promotion record is missing",
            result=result,
        )
        return
    create_release_reviews_or_approve(
        state,
        release=release,
        release_promotion=promotion,
        result=result,
    )


def create_release_reviews_or_approve(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    project = state.projects.get(release.project_id)
    manifest = release.manifest
    candidate = release.candidate_commit_sha
    if project is None or manifest is None or candidate is None:
        _reject_release(
            state,
            release,
            stage="configuration",
            kind="configuration",
            error="release gate configuration is incomplete",
            result=result,
        )
        return

    for requirement in manifest.review_requirements:
        existing = next(
            (
                state.review_requests[review_id]
                for review_id in reversed(release.review_request_ids)
                if review_id in state.review_requests
                and state.review_requests[review_id].superseded_by_review_request_id is None
                and state.review_requests[review_id].reviewer_agent_id
                == requirement.reviewer_agent_id
                and state.review_requests[review_id].review_profile_id
                == requirement.review_profile_id
            ),
            None,
        )
        if existing is not None:
            continue
        reviewer = state.agents.get(requirement.reviewer_agent_id)
        profile = state.review_profiles.get(requirement.review_profile_id)
        scope_error = _release_review_scope_error(
            project=project,
            reviewer=reviewer,
            profile=profile,
            reviewer_agent_id=requirement.reviewer_agent_id,
            review_profile_id=requirement.review_profile_id,
        )
        if scope_error is not None:
            _reject_release(
                state,
                release,
                stage="configuration",
                kind="configuration",
                error=scope_error,
                result=result,
            )
            return
        now = utc_now()
        review = _create_release_review(
            state,
            release=release,
            reviewer=reviewer,
            profile=profile,
            candidate_commit_sha=candidate,
            instructions=(
                f"Review release candidate {candidate} for release package {release.id}. "
                "Decide only the pinned candidate commit."
            ),
            result=result,
            now=now,
        )
        _queue_release_review_handoff(
            state,
            release=release,
            project=project,
            reviewer=reviewer,
            profile=profile,
            review=review,
            result=result,
            now=now,
        )

    current_reviews = _current_release_reviews(state, release)
    if any(
        review.state != TeamRecordState.ACCEPTED
        for review in current_reviews
    ):
        release.updated_at = utc_now()
        return
    human_request = ensure_human_review_request(
        state,
        release=release,
        result=result,
    )
    if human_request is not None and human_request.state != "approved":
        release.updated_at = utc_now()
        return
    approve_release_candidate(
        state,
        release=release,
        release_promotion=release_promotion,
        result=result,
    )


def approve_release_candidate(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    candidate = release.candidate_commit_sha
    if candidate is None:
        _reject_release(
            state,
            release,
            stage="configuration",
            kind="configuration",
            error="release candidate commit is missing",
            result=result,
        )
        return
    if release.manifest is not None and (
        release.manifest.human_review_requirement is not None
        or release.manifest.carried_human_findings
    ):
        request = human_review_request_for_release(state, release.id)
        decisions = [
            decision
            for decision in state.human_review_decisions.values()
            if request is not None
            and decision.human_review_request_id == request.id
            and decision.decision == "approved"
        ]
        if (
            request is None
            or request.state != "approved"
            or request.candidate_commit_sha != candidate
            or request.candidate_tree_hash != release.candidate_tree_hash
            or not decisions
        ):
            raise ValueError(
                f"release {release.id} cannot be approved before its pinned "
                "human review accepts"
            )
    release.gate_failure = None
    release.approved_commit_sha = candidate
    release.updated_at = utc_now()
    from .git_promotion import _queue_canonical_push_after_release_success

    result.setdefault("events", [])
    result.setdefault("git_promotion_event_ids", [])
    _queue_canonical_push_after_release_success(
        state,
        release=release,
        release_promotion=release_promotion,
        result=result,
    )


def _release_promotion(
    state: TeamState,
    release: ReleasePackage,
) -> GitPromotionRecord | None:
    matches = [
        state.git_promotions[promotion_id]
        for promotion_id in release.git_promotion_record_ids
        if promotion_id in state.git_promotions
        and state.git_promotions[promotion_id].promotion_kind == "release"
        and state.git_promotions[promotion_id].state == "succeeded"
    ]
    matches.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
    return matches[0] if matches else None


def retry_release_gate(
    state: TeamState,
    *,
    release: ReleasePackage,
    gate_kind: str,
    gate_id: str,
    reason: str,
    requested_by: str,
    result: dict[str, Any],
) -> None:
    if release.state != TeamRecordState.BLOCKED:
        raise ValueError(f"release {release.id} is not blocked")
    candidate = release.candidate_commit_sha
    if candidate is None:
        raise ValueError(f"release {release.id} has no pinned candidate")
    normalized_reason = _required_text(reason, "reason")
    normalized_requested_by = _required_text(requested_by, "requested_by")
    if gate_kind == "verification":
        current = _current_verification_run(state, release)
        if current is None or current.id != gate_id:
            raise ValueError("verification retry must target the current verification run")
        if current.state not in {"failed", "timed_out"} or current.failure_kind not in {
            "infrastructure",
            "timeout",
        }:
            raise ValueError("only infrastructure-failed or timed-out verification may be retried")
        if normalized_requested_by.startswith("agent:") and (
            current.retry_requested_by or ""
        ).startswith("agent:"):
            raise ValueError(
                "an agent-requested verification retry already failed against the "
                "unchanged candidate; do not retry it again as an agent. Escalate the "
                "current run and its concrete infrastructure recovery requirement to "
                "an operator"
            )
        verification_commands = release.manifest.verification_commands
        if not verification_commands:
            raise ValueError("release manifest has no verification commands")
        attempt_number = (
            max(
                (
                    state.release_verification_runs[run_id].attempt_number
                    for run_id in release.verification_run_ids
                    if run_id in state.release_verification_runs
                ),
                default=0,
            )
            + 1
        )
        run = ReleaseVerificationRun(
            id=_verification_run_id(release.id, candidate, attempt_number),
            release_package_id=release.id,
            candidate_commit_sha=candidate,
            session_id=f"team-release-verification-{uuid4().hex}",
            attempt_number=attempt_number,
            supersedes_verification_run_id=current.id,
            retry_reason=normalized_reason,
            retry_requested_by=normalized_requested_by,
            command_results=[
                ReleaseVerificationCommandResult(name=command.name)
                for command in verification_commands
            ],
        )
        current.superseded_by_verification_run_id = run.id
        state.release_verification_runs[run.id] = run
        release.verification_run_ids = _append_unique(release.verification_run_ids, run.id)
        _recompute_release_state_after_gate_retry(state, release)
        resolve_active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
            source_id=release.id,
            actor_agent_id=None,
            dispatch_id=None,
            note=f"Verification retry {run.id} was scheduled.",
            reference_ids=[run.id],
            result=result,
        )
        _append_verification_requested_event(result, release=release, run=run)
        result.setdefault("release_verification_run_ids", []).append(run.id)
        result["retried_gate_id"] = current.id
        result["replacement_gate_id"] = run.id
        result["retry_reason"] = normalized_reason
        result["retry_requested_by"] = normalized_requested_by
        return
    if gate_kind != "review":
        raise ValueError("gate_kind must be verification or review")
    current_reviews = _current_release_reviews(state, release)
    current = next((review for review in current_reviews if review.id == gate_id), None)
    if current is None:
        raise ValueError("review retry must target a current release review")
    if current.state != TeamRecordState.BLOCKED:
        raise ValueError("only a blocked release review may be retried")
    project = state.projects.get(release.project_id)
    profile = (
        state.review_profiles.get(current.review_profile_id)
        if current.review_profile_id is not None
        else None
    )
    reviewer = state.agents.get(current.reviewer_agent_id)
    if project is None:
        raise ValueError("release reviewer or review profile is unavailable")
    scope_error = _release_review_scope_error(
        project=project,
        reviewer=reviewer,
        profile=profile,
        reviewer_agent_id=current.reviewer_agent_id,
        review_profile_id=current.review_profile_id or "",
    )
    if scope_error is not None:
        raise ValueError(scope_error)
    now = utc_now()
    review = _create_release_review(
        state,
        release=release,
        reviewer=reviewer,
        profile=profile,
        candidate_commit_sha=candidate,
        supersedes=current,
        retry_reason=normalized_reason,
        retry_requested_by=normalized_requested_by,
        instructions=(
            f"Retry blocked review {current.id} for unchanged release candidate {candidate}. "
            f"Retry reason: {normalized_reason}"
        ),
        result=result,
        now=now,
    )
    _recompute_release_state_after_gate_retry(state, release)
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
        source_id=current.id,
        actor_agent_id=None,
        dispatch_id=None,
        note=f"Review retry {review.id} was scheduled.",
        reference_ids=[review.id],
        result=result,
    )
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=release.id,
        actor_agent_id=None,
        dispatch_id=None,
        note=f"Review retry {review.id} was scheduled.",
        reference_ids=[review.id],
        result=result,
    )
    _queue_release_review_handoff(
        state,
        release=release,
        project=project,
        reviewer=reviewer,
        profile=profile,
        review=review,
        result=result,
        now=now,
    )
    result["retried_gate_id"] = current.id
    result["replacement_gate_id"] = review.id
    result["retry_reason"] = normalized_reason
    result["retry_requested_by"] = normalized_requested_by


def _candidate_manifest(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
) -> ReleaseCandidateManifest:
    project = state.projects[release.project_id]
    policy = project.release_policy
    if policy is None or release.candidate_commit_sha is None:
        raise ValueError("release candidate policy or commit is missing")
    require_release_scope(
        state,
        project_id=release.project_id,
        deliverable_ids=release.deliverable_ids,
        allowed_parent_task_ids=allowed_release_parent_task_ids(
            state,
            project_id=release.project_id,
            predecessor_release_package_id=(
                release.supersedes_release_package_id
            ),
        ),
    )
    waivers = {waiver.deliverable_id: waiver for waiver in release.verification_waivers}
    deliverable_rows: list[ReleaseCandidateDeliverableEvidence] = []
    for deliverable_id in release.deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            raise ValueError(f"release deliverable {deliverable_id} is missing")
        accepted_reviews = [
            state.review_requests[review_id]
            for review_id in deliverable.review_request_ids
            if review_id in state.review_requests
            and state.review_requests[review_id].state == TeamRecordState.ACCEPTED
            and state.review_requests[review_id].superseded_by_review_request_id is None
            and state.review_requests[review_id].retired_at is None
        ]
        review_evidence = [
            ReleaseCandidateReviewEvidence(
                review_request_id=review.id,
                reviewer_agent_id=review.reviewer_agent_id,
                review_profile_id=review.review_profile_id,
                reviewed_commit_sha=_required_text(
                    review.reviewed_commit_sha,
                    f"review {review.id} reviewed_commit_sha",
                ),
                criteria=list(review.criteria),
            )
            for review in accepted_reviews
        ]
        reviewed_shas = {review.reviewed_commit_sha for review in review_evidence}
        if (
            deliverable.review_manifest is not None
            and reviewed_shas
            and deliverable.review_manifest.reviewed_commit_sha not in reviewed_shas
        ):
            raise ValueError(
                f"deliverable {deliverable.id} reviewed manifest commit "
                f"{deliverable.review_manifest.reviewed_commit_sha} does not match its "
                "accepted immutable review"
            )
        deliverable_rows.append(
            ReleaseCandidateDeliverableEvidence(
                deliverable_id=deliverable.id,
                artifact_refs=list(deliverable.artifact_refs),
                verification=deliverable.verification,
                verification_waiver=waivers.get(deliverable.id),
                accepted_reviews=review_evidence,
                review_manifest=deliverable.review_manifest,
                feature_branch=deliverable.git_refs.feature_branch,
                feature_commit_sha=deliverable.git_refs.feature_commit_sha,
            )
        )
    predecessor = state.release_packages.get(
        release.supersedes_release_package_id or ""
    )
    predecessor_manifest = predecessor.manifest if predecessor is not None else None
    human_requirement = policy.human_review_requirement
    human_snapshot = (
        HumanReviewRequirementSnapshot(
            directory_path=(
                human_requirement.directory_path
                or (project.git.directory_path if project.git is not None else "root")
            ),
            configured_group_ids=list(human_requirement.reviewer_group_ids),
            initially_eligible_user_ids=list(
                human_requirement.resolved_reviewer_user_ids
            ),
            checklist=[
                item.model_copy(deep=True)
                for item in human_requirement.checklist
            ],
            test_instructions=human_requirement.test_instructions,
            preparation_mode=human_requirement.preparation_mode,
        )
        if human_requirement is not None
        else None
    )
    predecessor_deliverable_ids = set(
        predecessor.deliverable_ids if predecessor is not None else []
    )
    correction_deliverable_ids = [
        deliverable_id
        for deliverable_id in release.deliverable_ids
        if deliverable_id not in predecessor_deliverable_ids
    ]
    predecessor_lineage_ids = _predecessor_release_package_ids(state, release)
    predecessor_requests = [
        request
        for request in state.human_review_requests.values()
        if request.release_package_id in predecessor_lineage_ids
    ]
    predecessor_request_ids = {request.id for request in predecessor_requests}
    carried_findings = [
        HumanReviewFindingSnapshot(
            finding_id=finding.id,
            text=finding.text,
            target_deliverable_ids=list(finding.target_deliverable_ids),
            correction_task_id=finding.correction_task_id,
            proposed_resolution=finding.proposed_resolution,
            resolution_evidence_refs=list(finding.resolution_evidence_refs),
            proposed_by_deliverable_ids=list(
                finding.proposed_by_deliverable_ids
            ),
        )
        for finding in sorted(
            state.human_review_findings.values(),
            key=lambda item: item.id,
        )
        if finding.source_human_review_request_id in predecessor_request_ids
        and finding.state == "open"
    ]
    if human_snapshot is None and carried_findings:
        if predecessor_manifest is not None and (
            predecessor_manifest.human_review_requirement is not None
        ):
            human_snapshot = (
                predecessor_manifest.human_review_requirement.model_copy(deep=True)
            )
        elif predecessor_requests:
            human_snapshot = predecessor_requests[-1].requirement_snapshot.model_copy(
                deep=True
            )
    lineage = deliverable_source_lineage(state, release.deliverable_ids)
    if not lineage["valid"]:
        raise ValueError("release candidate contribution graph is invalid")
    return ReleaseCandidateManifest(
        project_id=project.id,
        release_package_id=release.id,
        base_commit_sha=release.base_commit_sha,
        candidate_commit_sha=release.candidate_commit_sha,
        candidate_tree_hash=release.candidate_tree_hash,
        release_branch=release.release_branch or release_promotion.target_branch,
        deliverables=deliverable_rows,
        scope_validation=(
            release.scope_validation.model_copy(deep=True)
            if release.scope_validation is not None
            else None
        ),
        verification_commands=list(policy.verification_commands),
        review_requirements=list(policy.review_requirements),
        human_review_requirement=human_snapshot,
        predecessor_release_package_id=release.supersedes_release_package_id,
        correction_deliverable_ids=correction_deliverable_ids,
        carried_human_findings=carried_findings,
        contribution_graph=lineage["graph"],
    )


def _predecessor_release_package_ids(
    state: TeamState,
    release: ReleasePackage,
) -> set[str]:
    """Return every predecessor of a successor attempt, guarding malformed history."""

    predecessor_ids: set[str] = set()
    seen_ids = {release.id}
    predecessor_id = release.supersedes_release_package_id
    while predecessor_id is not None and predecessor_id not in seen_ids:
        predecessor_ids.add(predecessor_id)
        seen_ids.add(predecessor_id)
        predecessor = state.release_packages.get(predecessor_id)
        if predecessor is None:
            break
        predecessor_id = predecessor.supersedes_release_package_id
    return predecessor_ids


def _unchanged_failed_release(
    state: TeamState,
    *,
    release: ReleasePackage,
    candidate_commit_sha: str,
    candidate_tree_hash: str | None,
) -> ReleasePackage | None:
    deliverables = tuple(sorted(set(release.deliverable_ids)))
    candidates = [
        candidate
        for candidate in state.release_packages.values()
        if candidate.id != release.id
        and candidate.project_id == release.project_id
        and candidate.state == TeamRecordState.REJECTED
        and candidate.gate_failure is not None
        and candidate.gate_failure.kind in {"candidate", "rejected"}
        and tuple(sorted(set(candidate.deliverable_ids))) == deliverables
        and (
            (
                candidate_tree_hash is not None
                and candidate.candidate_tree_hash == candidate_tree_hash
            )
            or (
                candidate_tree_hash is None
                and candidate.candidate_commit_sha == candidate_commit_sha
            )
        )
    ]
    candidates.sort(key=lambda item: (item.updated_at or item.created_at, item.id), reverse=True)
    return candidates[0] if candidates else None


def _current_verification_run(
    state: TeamState,
    release: ReleasePackage,
) -> ReleaseVerificationRun | None:
    runs = [
        state.release_verification_runs[run_id]
        for run_id in release.verification_run_ids
        if run_id in state.release_verification_runs
        and state.release_verification_runs[run_id].superseded_by_verification_run_id is None
    ]
    runs.sort(key=lambda run: (run.attempt_number, run.created_at, run.id), reverse=True)
    return runs[0] if runs else None


def _current_release_reviews(state: TeamState, release: ReleasePackage) -> list[ReviewRequest]:
    return [
        state.review_requests[review_id]
        for review_id in release.review_request_ids
        if review_id in state.review_requests
        and state.review_requests[review_id].superseded_by_review_request_id is None
    ]


def current_release_gates_passed(state: TeamState, release: ReleasePackage) -> bool:
    current_run = _current_verification_run(state, release)
    verification_passed = current_run is None or current_run.state == "passed"
    reviews = _current_release_reviews(state, release)
    reviews_accepted = all(review.state == TeamRecordState.ACCEPTED for review in reviews)
    human_required = bool(
        release.manifest is not None
        and (
            release.manifest.human_review_requirement is not None
            or release.manifest.carried_human_findings
        )
    )
    human_request = human_review_request_for_release(state, release.id)
    human_accepted = not human_required or (
        human_request is not None and human_request.state == "approved"
    )
    return verification_passed and reviews_accepted and human_accepted


def _append_verification_requested_event(
    result: dict[str, Any],
    *,
    release: ReleasePackage,
    run: ReleaseVerificationRun,
) -> None:
    result.setdefault("events", []).append(
        {
            "event_type": RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE,
            "correlation_key": run.id,
            "idempotency_key": (f"team-addon:{RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE}:{run.id}"),
            "payload": {
                "release_package_id": release.id,
                "verification_run_id": run.id,
                "candidate_commit_sha": run.candidate_commit_sha,
            },
        }
    )


def _recompute_release_state_after_gate_retry(
    state: TeamState,
    release: ReleasePackage,
) -> None:
    blocked_reviews = [
        review
        for review in _current_release_reviews(state, release)
        if review.state == TeamRecordState.BLOCKED
    ]
    if blocked_reviews:
        existing_source_id = (
            release.gate_failure.source_id if release.gate_failure is not None else None
        )
        blocker = next(
            (review for review in blocked_reviews if review.id == existing_source_id),
            blocked_reviews[0],
        )
        message = "; ".join(blocker.findings) or f"release review {blocker.id} blocked"
        release.state = TeamRecordState.BLOCKED
        release.gate_failure = ReleaseGateFailure(
            stage="review",
            kind="blocked",
            source_id=blocker.id,
            message=message,
        )
        release.error = message
        release.updated_at = utc_now()
        return

    release.state = TeamRecordState.PENDING
    release.gate_failure = None
    release.error = None
    release.updated_at = utc_now()


def _verification_run_id(
    release_id: str,
    candidate_commit_sha: str,
    attempt_number: int,
) -> str:
    return f"release-verification-{release_id}-{candidate_commit_sha[:16]}-attempt-{attempt_number}"


def _block_release_gate(
    state: TeamState,
    release: ReleasePackage,
    *,
    stage: str,
    kind: str,
    error: str,
    result: dict[str, Any],
    source_id: str | None = None,
) -> None:
    release.state = TeamRecordState.BLOCKED
    release.gate_failure = ReleaseGateFailure(
        stage=stage,
        kind=kind,
        source_id=source_id,
        message=error,
    )
    release.error = error
    release.updated_at = utc_now()
    result["error"] = error
    blocker_details = error
    if stage == "verification" and kind in {"infrastructure", "timeout"}:
        current = _current_verification_run(state, release)
        if current is not None and (current.retry_requested_by or "").startswith("agent:"):
            blocker_details = (
                f"{error} An agent-requested retry already failed against this unchanged "
                "candidate. Do not schedule another agent retry; escalate the run evidence "
                "and concrete infrastructure recovery requirement to an operator."
            )
        else:
            blocker_details = (
                f"{error} Retry the unchanged candidate only after concrete evidence that "
                "the infrastructure failure recovered; cite what changed. If no such "
                "evidence exists, escalate the blocker instead of retrying."
            )
    open_release_blocker(
        state,
        release_package_id=release.id,
        summary=f"Release {release.id} gate is blocked",
        details=blocker_details,
        result=result,
        now=release.updated_at,
    )


def _reject_release(
    state: TeamState,
    release: ReleasePackage,
    *,
    stage: str,
    kind: str,
    error: str,
    result: dict[str, Any],
    source_id: str | None = None,
    route_to_team: bool = True,
) -> None:
    release.state = TeamRecordState.REJECTED
    release.gate_failure = ReleaseGateFailure(
        stage=stage,
        kind=kind,
        source_id=source_id,
        message=error,
    )
    release.error = error
    release.updated_at = utc_now()
    result["error"] = error
    if not route_to_team:
        return
    project = state.projects.get(release.project_id)
    if project is None:
        result["release_remediation_routing_error"] = "project_missing"
        return
    owner_agent_id = release.owner_agent_id or (
        project_lead_agent_id(state, project.id) or project.lead_agent_id
    )
    blocker, _created = open_or_get_blocker(
        state,
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=release.id,
        reason_code=f"release_gate_{stage}_{kind}",
        summary=f"Release {release.id} was rejected by its {stage} gate",
        details=(
            f"{error} Inspect the rejected release, its frozen gate evidence, and the "
            "current project release configuration. Correct the condition through "
            "supported Team actions and create a replacement release. If the condition "
            "requires configuration or invariant repair outside the team's authority, "
            "escalate it to an operator instead of retrying unchanged input."
        ),
        priority=TeamInboxPriority.HIGH,
        reporter_actor="system:release-gate",
        initial_owner_agent_id=owner_agent_id,
        team_id=project.team_id,
        project_id=project.id,
        result=result,
        now=release.updated_at,
    )
    result["release_remediation_blocker_id"] = blocker.id
    result["release_remediation_owner_agent_id"] = blocker.owner_agent_id


__all__ = [
    "RELEASE_VERIFICATION_REQUESTED_EVENT_TYPE",
    "advance_release_gates",
    "approve_release_candidate",
    "begin_release_gates",
    "create_release_reviews_or_approve",
    "materialize_release_review_rejection_correction",
    "current_release_gates_passed",
    "record_release_verification_result",
    "retry_release_gate",
]
