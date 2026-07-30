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


def is_human_review_correction_task(
    state: TeamState,
    *,
    task_id: str,
) -> bool:
    return any(
        finding.correction_task_id == task_id
        for finding in state.human_review_findings.values()
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
    request = state.human_review_requests.get(
        finding.source_human_review_request_id
    )
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
        raise ValueError(
            f"rejected release {release.id} has no complete candidate manifest"
        )

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
            raise ValueError(
                f"rejected release {release.id} records an invalid {field_name}"
            )

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


__all__ = [
    "HumanReviewCorrectionGitSource",
    "human_review_correction_git_source",
    "is_human_review_correction_task",
]
