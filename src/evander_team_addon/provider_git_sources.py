"""Provider git sources."""

from __future__ import annotations

import hashlib
import re
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from evander.review_snapshots import (
    team_deliverable_snapshot_binding_id,
    team_deliverable_snapshot_ref,
)

from .models import (
    TeamRecordState,
    TeamTaskState,
)
from .provider_git_errors import (
    DependencyGitPreparationError,
)
from .provider_values import (
    _optional_text,
    _required_text,
)
from .services.domain.correction_git_source import (
    HumanReviewCorrectionGitSource,
    RejectedCandidateGitSource,
    RejectedDeliverableContinuationGitSource,
    human_review_correction_git_source,
    rejected_candidate_git_source,
    rejected_deliverable_continuation_git_sources,
)
from .services.domain.task_assessment import (
    required_source_deliverable_ids_for_task,
)


def _verified_human_review_correction_git_source(
    context: Any,
    *,
    state: Any,
    task_id: str,
    managed_repo_id: str,
) -> HumanReviewCorrectionGitSource | None:
    try:
        source = human_review_correction_git_source(
            state,
            task_id=task_id,
        )
    except ValueError as exc:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=str(exc),
        ) from None
    if source is None:
        return None
    _verify_rejected_candidate_git_source(
        context,
        state=state,
        task_id=task_id,
        managed_repo_id=managed_repo_id,
        source=source,
    )
    return source


def _verified_rejected_candidate_git_source(
    context: Any,
    *,
    state: Any,
    task_id: str,
    managed_repo_id: str,
) -> RejectedCandidateGitSource | None:
    try:
        source = rejected_candidate_git_source(
            state,
            task_id=task_id,
        )
    except ValueError as exc:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=str(exc),
        ) from None
    if source is None:
        return None
    _verify_rejected_candidate_git_source(
        context,
        state=state,
        task_id=task_id,
        managed_repo_id=managed_repo_id,
        source=source,
    )
    return source


def _verified_rejected_deliverable_continuation_git_sources(
    context: Any,
    *,
    state: Any,
    task_id: str,
    managed_repo_id: str,
) -> tuple[RejectedDeliverableContinuationGitSource, ...]:
    try:
        sources = rejected_deliverable_continuation_git_sources(
            state,
            task_id=task_id,
        )
    except ValueError as exc:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=str(exc),
        ) from None
    for source in sources:
        _verify_rejected_deliverable_continuation_git_source(
            context,
            state=state,
            task_id=task_id,
            managed_repo_id=managed_repo_id,
            source=source,
        )
    return sources


def _verified_rejected_deliverable_continuation_git_source(
    context: Any,
    *,
    state: Any,
    task_id: str,
    managed_repo_id: str,
) -> RejectedDeliverableContinuationGitSource | None:
    sources = _verified_rejected_deliverable_continuation_git_sources(
        context,
        state=state,
        task_id=task_id,
        managed_repo_id=managed_repo_id,
    )
    return sources[0] if sources else None


def _verify_rejected_deliverable_continuation_git_source(
    context: Any,
    *,
    state: Any,
    task_id: str,
    managed_repo_id: str,
    source: RejectedDeliverableContinuationGitSource,
) -> None:
    task = state.tasks.get(task_id)
    project = state.projects.get(task.project_id) if task is not None else None
    project_git = getattr(project, "git", None)
    if project_git is None or project_git.managed_repo_id != managed_repo_id:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=(
                f"rejected deliverable {source.deliverable_id} belongs to a different "
                "managed repository"
            ),
        )
    managed_git = getattr(
        getattr(context, "admin_service", None),
        "managed_git_admin",
        None,
    )
    resolve_pinned = getattr(managed_git, "resolve_team_review_commit", None)
    resolve_branch = getattr(managed_git, "resolve_branch_commit", None)
    verify_ancestry = getattr(managed_git, "verify_branch_commit_ancestry", None)
    if not all(callable(item) for item in (resolve_pinned, resolve_branch, verify_ancestry)):
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=(
                "managed Git rejected-deliverable verification is unavailable; "
                f"cannot resume {source.deliverable_id}"
            ),
        )
    try:
        pinned_commit = resolve_pinned(
            managed_repo_id,
            directory_path=project_git.directory_path,
            pinned_ref=source.pinned_ref,
        )
        if _optional_text(pinned_commit) is None or str(pinned_commit).casefold() != (
            source.commit_sha
        ):
            raise RuntimeError(
                f"immutable ref {source.pinned_ref!r} does not retain {source.commit_sha}"
            )
        branch_commit = resolve_branch(
            managed_repo_id,
            directory_path=project_git.directory_path,
            branch=source.agent_branch,
        )
        if _optional_text(branch_commit) is None:
            raise RuntimeError(f"retained branch {source.agent_branch!r} does not exist")
        verify_ancestry(
            managed_repo_id,
            directory_path=project_git.directory_path,
            branch=source.agent_branch,
            commit_sha=str(branch_commit),
            required_ancestor_shas=[source.base_commit_sha, source.commit_sha],
        )
    except Exception as exc:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=(
                f"could not verify rejected deliverable continuation {source.deliverable_id}: {exc}"
            ),
        ) from None


