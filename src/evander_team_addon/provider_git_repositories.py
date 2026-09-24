"""Provider git repositories."""

from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from evander.effects.providers.agent_clone_repo import (
    AgentCloneRepoEffectProvider,
)
from evander.events.provider_models import (
    AgentCloneRepoEffectDefinition,
)

from .git_naming import (
    validate_branch_name,
)
from .provider_git_contexts import (
    _create_project_repo_git_work_contexts,
)
from .provider_git_errors import (
    DependencyGitPreparationError,
    ProjectGitPreparationError,
    ReviewRepositoryPreparationError,
)
from .provider_git_metadata import (
    _project_repo_context_tasks,
    _project_repo_payload,
    _resolved_project_git_identity,
)
from .provider_guidance import (
    _mark_project_repo_prepared,
    _mark_project_repos_prepared,
)
from .provider_projection import (
    _string_items,
)
from .provider_values import (
    _optional_text,
    _required_text,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)


def _prepare_project_repos(
    context: Any,
    *,
    repo: TeamStateRepository,
    state: Any,
    agent: Mapping[str, Any],
    dispatch_id: str,
    tasks: object,
    project_ids: Sequence[str],
    review_request_ids: Sequence[str] = (),
) -> tuple[Any, list[dict[str, Any]]]:
    _validate_review_repository_dispatch_compatibility(
        state,
        review_request_ids=review_request_ids,
    )
    project_repos: list[dict[str, Any]] = []
    prepared_review_checkouts: dict[tuple[str, str], Mapping[str, Any]] = {}
    for project_id in sorted(set(project_ids)):
        try:
            state, project_repo = _prepare_project_repo(
                context,
                repo=repo,
                state=state,
                agent={**agent, "project_ids": [project_id]},
                dispatch_id=dispatch_id,
                tasks=tasks,
                review_request_ids=review_request_ids,
                prepared_review_checkouts=prepared_review_checkouts,
            )
        except (DependencyGitPreparationError, TeamStateConflictError):
            raise
        except Exception as exc:
            raise ProjectGitPreparationError(
                project_id=project_id,
                message=str(exc) or exc.__class__.__name__,
            ) from exc
        if project_repo is not None:
            project_repos.append(project_repo)
    expected_managed_repo_ids = {
        project.git.managed_repo_id
        for project_id in project_ids
        if (project := state.projects.get(project_id)) is not None and project.git is not None
    }
    prepared_managed_repo_ids = {
        _required_text(project_repo.get("managed_repo_id"), "managed_repo_id")
        for project_repo in project_repos
    }
    missing_managed_repo_ids = sorted(expected_managed_repo_ids - prepared_managed_repo_ids)
    if missing_managed_repo_ids:
        raise RuntimeError(
            "dispatch repository preparation omitted managed repositories: "
            + ", ".join(missing_managed_repo_ids)
        )
    if project_repos:
        _mark_project_repos_prepared(
            context,
            session_id=_required_text(agent.get("session_id"), "session_id"),
            project_repos=project_repos,
        )
    return state, project_repos


