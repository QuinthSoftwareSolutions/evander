"""Derive immutable managed-Git provenance for newly-created Team reviews."""

from __future__ import annotations

from ...models import Deliverable, ReleasePackage, ReviewRepositorySnapshot, TeamState
from evander.review_snapshots import team_review_snapshot_ref

from .git_work_contexts import authoritative_deliverable_git_base


def deliverable_review_repository_snapshot(
    state: TeamState,
    *,
    deliverable: Deliverable,
    review_id: str,
) -> ReviewRepositorySnapshot | None:
    """Capture the only source a new deliverable review may inspect.

    Candidate commits take precedence.  A planning or artifact-only
    deliverable instead inherits the producer task's persisted Git base.  A
    configured managed repository without either source is an invalid review
    request, rather than permission to inspect the reviewer's prior checkout.
    """

    task = state.tasks.get(deliverable.task_id)
    if task is None:
        raise ValueError(
            f"deliverable {deliverable.id} references missing task {deliverable.task_id}"
        )
    project = state.projects.get(task.project_id)
    if project is None:
        raise ValueError(f"task {task.id} references missing project {task.project_id}")
    git = project.git
    if git is None:
        return None

    candidate_commits = {
        value.casefold()
        for value in (
            deliverable.git_refs.reviewed_commit_sha,
            deliverable.git_refs.submitted_commit_sha,
            deliverable.git_refs.commit_sha,
        )
        if value is not None
    }
    if len(candidate_commits) > 1:
        raise ValueError(
            f"Git-backed deliverable {deliverable.id} has conflicting candidate commits; "
            "submit a replacement deliverable before requesting review"
        )
    if candidate_commits:
        commit_sha = next(iter(candidate_commits))
        source_kind = "deliverable_candidate"
        source_ref = next(
            (
                value
                for value in (
                    deliverable.git_refs.review_branch,
                    deliverable.git_refs.agent_branch,
                    deliverable.git_refs.feature_branch,
                    deliverable.git_refs.release_branch,
                )
                if value is not None
            ),
            None,
        )
    else:
        try:
            source_ref, commit_sha = authoritative_deliverable_git_base(
                state,
                deliverable=deliverable,
            )
        except ValueError as exc:
            raise ValueError(
                f"Git-backed deliverable {deliverable.id} has ambiguous authoritative base: {exc}"
            ) from exc
        if commit_sha is None:
            raise ValueError(
                f"Git-backed deliverable {deliverable.id} has no immutable candidate or "
                "authoritative task base commit"
            )
        source_kind = "task_base"

    return ReviewRepositorySnapshot(
        managed_repo_id=git.managed_repo_id,
        directory_path=git.directory_path,
        commit_sha=commit_sha,
        pinned_ref=team_review_snapshot_ref(review_id),
        source_kind=source_kind,
        source_ref=source_ref,
    )


def release_review_repository_snapshot(
    state: TeamState,
    *,
    release: ReleasePackage,
    review_id: str,
    candidate_commit_sha: str,
) -> ReviewRepositorySnapshot | None:
    """Capture the immutable release candidate for a newly-created review."""

    project = state.projects.get(release.project_id)
    if project is None:
        raise ValueError(
            f"release package {release.id} references missing project {release.project_id}"
        )
    git = project.git
    if git is None:
        return None
    recorded_candidate = release.candidate_commit_sha
    if recorded_candidate is not None and recorded_candidate.casefold() != candidate_commit_sha.casefold():
        raise ValueError(
            f"release package {release.id} candidate {recorded_candidate} does not match "
            f"review candidate {candidate_commit_sha}"
        )
    source_ref = release.release_branch or (
        release.manifest.release_branch if release.manifest is not None else None
    )
    return ReviewRepositorySnapshot(
        managed_repo_id=git.managed_repo_id,
        directory_path=git.directory_path,
        commit_sha=candidate_commit_sha,
        pinned_ref=team_review_snapshot_ref(review_id),
        source_kind="release_candidate",
        source_ref=source_ref,
    )


__all__ = [
    "deliverable_review_repository_snapshot",
    "release_review_repository_snapshot",
]
