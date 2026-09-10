from __future__ import annotations

from typing import Any

from ...models import ReleasePackage, TeamState


TEAM_RELEASE_APPROVED_EVENT_TYPE = "team.release.approved"


def append_release_approved_event(
    state: TeamState,
    *,
    release: ReleasePackage,
    result: dict[str, Any],
) -> None:
    """Record the policy-independent handoff for an approved release candidate."""

    candidate_commit_sha = release.approved_commit_sha
    manifest = release.manifest
    if candidate_commit_sha is None or manifest is None:
        raise ValueError("approved Git release is missing its frozen candidate manifest")
    repository_scope = manifest.repository_scope
    legacy_project = state.projects.get(release.project_id)
    legacy_git = legacy_project.git if legacy_project is not None else None

    result.setdefault("events", []).append(
        {
            "event_type": TEAM_RELEASE_APPROVED_EVENT_TYPE,
            "correlation_key": release.id,
            "idempotency_key": (
                f"team-addon:{TEAM_RELEASE_APPROVED_EVENT_TYPE}:{release.id}:{candidate_commit_sha}"
            ),
            "payload": {
                "project_id": manifest.project_id,
                "release_package_id": release.id,
                "managed_repo_id": (
                    repository_scope.managed_repo_id
                    if repository_scope is not None
                    else getattr(legacy_git, "managed_repo_id", None)
                ),
                "directory_path": (
                    repository_scope.directory_path
                    if repository_scope is not None
                    else getattr(legacy_git, "directory_path", None)
                ),
                "base_branch": (
                    repository_scope.base_branch
                    if repository_scope is not None
                    else release.git_base_branch
                    or getattr(legacy_git, "base_branch", None)
                ),
                "base_commit_sha": manifest.base_commit_sha,
                "candidate_commit_sha": candidate_commit_sha,
                "approved_commit_sha": candidate_commit_sha,
                "expected_commit_sha": candidate_commit_sha,
                "source_ref": manifest.release_branch,
                "canonical_branch": (
                    repository_scope.canonical_branch
                    if repository_scope is not None
                    else release.canonical_branch
                ),
                "automatic_release_branch_publication": (
                    manifest.automatic_release_branch_publication
                ),
                "canonical_publication_performed": False,
                "published_commit_sha": None,
                "state": "approved",
                "success": True,
            },
        }
    )


__all__ = ["TEAM_RELEASE_APPROVED_EVENT_TYPE", "append_release_approved_event"]