def _prepare_project_repo(
    context: Any,
    *,
    repo: TeamStateRepository,
    state: Any,
    agent: Mapping[str, Any],
    dispatch_id: str,
    tasks: object,
    review_request_ids: Sequence[str] = (),
    prepared_review_checkouts: dict[tuple[str, str], Mapping[str, Any]] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    projects = getattr(state, "projects", None)
    if not isinstance(projects, Mapping):
        return state, None

    def _value(item: object, field_name: str) -> object:
        if isinstance(item, Mapping):
            return item.get(field_name)
        return getattr(item, field_name, None)

    project_id: str | None = None
    git: object | None = None
    for candidate_project_id in _string_items(agent.get("project_ids")):
        project = projects.get(candidate_project_id)
        if project is None:
            continue
        candidate_git = _value(project, "git")
        if candidate_git is None:
            continue
        project_id = candidate_project_id
        git = candidate_git
        break
    if git is None:
        return state, None

    agent_id = _required_text(agent.get("id"), "agent.id")
    session_id = _required_text(agent.get("session_id"), "session_id")
    managed_repo_id = _required_text(_value(git, "managed_repo_id"), "project.git.managed_repo_id")
    agent_author_identity = _resolved_project_git_identity(_value(git, "agent_author_identity"))
    agent_committer_identity = _resolved_project_git_identity(
        _value(git, "agent_committer_identity")
    )
    clone_branch = _optional_text(_value(git, "base_branch"))
    if clone_branch is None:
        clone_branch = _managed_repository_default_branch(
            context,
            managed_repo_id=managed_repo_id,
            directory_path=(_optional_text(_value(git, "directory_path")) or "root"),
        )
    directory_path = _optional_text(_value(git, "directory_path")) or "root"
    review_snapshots = _review_repository_snapshots_for_project(
        state,
        project_id=project_id,
        managed_repo_id=managed_repo_id,
        directory_path=directory_path,
        review_request_ids=review_request_ids,
    )
    pinned_ref: str | None = None
    pinned_commit_sha: str | None = None
    if review_snapshots:
        pinned_ref, pinned_commit_sha = _pin_review_repository_snapshots(
            context,
            project_id=project_id,
            snapshots=review_snapshots,
        )
    checkout_cache_key = (
        (managed_repo_id, pinned_commit_sha) if pinned_commit_sha is not None else None
    )
    clone_result = (
        prepared_review_checkouts.get(checkout_cache_key)
        if checkout_cache_key is not None and prepared_review_checkouts is not None
        else None
    )
    if clone_result is None:
        provider = AgentCloneRepoEffectProvider()
        provider.initialize(
            AgentCloneRepoEffectDefinition(
                id="team-prepare-project-repo",
                display_name="Prepare Team project repository",
                type="effect:agent:clone-repo",
                session_id=session_id,
                managed_repo_id=managed_repo_id,
                clone_branch=clone_branch,
                vm_user="agent",
                git_author_name=agent_author_identity.name,
                git_author_email=agent_author_identity.email,
                git_committer_name=agent_committer_identity.name,
                git_committer_email=agent_committer_identity.email,
                git_access_mode=(
                    "read-only"
                    if pinned_ref is not None
                    and not _project_repo_context_tasks(
                        tasks,
                        project_id=project_id,
                        agent_id=agent_id,
                    )
                    else None
                ),
                pinned_ref=pinned_ref,
                pinned_commit_sha=pinned_commit_sha,
            ),
            context,
        )
        clone_result = provider.execute()
        if (
            checkout_cache_key is not None
            and prepared_review_checkouts is not None
            and bool(clone_result.get("success"))
        ):
            prepared_review_checkouts[checkout_cache_key] = clone_result
    if not bool(clone_result.get("success")):
        message = (
            _optional_text(clone_result.get("message"))
            or _optional_text(clone_result.get("error"))
            or "failed to prepare Team project repository"
        )
        raise RuntimeError(message)
    project_repo = _project_repo_payload(
        project_id=project_id,
        managed_repo_id=managed_repo_id,
        clone_result=clone_result,
        base_branch=clone_branch,
    )
    if review_snapshots:
        project_repo["review_repository_snapshots"] = review_snapshots
    state, git_work_contexts, commit_diagnostics = _create_project_repo_git_work_contexts(
        context,
        repo,
        state=state,
        agent_id=agent_id,
        session_id=session_id,
        project_id=project_id,
        project_repo=project_repo,
        dispatch_id=dispatch_id,
        tasks=tasks,
        base_branch=clone_branch,
        dependency_merge_author_identity=agent_author_identity,
    )
    if git_work_contexts:
        project_repo["git_work_contexts"] = git_work_contexts
        project_repo["branch_contexts_by_task_id"] = {
            _required_text(row.get("task_id"), "task_id"): row for row in git_work_contexts
        }
        if len(git_work_contexts) == 1:
            project_repo["branch_context_id"] = git_work_contexts[0]["branch_context_id"]
            project_repo["agent_branch"] = git_work_contexts[0]["agent_branch"]
    project_repo.update(commit_diagnostics)
    from .code_index import prepare_indexes

    prepare_indexes(context, project_repo)
    _mark_project_repo_prepared(context, session_id=session_id, project_repo=project_repo)
    return state, project_repo


def _validate_review_repository_dispatch_compatibility(
    state: Any,
    *,
    review_request_ids: Sequence[str],
) -> None:
    """Reject a dispatch before checkout when one repository has divergent reviews."""

    reviews = getattr(state, "review_requests", {})
    by_repository: dict[str, list[tuple[str, Any]]] = {}
    for review_id in sorted(set(_string_items(review_request_ids))):
        review = reviews.get(review_id)
        snapshot = getattr(review, "repository_snapshot", None) if review is not None else None
        if snapshot is None:
            continue
        by_repository.setdefault(snapshot.managed_repo_id, []).append((review_id, snapshot))
    for managed_repo_id, snapshots in by_repository.items():
        commits = {snapshot.commit_sha.casefold() for _, snapshot in snapshots}
        if len(commits) <= 1:
            continue
        raise ReviewRepositoryPreparationError(
            project_id="dispatch",
            review_request_ids=[review_id for review_id, _ in snapshots],
            message=(
                f"reviews for managed repository {managed_repo_id} require different immutable "
                "commits and cannot share one checkout: "
                + ", ".join(
                    f"{review_id}={snapshot.commit_sha}" for review_id, snapshot in snapshots
                )
            ),
        )


def _managed_repository_default_branch(
    context: Any,
    *,
    managed_repo_id: str,
    directory_path: str,
) -> str:
    managed_git = getattr(
        getattr(context, "admin_service", None),
        "managed_git_admin",
        None,
    )
    get_repository = getattr(managed_git, "get_repository", None)
    if not callable(get_repository):
        raise RuntimeError("managed Git default-branch resolution is unavailable")
    record = get_repository(
        managed_repo_id,
        directory_path=directory_path,
    )
    if not isinstance(record, Mapping):
        raise RuntimeError("managed Git repository lookup returned an invalid record")
    default_branch = _optional_text(record.get("default_branch"))
    if default_branch is None:
        raise RuntimeError(f"managed repository {managed_repo_id} has no concrete default branch")
    return validate_branch_name(default_branch)


def _review_repository_snapshots_for_project(
    state: Any,
    *,
    project_id: str,
    managed_repo_id: str,
    directory_path: str,
    review_request_ids: Sequence[str],
) -> list[dict[str, str]]:
    """Return immutable review sources for this project or fail before cloning."""

    reviews = getattr(state, "review_requests", {})
    deliverables = getattr(state, "deliverables", {})
    tasks = getattr(state, "tasks", {})
    releases = getattr(state, "release_packages", {})
    selected: list[tuple[str, Any]] = []
    for review_id in sorted(set(_string_items(review_request_ids))):
        review = reviews.get(review_id)
        if review is None:
            continue
        review_project_id: str | None = None
        deliverable_id = _optional_text(getattr(review, "deliverable_id", None))
        if deliverable_id is not None:
            deliverable = deliverables.get(deliverable_id)
            task = (
                tasks.get(getattr(deliverable, "task_id", None))
                if deliverable is not None
                else None
            )
            review_project_id = _optional_text(getattr(task, "project_id", None))
        else:
            release = releases.get(
                _optional_text(getattr(review, "release_package_id", None)) or ""
            )
            review_project_id = _optional_text(getattr(release, "project_id", None))
        if review_project_id == project_id:
            selected.append((review_id, review))
    if not selected:
        return []

    missing = [
        review_id
        for review_id, review in selected
        if getattr(review, "repository_snapshot", None) is None
    ]
    if missing:
        raise ReviewRepositoryPreparationError(
            project_id=project_id,
            review_request_ids=missing,
            message=(
                "review request(s) have no immutable repository snapshot (legacy reviews must "
                "be retried or replaced): " + ", ".join(missing)
            ),
        )

    snapshots: list[dict[str, str]] = []
    invalid_ids: list[str] = []
    for review_id, review in selected:
        snapshot = review.repository_snapshot
        assert snapshot is not None
        if snapshot.managed_repo_id != managed_repo_id or snapshot.directory_path != directory_path:
            invalid_ids.append(review_id)
            continue
        snapshots.append(
            {
                "review_request_id": review_id,
                "managed_repo_id": snapshot.managed_repo_id,
                "directory_path": snapshot.directory_path,
                "commit_sha": snapshot.commit_sha,
                "pinned_ref": snapshot.pinned_ref,
                "source_kind": snapshot.source_kind,
                **({"source_ref": snapshot.source_ref} if snapshot.source_ref is not None else {}),
            }
        )
    if invalid_ids:
        raise ReviewRepositoryPreparationError(
            project_id=project_id,
            review_request_ids=invalid_ids,
            message=(
                f"review snapshot repository provenance does not match project {project_id}: "
                + ", ".join(invalid_ids)
            ),
        )
    commits = {snapshot["commit_sha"] for snapshot in snapshots}
    if len(commits) != 1:
        raise ReviewRepositoryPreparationError(
            project_id=project_id,
            review_request_ids=[snapshot["review_request_id"] for snapshot in snapshots],
            message=(
                f"reviews for managed repository {managed_repo_id} require different immutable "
                "commits and cannot share one checkout: "
                + ", ".join(
                    f"{snapshot['review_request_id']}={snapshot['commit_sha']}"
                    for snapshot in snapshots
                )
            ),
        )
    return snapshots


def _pin_review_repository_snapshots(
    context: Any,
    *,
    project_id: str,
    snapshots: Sequence[Mapping[str, str]],
) -> tuple[str, str]:
    """Create every retained review ref and select their shared checkout source."""

    if not snapshots:
        raise ValueError("review snapshots are required")
    managed_git = getattr(getattr(context, "admin_service", None), "managed_git_admin", None)
    pin_snapshot = getattr(managed_git, "pin_team_review_snapshot", None)
    review_ids = [snapshot["review_request_id"] for snapshot in snapshots]
    if not callable(pin_snapshot):
        raise ReviewRepositoryPreparationError(
            project_id=project_id,
            review_request_ids=review_ids,
            message="managed Git Team review snapshot pinning is unavailable",
        )
    expected_commit = snapshots[0]["commit_sha"]
    selected_ref: str | None = None
    try:
        for snapshot in snapshots:
            result = pin_snapshot(
                snapshot["managed_repo_id"],
                snapshot["directory_path"],
                review_id=snapshot["review_request_id"],
                commit_sha=snapshot["commit_sha"],
                source_ref=snapshot.get("source_ref"),
                pinned_ref=snapshot["pinned_ref"],
            )
            if not isinstance(result, Mapping):
                raise RuntimeError(
                    "managed Git Team review snapshot pinning returned an invalid result"
                )
            actual_commit = _required_text(result.get("commit_sha"), "pinned snapshot commit_sha")
            actual_ref = _required_text(result.get("pin_ref"), "pinned snapshot pin_ref")
            if actual_commit.casefold() != snapshot["commit_sha"].casefold():
                raise RuntimeError(
                    f"review snapshot {snapshot['review_request_id']} pinned {actual_commit}, not "
                    f"{snapshot['commit_sha']}"
                )
            if actual_ref != snapshot["pinned_ref"]:
                raise RuntimeError(
                    f"review snapshot {snapshot['review_request_id']} pinned ref {actual_ref}, not "
                    f"{snapshot['pinned_ref']}"
                )
            selected_ref = selected_ref or actual_ref
    except Exception as exc:
        raise ReviewRepositoryPreparationError(
            project_id=project_id,
            review_request_ids=review_ids,
            message=f"failed to retain immutable review snapshot: {exc}",
        ) from exc
    if selected_ref is None:
        raise ReviewRepositoryPreparationError(
            project_id=project_id,
            review_request_ids=review_ids,
            message="review snapshot pinning returned no checkout ref",
        )
    return selected_ref, expected_commit
