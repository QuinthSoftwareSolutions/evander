from __future__ import annotations

import re
from dataclasses import dataclass

from ...models import (
    DependencyGitSource,
    TeamRecordState,
    TeamState,
)


_FULL_OBJECT_ID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


@dataclass(frozen=True)
class HumanReviewCorrectionGitSource:
    human_review_finding_id: str
    human_review_request_id: str
    release_package_id: str
    release_branch: str
    candidate_commit_sha: str
    candidate_tree_hash: str
    dependency_git_sources: tuple[DependencyGitSource, ...]


@dataclass(frozen=True)
class ReleaseVerificationCorrectionGitSource:
    release_verification_run_id: str
    release_package_id: str
    release_branch: str
    candidate_commit_sha: str
    candidate_tree_hash: str
    dependency_git_sources: tuple[DependencyGitSource, ...]


@dataclass(frozen=True)
class ReleaseReviewCorrectionGitSource:
    release_review_request_id: str
    release_package_id: str
    release_branch: str
    candidate_commit_sha: str
    candidate_tree_hash: str
    dependency_git_sources: tuple[DependencyGitSource, ...]


@dataclass(frozen=True)
class RejectedDeliverableContinuationGitSource:
    """Immutable same-task Git source retained for a replacement deliverable."""

    deliverable_id: str
    agent_branch: str
    base_commit_sha: str
    commit_sha: str
    pinned_ref: str


RejectedCandidateGitSource = (
    HumanReviewCorrectionGitSource
    | ReleaseVerificationCorrectionGitSource
    | ReleaseReviewCorrectionGitSource
)


def rejected_deliverable_continuation_git_source(
    state: TeamState,
    *,
    task_id: str,
) -> RejectedDeliverableContinuationGitSource | None:
    """Return the rejected Git candidate that same-task rework must retain."""

    task = state.tasks.get(task_id)
    if task is None:
        return None
    matches = sorted(
        (
            deliverable
            for deliverable_id in task.deliverable_ids
            if (deliverable := state.deliverables.get(deliverable_id)) is not None
            and deliverable.task_id == task.id
            and deliverable.state == TeamRecordState.REJECTED
            and deliverable.superseded_by_deliverable_id is None
        ),
        key=lambda deliverable: deliverable.id,
    )
    git_matches: list[RejectedDeliverableContinuationGitSource] = []
    for deliverable in matches:
        git_refs = deliverable.git_refs
        git_values = (
            git_refs.agent_branch,
            git_refs.commit_sha,
            git_refs.submitted_commit_sha,
            git_refs.accepted_pinned_ref,
        )
        if not any(git_values):
            continue
        branch = git_refs.agent_branch
        commit_sha = git_refs.submitted_commit_sha or git_refs.commit_sha
        branch_context = state.git_work_contexts.get(git_refs.branch_context_id or "")
        base_commit_sha = (
            branch_context.base_commit
            if branch_context is not None and branch_context.task_id == task.id
            else None
        )
        if branch is None or commit_sha is None or base_commit_sha is None:
            raise ValueError(f"rejected deliverable {deliverable.id} has partial Git provenance")
        if git_refs.commit_sha is not None and git_refs.commit_sha.casefold() != (
            commit_sha.casefold()
        ):
            raise ValueError(
                f"rejected deliverable {deliverable.id} records conflicting Git commits"
            )
        normalized_commit = commit_sha.casefold()
        normalized_base_commit = base_commit_sha.casefold()
        if _FULL_OBJECT_ID_RE.fullmatch(normalized_commit) is None:
            raise ValueError(
                f"rejected deliverable {deliverable.id} commit is not a full Git object ID"
            )
        if _FULL_OBJECT_ID_RE.fullmatch(normalized_base_commit) is None:
            raise ValueError(
                f"rejected deliverable {deliverable.id} base is not a full Git object ID"
            )
        pinned_ref = git_refs.accepted_pinned_ref
        if pinned_ref is None:
            raise ValueError(
                f"rejected Git deliverable {deliverable.id} has no immutable candidate ref"
            )
        git_matches.append(
            RejectedDeliverableContinuationGitSource(
                deliverable_id=deliverable.id,
                agent_branch=branch,
                base_commit_sha=normalized_base_commit,
                commit_sha=normalized_commit,
                pinned_ref=pinned_ref,
            )
        )
    if len(git_matches) > 1:
        raise ValueError(
            f"task {task_id} has multiple unsuperseded rejected Git deliverables: "
            + ", ".join(source.deliverable_id for source in git_matches)
        )
    return git_matches[0] if git_matches else None