def _rejected_deliverable_continuation_dependency_source(
    *,
    task_id: str,
    source: RejectedDeliverableContinuationGitSource,
) -> dict[str, str]:
    return {
        "dependency_task_id": task_id,
        "deliverable_id": source.deliverable_id,
        "branch": source.agent_branch,
        "commit_sha": source.commit_sha,
        "relationship_kind": "task_continuation",
        "pinned_ref": source.pinned_ref,
    }


def _rejected_deliverable_continuation_identity_commit(
    sources: Sequence[RejectedDeliverableContinuationGitSource],
) -> str | None:
    if not sources:
        return None
    if len(sources) == 1:
        return sources[0].commit_sha
    material = "\0".join(
        f"{source.deliverable_id}\0{source.commit_sha}"
        for source in sorted(sources, key=lambda item: item.deliverable_id)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _managed_git_base_tree_hash(
    context: Any,
    *,
    task_id: str,
    managed_repo_id: str,
    directory_path: str,
    base_commit_sha: str,
    sources: Sequence[Mapping[str, str]],
) -> str:
    managed_git = getattr(
        getattr(context, "admin_service", None),
        "managed_git_admin",
        None,
    )
    reduce_frontier = getattr(managed_git, "reduce_commit_frontier", None)
    if not callable(reduce_frontier):
        raise DependencyGitPreparationError(
            task_id=task_id,
            message="managed Git base-tree resolution is unavailable",
        )
    try:
        result = reduce_frontier(
            managed_repo_id,
            directory_path=directory_path,
            base_commit_sha=base_commit_sha,
            sources=[dict(source) for source in sources],
        )
        resolved_base = _required_text(result.get("base_commit_sha"), "base_commit_sha")
        resolved_tree = _required_text(result.get("base_tree_hash"), "base_tree_hash")
        if resolved_base.casefold() != base_commit_sha.casefold():
            raise RuntimeError(f"managed Git resolved base {resolved_base}, not {base_commit_sha}")
        if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", resolved_tree) is None:
            raise RuntimeError("managed Git returned an invalid base tree hash")
    except Exception as exc:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=f"could not resolve immutable task base tree: {exc}",
        ) from None
    return resolved_tree.casefold()


def _verify_rejected_candidate_git_source(
    context: Any,
    *,
    state: Any,
    task_id: str,
    managed_repo_id: str,
    source: RejectedCandidateGitSource,
) -> None:
    task = state.tasks.get(task_id)
    project = state.projects.get(task.project_id) if task is not None else None
    project_git = getattr(project, "git", None)
    if project_git is None or project_git.managed_repo_id != managed_repo_id:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=(
                f"correction task {task_id} rejected candidate belongs "
                "to a different managed repository"
            ),
        )
    managed_git = getattr(
        getattr(context, "admin_service", None),
        "managed_git_admin",
        None,
    )
    resolve_snapshot = getattr(managed_git, "resolve_snapshot", None)
    if not callable(resolve_snapshot):
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=(
                "managed Git snapshot verification is unavailable; cannot verify "
                f"the rejected candidate for correction task {task_id}"
            ),
        )
    try:
        snapshot = resolve_snapshot(
            managed_repo_id,
            project_git.directory_path,
            source_ref=source.release_branch,
            commit_sha=source.candidate_commit_sha,
            tree_hash=source.candidate_tree_hash,
        )
    except Exception as exc:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=(f"could not verify rejected candidate for correction task {task_id}: {exc}"),
        ) from None
    if (
        _optional_text(snapshot.get("commit_sha")) or ""
    ).casefold() != source.candidate_commit_sha or (
        _optional_text(snapshot.get("tree_hash")) or ""
    ).casefold() != source.candidate_tree_hash:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=(
                f"managed Git returned incomplete rejected-candidate provenance "
                f"for correction task {task_id}"
            ),
        )