def is_human_review_correction_task(
    state: TeamState,
    *,
    task_id: str,
) -> bool:
    return any(
        finding.correction_task_id == task_id for finding in state.human_review_findings.values()
    )


def human_review_correction_git_source(
    state: TeamState,
    *,
    task_id: str,
) -> HumanReviewCorrectionGitSource | None:
    """Return the immutable rejected-candidate source for a correction task.

    Human-review findings are the durable link between a correction task and
    the exact request/release that rejected the candidate.  The task's
    ``originating_task_id`` and dependency list are intentionally not used to
    infer the base because they describe task lineage and ordering, not an
    immutable Git snapshot.
    """

    matches = sorted(
        (
            finding
            for finding in state.human_review_findings.values()
            if finding.correction_task_id == task_id
        ),
        key=lambda finding: finding.id,
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(
            f"task {task_id} is linked to multiple human review findings: "
            + ", ".join(finding.id for finding in matches)
        )
    finding = matches[0]
    request = state.human_review_requests.get(finding.source_human_review_request_id)
    if request is None:
        raise ValueError(
            f"human review finding {finding.id} references missing request "
            f"{finding.source_human_review_request_id}"
        )
    if request.state != "changes_requested":
        raise ValueError(
            f"human review correction task {task_id} references request "
            f"{request.id} in state {request.state}"
        )
    release = state.release_packages.get(request.release_package_id)
    if release is None:
        raise ValueError(
            f"human review request {request.id} references missing release "
            f"{request.release_package_id}"
        )
    task = state.tasks.get(task_id)
    if task is None:
        raise ValueError(f"human review correction task {task_id} does not exist")
    if release.project_id != task.project_id:
        raise ValueError(
            f"human review correction task {task_id} belongs to project "
            f"{task.project_id}, not rejected release project {release.project_id}"
        )
    manifest = release.manifest
    if manifest is None or manifest.evidence_status != "complete":
        raise ValueError(f"rejected release {release.id} has no complete candidate manifest")

    candidate_commit_sha = request.candidate_commit_sha.casefold()
    candidate_tree_hash = request.candidate_tree_hash.casefold()
    release_branch = release.release_branch or manifest.release_branch
    if not release_branch:
        raise ValueError(f"rejected release {release.id} has no release branch")
    if (
        release.candidate_commit_sha is None
        or release.candidate_commit_sha.casefold() != candidate_commit_sha
        or manifest.candidate_commit_sha.casefold() != candidate_commit_sha
    ):
        raise ValueError(
            f"human review request {request.id} does not match rejected release "
            f"{release.id} candidate commit"
        )
    if (
        release.candidate_tree_hash is None
        or release.candidate_tree_hash.casefold() != candidate_tree_hash
        or manifest.candidate_tree_hash is None
        or manifest.candidate_tree_hash.casefold() != candidate_tree_hash
    ):
        raise ValueError(
            f"human review request {request.id} does not match rejected release "
            f"{release.id} candidate tree"
        )
    if manifest.release_branch != release_branch:
        raise ValueError(
            f"rejected release {release.id} has inconsistent release branch provenance"
        )
    for field_name, value in (
        ("candidate commit", candidate_commit_sha),
        ("candidate tree", candidate_tree_hash),
    ):
        if _FULL_OBJECT_ID_RE.fullmatch(value) is None:
            raise ValueError(f"rejected release {release.id} records an invalid {field_name}")

    dependency_sources: list[DependencyGitSource] = []
    for evidence in sorted(
        manifest.deliverables,
        key=lambda item: item.deliverable_id,
    ):
        deliverable = state.deliverables.get(evidence.deliverable_id)
        if deliverable is None:
            raise ValueError(
                f"rejected release {release.id} manifest references missing "
                f"deliverable {evidence.deliverable_id}"
            )
        if deliverable.state != TeamRecordState.ACCEPTED:
            raise ValueError(
                f"rejected release {release.id} source deliverable "
                f"{deliverable.id} is no longer accepted"
            )
        source_task = state.tasks.get(deliverable.task_id)
        if source_task is None or source_task.project_id != release.project_id:
            raise ValueError(
                f"rejected release {release.id} source deliverable "
                f"{deliverable.id} has invalid task provenance"
            )
        feature_branch = evidence.feature_branch
        feature_commit_sha = evidence.feature_commit_sha
        if feature_branch is None and feature_commit_sha is None:
            continue
        if feature_branch is None or feature_commit_sha is None:
            raise ValueError(
                f"rejected release {release.id} source deliverable "
                f"{deliverable.id} has partial Git provenance"
            )
        dependency_sources.append(
            DependencyGitSource(
                dependency_task_id=deliverable.task_id,
                deliverable_id=deliverable.id,
                branch=feature_branch,
                commit_sha=feature_commit_sha,
            )
        )

    return HumanReviewCorrectionGitSource(
        human_review_finding_id=finding.id,
        human_review_request_id=request.id,
        release_package_id=release.id,
        release_branch=release_branch,
        candidate_commit_sha=candidate_commit_sha,
        candidate_tree_hash=candidate_tree_hash,
        dependency_git_sources=tuple(dependency_sources),
    )


def is_release_verification_correction_task(
    state: TeamState,
    *,
    task_id: str,
) -> bool:
    return any(
        run.correction_task_id == task_id for run in state.release_verification_runs.values()
    )


def release_verification_correction_git_source(
    state: TeamState,
    *,
    task_id: str,
) -> ReleaseVerificationCorrectionGitSource | None:
    """Return the immutable failed candidate source for verifier correction work."""

    matches = sorted(
        (
            run
            for run in state.release_verification_runs.values()
            if run.correction_task_id == task_id
        ),
        key=lambda run: run.id,
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(
            f"task {task_id} is linked to multiple release verification runs: "
            + ", ".join(run.id for run in matches)
        )
    run = matches[0]
    if run.state != "failed" or run.failure_kind != "candidate":
        raise ValueError(
            f"release verification correction task {task_id} references run "
            f"{run.id} in state {run.state}/{run.failure_kind or 'none'}"
        )
    release = state.release_packages.get(run.release_package_id)
    if release is None:
        raise ValueError(
            f"release verification run {run.id} references missing release {run.release_package_id}"
        )
    task = state.tasks.get(task_id)
    if task is None:
        raise ValueError(f"release verification correction task {task_id} does not exist")
    if release.project_id != task.project_id:
        raise ValueError(
            f"release verification correction task {task_id} belongs to project "
            f"{task.project_id}, not rejected release project {release.project_id}"
        )
    if (
        release.state != TeamRecordState.REJECTED
        or release.gate_failure is None
        or release.gate_failure.stage != "verification"
        or release.gate_failure.kind != "candidate"
        or release.gate_failure.source_id != run.id
    ):
        raise ValueError(
            f"release verification correction task {task_id} does not reference "
            f"the active candidate failure on release {release.id}"
        )
    manifest = release.manifest
    if manifest is None or manifest.evidence_status != "complete":
        raise ValueError(f"rejected release {release.id} has no complete candidate manifest")

    candidate_commit_sha = run.candidate_commit_sha.casefold()
    candidate_tree_hash = (
        release.candidate_tree_hash.casefold() if release.candidate_tree_hash is not None else ""
    )
    release_branch = release.release_branch or manifest.release_branch
    if not release_branch:
        raise ValueError(f"rejected release {release.id} has no release branch")
    if (
        release.candidate_commit_sha is None
        or release.candidate_commit_sha.casefold() != candidate_commit_sha
        or manifest.candidate_commit_sha.casefold() != candidate_commit_sha
    ):
        raise ValueError(
            f"release verification run {run.id} does not match rejected release "
            f"{release.id} candidate commit"
        )
    if (
        not candidate_tree_hash
        or manifest.candidate_tree_hash is None
        or manifest.candidate_tree_hash.casefold() != candidate_tree_hash
    ):
        raise ValueError(
            f"release verification run {run.id} does not match rejected release "
            f"{release.id} candidate tree"
        )
    if manifest.release_branch != release_branch:
        raise ValueError(
            f"rejected release {release.id} has inconsistent release branch provenance"
        )
    for field_name, value in (
        ("candidate commit", candidate_commit_sha),
        ("candidate tree", candidate_tree_hash),
    ):
        if _FULL_OBJECT_ID_RE.fullmatch(value) is None:
            raise ValueError(f"rejected release {release.id} records an invalid {field_name}")

    dependency_sources: list[DependencyGitSource] = []
    for evidence in sorted(
        manifest.deliverables,
        key=lambda item: item.deliverable_id,
    ):
        deliverable = state.deliverables.get(evidence.deliverable_id)
        if deliverable is None:
            raise ValueError(
                f"rejected release {release.id} manifest references missing "
                f"deliverable {evidence.deliverable_id}"
            )
        if deliverable.state != TeamRecordState.ACCEPTED:
            raise ValueError(
                f"rejected release {release.id} source deliverable "
                f"{deliverable.id} is no longer accepted"
            )
        source_task = state.tasks.get(deliverable.task_id)
        if source_task is None or source_task.project_id != release.project_id:
            raise ValueError(
                f"rejected release {release.id} source deliverable "
                f"{deliverable.id} has invalid task provenance"
            )
        feature_branch = evidence.feature_branch
        feature_commit_sha = evidence.feature_commit_sha
        if feature_branch is None and feature_commit_sha is None:
            continue
        if feature_branch is None or feature_commit_sha is None:
            raise ValueError(
                f"rejected release {release.id} source deliverable "
                f"{deliverable.id} has partial Git provenance"
            )
        dependency_sources.append(
            DependencyGitSource(
                dependency_task_id=deliverable.task_id,
                deliverable_id=deliverable.id,
                branch=feature_branch,
                commit_sha=feature_commit_sha,
            )
        )

    return ReleaseVerificationCorrectionGitSource(
        release_verification_run_id=run.id,
        release_package_id=release.id,
        release_branch=release_branch,
        candidate_commit_sha=candidate_commit_sha,
        candidate_tree_hash=candidate_tree_hash,
        dependency_git_sources=tuple(dependency_sources),
    )


def is_release_review_correction_task(
    state: TeamState,
    *,
    task_id: str,
) -> bool:
    return any(
        release.review_correction_task_id == task_id for release in state.release_packages.values()
    )


def is_rejected_candidate_correction_task(
    state: TeamState,
    *,
    task_id: str,
) -> bool:
    """Whether a task is durably linked to one rejected candidate source."""

    return (
        is_human_review_correction_task(state, task_id=task_id)
        or is_release_verification_correction_task(state, task_id=task_id)
        or is_release_review_correction_task(state, task_id=task_id)
    )


def release_review_correction_git_source(
    state: TeamState,
    *,
    task_id: str,
) -> ReleaseReviewCorrectionGitSource | None:
    """Return the immutable rejected candidate for agent release-review work."""

    matches = sorted(
        (
            release
            for release in state.release_packages.values()
            if release.review_correction_task_id == task_id
        ),
        key=lambda release: release.id,
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(
            f"task {task_id} is linked to multiple agent release reviews: "
            + ", ".join(release.id for release in matches)
        )
    release = matches[0]
    task = state.tasks.get(task_id)
    if task is None:
        raise ValueError(f"release review correction task {task_id} does not exist")
    if task.project_id != release.project_id:
        raise ValueError(
            f"release review correction task {task_id} belongs to project "
            f"{task.project_id}, not rejected release project {release.project_id}"
        )
    failure = release.gate_failure
    if (
        release.state != TeamRecordState.REJECTED
        or failure is None
        or failure.stage != "review"
        or failure.kind != "rejected"
        or failure.source_id is None
    ):
        raise ValueError(
            f"release review correction task {task_id} does not reference an active "
            f"agent review rejection on release {release.id}"
        )
    review = state.review_requests.get(failure.source_id)
    if (
        review is None
        or review.release_package_id != release.id
        or review.state != TeamRecordState.REJECTED
    ):
        raise ValueError(
            f"release review correction task {task_id} references missing rejected "
            f"release review {failure.source_id}"
        )
    manifest = release.manifest
    if manifest is None or manifest.evidence_status != "complete":
        raise ValueError(f"rejected release {release.id} has no complete candidate manifest")

    candidate_commit_sha = (release.candidate_commit_sha or "").casefold()
    candidate_tree_hash = (release.candidate_tree_hash or "").casefold()
    release_branch = release.release_branch or manifest.release_branch
    if not release_branch:
        raise ValueError(f"rejected release {release.id} has no release branch")
    if (
        not candidate_commit_sha
        or review.candidate_commit_sha is None
        or review.candidate_commit_sha.casefold() != candidate_commit_sha
        or manifest.candidate_commit_sha.casefold() != candidate_commit_sha
    ):
        raise ValueError(
            f"release review {review.id} does not match rejected release {release.id} "
            "candidate commit"
        )
    if (
        not candidate_tree_hash
        or manifest.candidate_tree_hash is None
        or manifest.candidate_tree_hash.casefold() != candidate_tree_hash
    ):
        raise ValueError(
            f"release review {review.id} does not match rejected release {release.id} "
            "candidate tree"
        )
    if manifest.release_branch != release_branch:
        raise ValueError(
            f"rejected release {release.id} has inconsistent release branch provenance"
        )
    for field_name, value in (
        ("candidate commit", candidate_commit_sha),
        ("candidate tree", candidate_tree_hash),
    ):
        if _FULL_OBJECT_ID_RE.fullmatch(value) is None:
            raise ValueError(f"rejected release {release.id} records an invalid {field_name}")

    dependency_sources: list[DependencyGitSource] = []
    for evidence in sorted(
        manifest.deliverables,
        key=lambda item: item.deliverable_id,
    ):
        deliverable = state.deliverables.get(evidence.deliverable_id)
        if deliverable is None:
            raise ValueError(
                f"rejected release {release.id} manifest references missing "
                f"deliverable {evidence.deliverable_id}"
            )
        if deliverable.state != TeamRecordState.ACCEPTED:
            raise ValueError(
                f"rejected release {release.id} source deliverable "
                f"{deliverable.id} is no longer accepted"
            )
        source_task = state.tasks.get(deliverable.task_id)
        if source_task is None or source_task.project_id != release.project_id:
            raise ValueError(
                f"rejected release {release.id} source deliverable "
                f"{deliverable.id} has invalid task provenance"
            )
        feature_branch = evidence.feature_branch
        feature_commit_sha = evidence.feature_commit_sha
        if feature_branch is None and feature_commit_sha is None:
            continue
        if feature_branch is None or feature_commit_sha is None:
            raise ValueError(
                f"rejected release {release.id} source deliverable "
                f"{deliverable.id} has partial Git provenance"
            )
        dependency_sources.append(
            DependencyGitSource(
                dependency_task_id=deliverable.task_id,
                deliverable_id=deliverable.id,
                branch=feature_branch,
                commit_sha=feature_commit_sha,
            )
        )

    return ReleaseReviewCorrectionGitSource(
        release_review_request_id=review.id,
        release_package_id=release.id,
        release_branch=release_branch,
        candidate_commit_sha=candidate_commit_sha,
        candidate_tree_hash=candidate_tree_hash,
        dependency_git_sources=tuple(dependency_sources),
    )


def rejected_candidate_git_source(
    state: TeamState,
    *,
    task_id: str,
) -> RejectedCandidateGitSource | None:
    """Resolve exactly one durable rejected-candidate source for a task."""

    human_source = human_review_correction_git_source(state, task_id=task_id)
    verification_source = release_verification_correction_git_source(
        state,
        task_id=task_id,
    )
    review_source = release_review_correction_git_source(state, task_id=task_id)
    source_count = sum(
        source is not None for source in (human_source, verification_source, review_source)
    )
    if source_count > 1:
        raise ValueError(f"task {task_id} is linked to multiple rejected-candidate failures")
    return human_source or verification_source or review_source


__all__ = [
    "HumanReviewCorrectionGitSource",
    "RejectedCandidateGitSource",
    "RejectedDeliverableContinuationGitSource",
    "ReleaseReviewCorrectionGitSource",
    "ReleaseVerificationCorrectionGitSource",
    "human_review_correction_git_source",
    "is_human_review_correction_task",
    "is_rejected_candidate_correction_task",
    "is_release_review_correction_task",
    "is_release_verification_correction_task",
    "rejected_candidate_git_source",
    "rejected_deliverable_continuation_git_source",
    "release_review_correction_git_source",
    "release_verification_correction_git_source",
]