def _project_repo_task_dependency_git_sources(
    context: Any,
    *,
    state: Any,
    task_id: str,
    managed_repo_id: str,
) -> list[dict[str, str]]:
    """Resolve accepted direct-dependency Git contributions from host state."""

    try:
        return _project_repo_task_dependency_git_sources_unchecked(
            context,
            state=state,
            task_id=task_id,
            managed_repo_id=managed_repo_id,
        )
    except DependencyGitPreparationError:
        raise
    except RuntimeError as exc:
        raise DependencyGitPreparationError(
            task_id=task_id,
            message=str(exc),
        ) from None


def _merge_dependency_git_source_rows(
    *groups: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    """Merge immutable source rows by deliverable, preferring current task semantics."""

    rows_by_deliverable_id: dict[str, dict[str, str]] = {}
    for group in groups:
        for source in group:
            deliverable_id = _required_text(
                source.get("deliverable_id"),
                "dependency source deliverable_id",
            )
            rows_by_deliverable_id[deliverable_id] = dict(source)
    return [
        rows_by_deliverable_id[deliverable_id] for deliverable_id in sorted(rows_by_deliverable_id)
    ]


def _project_repo_task_dependency_git_sources_unchecked(
    context: Any,
    *,
    state: Any,
    task_id: str,
    managed_repo_id: str,
) -> list[dict[str, str]]:
    task = getattr(state, "tasks", {}).get(task_id)
    if task is None:
        raise RuntimeError(f"task {task_id} no longer exists during Git preparation")
    rows: list[dict[str, str]] = []
    managed_git = getattr(getattr(context, "admin_service", None), "managed_git_admin", None)
    resolve_branch_commit = getattr(managed_git, "resolve_branch_commit", None)
    resolve_team_review_commit = getattr(managed_git, "resolve_team_review_commit", None)
    required_source_ids = set(required_source_deliverable_ids_for_task(state, task_id=task_id))
    pinned_sources = (
        set(task.deliverable_contract.pinned_source_deliverable_ids)
        if task.deliverable_contract
        else set()
    )
    if pinned_sources and not pinned_sources.issubset(required_source_ids):
        raise RuntimeError(
            "pinned integration contributions changed; create a new recovery preview"
        )
    required_task_ids = {
        state.deliverables[deliverable_id].task_id
        for deliverable_id in required_source_ids
        if deliverable_id in state.deliverables
    }
    dependency_task_ids = list(
        dict.fromkeys([*task.dependency_task_ids, *sorted(required_task_ids)])
    )
    for dependency_task_id in dependency_task_ids:
        dependency = state.tasks.get(dependency_task_id)
        if dependency is None:
            raise RuntimeError(f"task {task_id} dependency {dependency_task_id} no longer exists")
        if dependency.state == TeamTaskState.CANCELLED:
            continue
        current_deliverables = sorted(
            (
                deliverable
                for deliverable in state.deliverables.values()
                if deliverable.task_id == dependency_task_id
                and deliverable.superseded_by_deliverable_id is None
            ),
            key=lambda deliverable: deliverable.id,
        )
        git_deliverables = [
            deliverable
            for deliverable in current_deliverables
            if _deliverable_has_git_state(deliverable)
            and (not pinned_sources or deliverable.id in pinned_sources)
            and (
                dependency_task_id in task.dependency_task_ids
                or deliverable.id in required_source_ids
            )
        ]
        if not git_deliverables:
            continue
        unaccepted = [
            deliverable.id
            for deliverable in git_deliverables
            if deliverable.state != TeamRecordState.ACCEPTED
        ]
        if unaccepted:
            raise RuntimeError(
                f"task {task_id} cannot inherit dependency {dependency_task_id}: "
                "current Git deliverables are not accepted: " + ", ".join(unaccepted)
            )
        dependency_project = state.projects.get(dependency.project_id)
        dependency_git = (
            getattr(dependency_project, "git", None) if dependency_project is not None else None
        )
        if dependency_git is None:
            raise RuntimeError(
                f"task {task_id} cannot inherit Git-backed dependency "
                f"{dependency_task_id}: its project has no managed Git configuration"
            )
        if dependency_git.managed_repo_id != managed_repo_id:
            raise RuntimeError(
                f"task {task_id} cannot inherit Git-backed dependency "
                f"{dependency_task_id}: it belongs to managed repository "
                f"{dependency_git.managed_repo_id}, not {managed_repo_id}"
            )
        if not callable(resolve_branch_commit):
            raise RuntimeError(
                "managed Git branch resolution is unavailable; cannot verify "
                f"dependency {dependency_task_id} before leasing task {task_id}"
            )
        for deliverable in git_deliverables:
            git_refs = deliverable.git_refs
            commit_candidates = {
                commit.casefold()
                for commit in (
                    git_refs.commit_sha,
                    git_refs.submitted_commit_sha,
                    git_refs.reviewed_commit_sha,
                )
                if commit is not None
            }
            if len(commit_candidates) != 1:
                raise RuntimeError(
                    f"accepted Git deliverable {deliverable.id} must record one "
                    "consistent submitted/reviewed commit"
                )
            commit_sha = next(iter(commit_candidates))
            if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit_sha) is None:
                raise RuntimeError(
                    f"accepted Git deliverable {deliverable.id} records an invalid commit ID"
                )
            pinned_ref = _accepted_deliverable_pinned_ref(
                context,
                state,
                deliverable_id=deliverable.id,
                managed_repo_id=managed_repo_id,
                directory_path=dependency_git.directory_path,
                commit_sha=commit_sha,
            )
            branch_context_id = git_refs.branch_context_id
            branch_context = (
                state.git_work_contexts.get(branch_context_id)
                if branch_context_id is not None
                else None
            )
            if branch_context is not None and branch_context.task_id != dependency_task_id:
                raise RuntimeError(
                    f"accepted Git deliverable {deliverable.id} has an invalid "
                    f"branch context {branch_context_id}"
                )
            branch = _optional_text(git_refs.agent_branch) or _optional_text(
                getattr(branch_context, "agent_branch", None)
            )
            if branch is None:
                raise RuntimeError(
                    f"accepted Git deliverable {deliverable.id} has no source branch label"
                )
            if branch_context is not None and branch != branch_context.agent_branch:
                raise RuntimeError(
                    f"accepted Git deliverable {deliverable.id} records ambiguous "
                    "agent branch provenance"
                )
            if (
                pinned_ref is None
                and branch_context is None
                and deliverable.acceptance_policy_version >= 2
            ):
                raise RuntimeError(
                    f"policy-v{deliverable.acceptance_policy_version} accepted Git "
                    f"deliverable {deliverable.id} has no retained ref or branch context"
                )
            try:
                authoritative_commit = (
                    resolve_team_review_commit(
                        managed_repo_id,
                        directory_path=dependency_git.directory_path,
                        pinned_ref=pinned_ref,
                    )
                    if pinned_ref is not None and callable(resolve_team_review_commit)
                    else resolve_branch_commit(
                        managed_repo_id,
                        directory_path=dependency_git.directory_path,
                        branch=branch,
                    )
                )
            except Exception as exc:
                source_label = pinned_ref or branch
                raise RuntimeError(
                    f"could not resolve dependency source {source_label!r} for accepted "
                    f"deliverable {deliverable.id}: {exc}"
                ) from None
            normalized_authoritative = _optional_text(authoritative_commit)
            if normalized_authoritative is None:
                raise RuntimeError(
                    f"dependency branch {branch!r} for accepted deliverable "
                    f"{deliverable.id} does not exist in managed Git"
                )
            normalized_authoritative = normalized_authoritative.casefold()
            if normalized_authoritative != commit_sha:
                if pinned_ref is not None:
                    raise RuntimeError(
                        f"immutable dependency ref {pinned_ref!r} for accepted deliverable "
                        f"{deliverable.id} does not match reviewed commit {commit_sha}: found "
                        f"{normalized_authoritative}"
                    )
                raise RuntimeError(
                    f"dependency branch {branch!r} moved after acceptance for "
                    f"deliverable {deliverable.id}: expected {commit_sha}, found "
                    f"{normalized_authoritative}"
                )
            rows.append(
                {
                    "dependency_task_id": dependency_task_id,
                    "deliverable_id": deliverable.id,
                    "branch": branch,
                    "commit_sha": commit_sha,
                    "relationship_kind": (
                        "required_contribution"
                        if deliverable.id in required_source_ids
                        else "task_dependency"
                    ),
                    **({"pinned_ref": pinned_ref} if pinned_ref is not None else {}),
                }
            )
    return rows


def _accepted_deliverable_pinned_ref(
    context: Any,
    state: Any,
    *,
    deliverable_id: str,
    managed_repo_id: str,
    directory_path: str,
    commit_sha: str,
) -> str | None:
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        return None
    recorded = _optional_text(getattr(deliverable.git_refs, "accepted_pinned_ref", None))
    snapshot_rows = sorted(
        (
            (review.id, review.repository_snapshot)
            for review in state.review_requests.values()
            if review.deliverable_id == deliverable_id
            and review.state == TeamRecordState.ACCEPTED
            and review.repository_snapshot is not None
            and review.repository_snapshot.managed_repo_id == managed_repo_id
            and review.repository_snapshot.directory_path == directory_path
            and review.repository_snapshot.commit_sha.casefold() == commit_sha.casefold()
        ),
        key=lambda row: row[1].pinned_ref,
    )
    snapshots = [snapshot for _review_id, snapshot in snapshot_rows]
    if recorded is not None:
        if snapshots and all(snapshot.pinned_ref != recorded for snapshot in snapshots):
            raise RuntimeError(
                f"accepted Git deliverable {deliverable_id} records pinned ref {recorded!r} "
                "outside its accepted review evidence"
            )
        selected_ref = recorded
    else:
        selected_ref = snapshots[0].pinned_ref if snapshots else None
    if selected_ref is None:
        return None

    selected_row = next(
        (row for row in snapshot_rows if row[1].pinned_ref == selected_ref),
        None,
    )
    if selected_row is not None:
        review_id = selected_row[0]
        source_ref = selected_row[1].source_ref
    elif selected_ref == team_deliverable_snapshot_ref(deliverable_id):
        review_id = team_deliverable_snapshot_binding_id(deliverable_id)
        source_ref = _optional_text(deliverable.git_refs.agent_branch)
    else:
        raise RuntimeError(
            f"accepted Git deliverable {deliverable_id} records unrecognized "
            f"immutable ref {selected_ref!r}"
        )
    managed_git = getattr(getattr(context, "admin_service", None), "managed_git_admin", None)
    resolve_review_commit = getattr(managed_git, "resolve_team_review_commit", None)
    pin_snapshot = getattr(managed_git, "pin_team_review_snapshot", None)
    try:
        resolved_before = (
            resolve_review_commit(
                managed_repo_id,
                directory_path=directory_path,
                pinned_ref=selected_ref,
            )
            if callable(resolve_review_commit)
            else None
        )
        if _optional_text(resolved_before) is not None:
            if str(resolved_before).casefold() != commit_sha.casefold():
                raise RuntimeError(
                    f"immutable dependency ref {selected_ref!r} points to "
                    f"{resolved_before}, not accepted commit {commit_sha}"
                )
            return selected_ref
        if not callable(pin_snapshot):
            raise RuntimeError("managed Git Team review snapshot pinning is unavailable")
        pinned = pin_snapshot(
            managed_repo_id,
            directory_path,
            review_id=review_id,
            commit_sha=commit_sha,
            source_ref=source_ref,
            pinned_ref=selected_ref,
        )
        if not isinstance(pinned, Mapping):
            raise RuntimeError("managed Git Team review snapshot pinning returned invalid data")
        actual_ref = _required_text(pinned.get("pin_ref"), "pinned snapshot pin_ref")
        actual_commit = _required_text(
            pinned.get("commit_sha"),
            "pinned snapshot commit_sha",
        )
        if actual_ref != selected_ref or actual_commit.casefold() != commit_sha.casefold():
            raise RuntimeError(
                f"managed Git pinned {actual_ref}@{actual_commit}, not {selected_ref}@{commit_sha}"
            )
        if callable(resolve_review_commit):
            resolved_after = resolve_review_commit(
                managed_repo_id,
                directory_path=directory_path,
                pinned_ref=selected_ref,
            )
            if _optional_text(resolved_after) is None or str(resolved_after).casefold() != (
                commit_sha.casefold()
            ):
                raise RuntimeError(
                    f"managed Git did not retain {selected_ref!r} at accepted commit {commit_sha}"
                )
        return selected_ref
    except Exception as exc:
        if deliverable.acceptance_policy_version < 2:
            return None
        raise RuntimeError(
            f"could not materialize immutable dependency ref {selected_ref!r} for "
            f"policy-v{deliverable.acceptance_policy_version} deliverable "
            f"{deliverable_id}: {exc}"
        ) from exc


def _deliverable_has_git_state(deliverable: Any) -> bool:
    git_refs = deliverable.git_refs
    return any(
        (
            git_refs.branch_context_id,
            git_refs.agent_branch,
            git_refs.review_branch,
            git_refs.feature_branch,
            git_refs.release_branch,
            git_refs.commit_sha,
            git_refs.submitted_commit_sha,
            git_refs.reviewed_commit_sha,
            git_refs.feature_commit_sha,
            git_refs.release_commit_sha,
        )
    )


def _prior_project_repo_task_base_commit(
    state: Any,
    *,
    agent_id: str,
    task_id: str,
    agent_branch: str,
    base_branch: str | None = None,
    dependency_git_sources: Sequence[Mapping[str, str]] = (),
    preparation_generation: str | None = None,
    require_dependency_provenance_match: bool = False,
) -> str | None:
    contexts = getattr(state, "git_work_contexts", {})
    if not isinstance(contexts, Mapping):
        return None
    matching_contexts = [
        context_model
        for context_model in contexts.values()
        if _optional_text(getattr(context_model, "task_id", None)) == task_id
        and _optional_text(getattr(context_model, "base_branch", None)) == base_branch
        and (
            preparation_generation is None
            or _optional_text(getattr(context_model, "preparation_generation", None))
            == preparation_generation
        )
        and (
            not require_dependency_provenance_match
            or (
                _optional_text(getattr(context_model, "agent_id", None)) == agent_id
                and _optional_text(getattr(context_model, "agent_branch", None)) == agent_branch
            )
        )
    ]
    expected_dependency_sources = [dict(row) for row in dependency_git_sources]
    provenance_matches = [
        context_model
        for context_model in matching_contexts
        if [
            source.model_dump(mode="json") if hasattr(source, "model_dump") else dict(source)
            for source in getattr(context_model, "dependency_git_sources", [])
        ]
        == expected_dependency_sources
    ]
    if provenance_matches or require_dependency_provenance_match:
        matching_contexts = provenance_matches
    candidates = {
        base_commit
        for context_model in matching_contexts
        if (
            base_commit := (
                _optional_text(
                    getattr(
                        getattr(context_model, "dependency_composition_manifest", None),
                        "composed_commit_sha",
                        None,
                    )
                )
                or _optional_text(getattr(context_model, "base_commit", None))
            )
        )
        is not None
    }
    if len(candidates) > 1:
        raise RuntimeError(
            f"task worktree provenance is ambiguous for task {task_id}: "
            f"prior Git work contexts record conflicting base commits "
            f"{', '.join(sorted(candidates))}"
        )
    return next(iter(candidates), None)
