"""Create and validate authoritative Git work contexts for leased tasks.

Repository discovery, dependency source validation, and backend worktree
preparation are separate dependencies; this module coordinates their receipts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from evander.effects.providers.agent_session import (
    selected_execution_backend_for_target,
)
from evander.git_identity import (
    DEFAULT_AGENT_GIT_IDENTITY,
    GitIdentity,
)

from .git_naming import (
    validate_branch_name,
)
from .models import (
    DependencyCompositionManifest,
    TeamGitPreparationAttemptState,
    TeamTaskKind,
)
from .provider_constants import (
    TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS,
)
from .provider_git_errors import (
    DependencyGitPreparationError,
)
from .provider_git_metadata import (
    _ensure_project_repo_git_preparation_attempt,
    _git_work_context_prompt_payload,
    _project_repo_context_tasks,
    _project_repo_git_preparation_attempt_id,
    _project_repo_task_preparation_generation,
    _project_repo_task_worktree_name,
    _record_project_repo_git_preparation_receipt,
    _resolved_project_git_identity,
)
from .provider_git_sources import (
    _managed_git_base_tree_hash,
    _merge_dependency_git_source_rows,
    _prior_project_repo_task_base_commit,
    _project_repo_task_dependency_git_sources,
    _rejected_deliverable_continuation_dependency_source,
    _rejected_deliverable_continuation_identity_commit,
    _verified_rejected_candidate_git_source,
    _verified_rejected_deliverable_continuation_git_sources,
)
from .provider_git_worktrees import (
    _prepare_project_repo_task_worktree,
)
from .provider_values import (
    _optional_text,
    _required_text,
    _runtime_dir,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.admin_service import (
    TeamAdminService,
)
from .services.domain.common import (
    _slug_id_part,
)
from .services.domain.dependency_composition_recovery import (
    accepted_recovery_for_target,
    dependency_composition_source_fingerprint,
    recovery_for_task,
)
from .services.domain.preparation_failures import (
    task_git_work_context_generation,
)
from .services.worktree_cleanup_service import (
    team_task_worktree_cleanup_lock,
)


def _create_project_repo_git_work_contexts(
    context: Any,
    repo: TeamStateRepository,
    *,
    state: Any,
    agent_id: str,
    session_id: str,
    project_id: str,
    project_repo: Mapping[str, Any],
    dispatch_id: str,
    tasks: object,
    base_branch: str | None,
    dependency_merge_author_identity: GitIdentity = DEFAULT_AGENT_GIT_IDENTITY,
) -> tuple[Any, list[dict[str, Any]], dict[str, int]]:
    target_dir = _optional_text(project_repo.get("target_dir"))
    if target_dir is None:
        return (
            state,
            [],
            {
                "state_commit_attempt_count": 0,
                "state_conflict_retry_count": 0,
            },
        )
    rows: list[dict[str, Any]] = []
    admin = TeamAdminService(
        repo,
        managed_git_admin=getattr(
            getattr(context, "admin_service", None),
            "managed_git_admin",
            None,
        ),
    )
    state_commit_attempt_count = 0
    state_conflict_retry_count = 0
    task_worktree_repo_name = _required_text(
        project_repo.get("guest_repo_name"),
        "guest_repo_name",
    )
    for task in _project_repo_context_tasks(
        tasks,
        project_id=project_id,
        agent_id=agent_id,
    ):
        task_id = _required_text(task.get("id"), "task.id")
        task_record = state.tasks.get(task_id)
        if task_record is not None and task_record.task_kind == TeamTaskKind.COORDINATION:
            continue
        task_author = (
            _resolved_project_git_identity(task_record.authored_by)
            if task_record is not None and task_record.authored_by is not None
            else dependency_merge_author_identity
        )
        managed_repo_id = _required_text(
            project_repo.get("managed_repo_id"),
            "managed_repo_id",
        )
        dependency_composition_mode = "automatic"
        dependency_composition_recovery_id: str | None = None
        resolved_dependency_branch: str | None = None
        resolved_dependency_commit_sha: str | None = None
        manual_dependency_git_sources: list[dict[str, str]] = []
        composition_manifest_git_sources: list[dict[str, str]] = []
        correction_source = _verified_rejected_candidate_git_source(
            context,
            state=state,
            task_id=task_id,
            managed_repo_id=managed_repo_id,
        )
        continuation_sources = _verified_rejected_deliverable_continuation_git_sources(
            context,
            state=state,
            task_id=task_id,
            managed_repo_id=managed_repo_id,
        )
        continuation_identity_commit = _rejected_deliverable_continuation_identity_commit(
            continuation_sources
        )
        dependency_recovery = recovery_for_task(
            state,
            recovery_task_id=task_id,
        )
        if dependency_recovery is not None:
            dependency_git_sources = [
                source.model_dump(mode="json")
                for source in dependency_recovery.dependency_git_sources
            ]
            effective_base_branch = dependency_recovery.base_branch
            worktree_base_ref = dependency_recovery.base_commit_sha
            prior_base_commit = dependency_recovery.base_commit_sha
            pinned_base_commit = dependency_recovery.base_commit_sha
            pinned_base_tree = dependency_recovery.base_tree_hash
            composition_git_sources = dependency_git_sources
            composition_manifest_git_sources = dependency_git_sources
            manual_dependency_git_sources = dependency_git_sources
            dependency_composition_mode = "manual_recovery"
            dependency_composition_recovery_id = dependency_recovery.id
        elif continuation_sources:
            continuation_dependency_sources = [
                _rejected_deliverable_continuation_dependency_source(
                    task_id=task_id,
                    source=source,
                )
                for source in continuation_sources
            ]
            dependency_git_sources = _merge_dependency_git_source_rows(
                continuation_dependency_sources,
                _project_repo_task_dependency_git_sources(
                    context,
                    state=state,
                    task_id=task_id,
                    managed_repo_id=managed_repo_id,
                ),
            )
            effective_base_branch = _optional_text(task.get("git_base_branch")) or base_branch
            worktree_base_ref = effective_base_branch
            pinned_base_commit = _optional_text(task.get("git_base_commit_sha"))
            continuation_base_commits = {source.base_commit_sha for source in continuation_sources}
            if len(continuation_base_commits) != 1:
                raise DependencyGitPreparationError(
                    task_id=task_id,
                    message=(
                        "unsuperseded rejected deliverables record different original "
                        "Git bases: " + ", ".join(sorted(continuation_base_commits))
                    ),
                )
            continuation_base_commit = next(iter(continuation_base_commits))
            if pinned_base_commit is not None and pinned_base_commit.casefold() != (
                continuation_base_commit
            ):
                raise DependencyGitPreparationError(
                    task_id=task_id,
                    message=(
                        "rejected deliverables "
                        + ", ".join(source.deliverable_id for source in continuation_sources)
                        + f" were prepared from {continuation_base_commit}, not task pin "
                        f"{pinned_base_commit}"
                    ),
                )
            prior_base_commit = continuation_base_commit
            pinned_base_tree = (
                _managed_git_base_tree_hash(
                    context,
                    task_id=task_id,
                    managed_repo_id=managed_repo_id,
                    directory_path=(
                        _optional_text(
                            getattr(
                                getattr(state.projects.get(project_id), "git", None),
                                "directory_path",
                                None,
                            )
                        )
                        or "root"
                    ),
                    base_commit_sha=pinned_base_commit,
                    sources=continuation_dependency_sources,
                )
                if pinned_base_commit is not None
                else None
            )
            # Treat the reviewed candidate as a distinct composition relationship:
            # its immutable pin becomes an ancestor of the replacement branch,
            # while the task's original base provenance remains unchanged.
            composition_git_sources = dependency_git_sources
            composition_manifest_git_sources = dependency_git_sources
        elif correction_source is not None:
            rejected_candidate_git_sources = [
                source.model_dump(mode="json")
                for source in correction_source.dependency_git_sources
            ]
            correction_task_git_sources = _project_repo_task_dependency_git_sources(
                context,
                state=state,
                task_id=task_id,
                managed_repo_id=managed_repo_id,
            )
            dependency_git_sources = _merge_dependency_git_source_rows(
                rejected_candidate_git_sources,
                correction_task_git_sources,
            )
            effective_base_branch = correction_source.release_branch
            worktree_base_ref = effective_base_branch
            prior_base_commit = correction_source.candidate_commit_sha
            pinned_base_commit = correction_source.candidate_commit_sha
            pinned_base_tree = correction_source.candidate_tree_hash
            # The rejected candidate already contains its original release
            # sources. Compose only newly accepted correction-task inputs on
            # top of that immutable base, and attest those current inputs in
            # the acceptance manifest.
            composition_git_sources = correction_task_git_sources
            composition_manifest_git_sources = correction_task_git_sources
        else:
            dependency_git_sources = _project_repo_task_dependency_git_sources(
                context,
                state=state,
                task_id=task_id,
                managed_repo_id=managed_repo_id,
            )
            effective_base_branch = _optional_text(task.get("git_base_branch")) or base_branch
            worktree_base_ref = effective_base_branch
            pinned_base_commit = _optional_text(task.get("git_base_commit_sha"))
            pinned_base_tree = None
            composition_git_sources = dependency_git_sources
            composition_manifest_git_sources = dependency_git_sources
            accepted_recovery = accepted_recovery_for_target(
                state,
                target_task_id=task_id,
                dependency_git_sources=dependency_git_sources,
            )
            if accepted_recovery is not None:
                recovery_deliverable = state.deliverables.get(
                    accepted_recovery.accepted_deliverable_id or ""
                )
                if recovery_deliverable is None:
                    raise DependencyGitPreparationError(
                        task_id=task_id,
                        message=(
                            f"accepted dependency recovery {accepted_recovery.id} "
                            "references a missing deliverable"
                        ),
                    )
                resolved_dependency_branch = _optional_text(
                    recovery_deliverable.git_refs.agent_branch
                )
                resolved_dependency_commit_sha = _optional_text(
                    accepted_recovery.resolved_commit_sha
                )
                if resolved_dependency_branch is None or resolved_dependency_commit_sha is None:
                    raise DependencyGitPreparationError(
                        task_id=task_id,
                        message=(
                            f"accepted dependency recovery {accepted_recovery.id} "
                            "has incomplete resolved Git provenance"
                        ),
                    )
                effective_base_branch = accepted_recovery.base_branch
                worktree_base_ref = effective_base_branch
                pinned_base_commit = accepted_recovery.base_commit_sha
                pinned_base_tree = accepted_recovery.base_tree_hash
                dependency_composition_mode = "adopt_resolved"
                dependency_composition_recovery_id = accepted_recovery.id
        preparation_generation = _project_repo_task_preparation_generation(
            state,
            task_id=task_id,
            effective_base_branch=effective_base_branch,
        )
        # A failed automatic composition may already own a worktree and branch,
        # even though it never produced a preparation receipt. Give the reviewed
        # recovery its own stable identity on both backends, preserving that
        # checkout and keeping retries of the same recovery on the same branch.
        candidate_identity_commit = (
            continuation_identity_commit
            if continuation_identity_commit is not None
            else correction_source.candidate_commit_sha
            if correction_source is not None
            else resolved_dependency_commit_sha
        )
        agent_branch = _project_repo_agent_branch(
            session_id,
            task_id=task_id,
            preparation_generation=preparation_generation,
            candidate_commit_sha=candidate_identity_commit,
        )
        try:
            if dependency_recovery is None:
                recorded_base_commit = _prior_project_repo_task_base_commit(
                    state,
                    agent_id=agent_id,
                    task_id=task_id,
                    agent_branch=agent_branch,
                    base_branch=effective_base_branch,
                    dependency_git_sources=dependency_git_sources,
                    preparation_generation=preparation_generation,
                    require_dependency_provenance_match=bool(continuation_sources),
                )
                # The rejected-candidate pin remains the immutable composition
                # root, while a retained correction resumes from its recorded
                # composed base after the first preparation.
                if recorded_base_commit is not None:
                    prior_base_commit = recorded_base_commit
                elif correction_source is None and not continuation_sources:
                    prior_base_commit = None
        except RuntimeError as exc:
            if dependency_git_sources:
                raise DependencyGitPreparationError(
                    task_id=task_id,
                    message=str(exc),
                ) from None
            raise
        worktree_name = _project_repo_task_worktree_name(
            task_id=task_id,
            preparation_generation=preparation_generation,
            identity_commit=(candidate_identity_commit or pinned_base_commit),
        )
        directory_path = (
            _optional_text(
                getattr(
                    getattr(state.projects.get(project_id), "git", None),
                    "directory_path",
                    None,
                )
            )
            or "root"
        )
        selected_backend, _selected_config = selected_execution_backend_for_target(
            context,
            {"session_id": session_id},
            directory_path=directory_path,
        )
        execution_backend = (
            _optional_text(project_repo.get("execution_backend")) or selected_backend
        )
        preparation_attempt_id = _project_repo_git_preparation_attempt_id(
            agent_id=agent_id,
            session_id=session_id,
            task_id=task_id,
            managed_repo_id=managed_repo_id,
            branch=agent_branch,
            preparation_generation=preparation_generation,
        )
        expected_worktree_path = f"/home/agent/worktrees/{task_worktree_repo_name}/{worktree_name}"
        state = _ensure_project_repo_git_preparation_attempt(
            repo,
            attempt_id=preparation_attempt_id,
            agent_id=agent_id,
            session_id=session_id,
            task_id=task_id,
            project_id=project_id,
            dispatch_id=dispatch_id,
            managed_repo_id=managed_repo_id,
            directory_path=directory_path,
            execution_backend=execution_backend,
            worktree_path=expected_worktree_path,
            branch=agent_branch,
            base_ref=worktree_base_ref or "HEAD",
            preparation_generation=preparation_generation,
        )
        preparation_attempt = state.git_preparation_attempts[preparation_attempt_id]
        use_preparation_receipt = (
            preparation_attempt.state != TeamGitPreparationAttemptState.COMMITTED
        )
        if (
            preparation_attempt.state == TeamGitPreparationAttemptState.PREPARED
            and prior_base_commit is None
        ):
            prior_base_commit = preparation_attempt.base_commit_sha
        recovery_already_committed = (
            dependency_composition_mode == "adopt_resolved"
            and preparation_attempt.state == TeamGitPreparationAttemptState.COMMITTED
            and prior_base_commit == resolved_dependency_commit_sha
        )
        with team_task_worktree_cleanup_lock(
            runtime_dir=_runtime_dir(context),
            session_id=session_id,
            repo_name=task_worktree_repo_name,
        ):
            composition_result: dict[str, Any] = {}
            preparation_receipt_result: dict[str, Any] = {}
            try:
                worktree_path, base_commit = _prepare_project_repo_task_worktree(
                    context,
                    project_repo=project_repo,
                    session_id=session_id,
                    task_id=task_id,
                    agent_branch=agent_branch,
                    base_ref=worktree_base_ref or "HEAD",
                    worktree_name=worktree_name,
                    preparation_attempt_id=(
                        preparation_attempt_id if use_preparation_receipt else None
                    ),
                    worktree_identity_commit=candidate_identity_commit,
                    preparation_generation=preparation_generation,
                    prior_base_commit=prior_base_commit,
                    pinned_base_commit=pinned_base_commit,
                    pinned_base_tree=pinned_base_tree,
                    dependency_git_sources=composition_git_sources,
                    # After adoption has committed, validate the retained task
                    # checkout normally so later agent work is never reset to
                    # the recovery candidate. Keep the recovery on the Team
                    # context below, where it remains part of the provenance.
                    dependency_composition_mode=(
                        "automatic" if recovery_already_committed else dependency_composition_mode
                    ),
                    dependency_composition_recovery_id=(
                        None if recovery_already_committed else dependency_composition_recovery_id
                    ),
                    resolved_dependency_branch=(
                        None if recovery_already_committed else resolved_dependency_branch
                    ),
                    resolved_dependency_commit_sha=(
                        None if recovery_already_committed else resolved_dependency_commit_sha
                    ),
                    dependency_merge_author_identity=task_author,
                    task_author_identity=task_author,
                    composition_result=composition_result,
                    preparation_receipt_result=preparation_receipt_result,
                )
            except DependencyGitPreparationError as exc:
                if exc.conflict is not None:
                    project = state.projects.get(project_id)
                    project_git = getattr(project, "git", None)
                    exc.conflict.update(
                        {
                            "target_task_id": task_id,
                            "project_id": project_id,
                            "managed_repo_id": managed_repo_id,
                            "base_branch": effective_base_branch or "HEAD",
                            "directory_path": (
                                _optional_text(getattr(project_git, "directory_path", None))
                                or "root"
                            ),
                        }
                    )
                raise
            composed_head_commit = _optional_text(composition_result.get("composed_commit_sha"))
            task_base_commit_pin = _optional_text(task.get("git_base_commit_sha"))
            recorded_work_context_base_commit = base_commit
            if continuation_sources and pinned_base_commit is not None:
                recorded_work_context_base_commit = pinned_base_commit
            if task_base_commit_pin is not None:
                recorded_work_context_base_commit = task_base_commit_pin
            prepared_head_commit = (
                _optional_text(preparation_receipt_result.get("head_commit_sha"))
                or composed_head_commit
                or base_commit
            )
            if use_preparation_receipt:
                preparation_receipt_result.setdefault(
                    "request_fingerprint",
                    "provider:"
                    + hashlib.sha256(
                        json.dumps(
                            {
                                "attempt_id": preparation_attempt_id,
                                "worktree_path": worktree_path,
                                "base_commit": base_commit,
                                "head_commit": prepared_head_commit,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                )
                preparation_receipt_result.setdefault(
                    "head_commit_sha",
                    prepared_head_commit,
                )
                state = _record_project_repo_git_preparation_receipt(
                    repo,
                    attempt_id=preparation_attempt_id,
                    receipt=preparation_receipt_result,
                    # The preparation receipt describes the physical checkout.
                    # A pinned provenance root can intentionally differ from
                    # evander.baseCommit after dependency composition.
                    base_commit_sha=base_commit,
                    worktree_path=worktree_path,
                )
            dependency_composition_manifest = _build_dependency_composition_manifest(
                context,
                task_id=task_id,
                managed_repo_id=managed_repo_id,
                directory_path=(
                    _optional_text(
                        getattr(
                            getattr(state.projects.get(project_id), "git", None),
                            "directory_path",
                            None,
                        )
                    )
                    or "root"
                ),
                base_branch=effective_base_branch or "HEAD",
                dependency_git_sources=composition_manifest_git_sources,
                composition_result=composition_result,
            )
            context_id = _project_repo_git_work_context_id(
                agent_id=agent_id,
                task_id=task_id,
                dispatch_id=dispatch_id,
            )

            def _validate_prepared_dependency_provenance(
                current_state: Any,
            ) -> None:
                current_correction_source = _verified_rejected_candidate_git_source(
                    context,
                    state=current_state,
                    task_id=task_id,
                    managed_repo_id=managed_repo_id,
                )
                current_continuation_sources = (
                    _verified_rejected_deliverable_continuation_git_sources(
                        context,
                        state=current_state,
                        task_id=task_id,
                        managed_repo_id=managed_repo_id,
                    )
                )
                current_dependency_recovery = recovery_for_task(
                    current_state,
                    recovery_task_id=task_id,
                )
                current_dependency_git_sources = (
                    [
                        source.model_dump(mode="json")
                        for source in current_dependency_recovery.dependency_git_sources
                    ]
                    if current_dependency_recovery is not None
                    else _merge_dependency_git_source_rows(
                        [
                            _rejected_deliverable_continuation_dependency_source(
                                task_id=task_id,
                                source=source,
                            )
                            for source in current_continuation_sources
                        ],
                        _project_repo_task_dependency_git_sources(
                            context,
                            state=current_state,
                            task_id=task_id,
                            managed_repo_id=managed_repo_id,
                        ),
                    )
                    if current_continuation_sources
                    else _merge_dependency_git_source_rows(
                        [
                            source.model_dump(mode="json")
                            for source in current_correction_source.dependency_git_sources
                        ],
                        _project_repo_task_dependency_git_sources(
                            context,
                            state=current_state,
                            task_id=task_id,
                            managed_repo_id=managed_repo_id,
                        ),
                    )
                    if current_correction_source is not None
                    else _project_repo_task_dependency_git_sources(
                        context,
                        state=current_state,
                        task_id=task_id,
                        managed_repo_id=managed_repo_id,
                    )
                )
                if current_correction_source != correction_source:
                    raise DependencyGitPreparationError(
                        task_id=task_id,
                        message=(
                            f"task {task_id} rejected-candidate Git source changed "
                            "during worktree preparation; no lease was granted"
                        ),
                    )
                if current_continuation_sources != continuation_sources:
                    raise DependencyGitPreparationError(
                        task_id=task_id,
                        message=(
                            f"task {task_id} rejected-deliverable continuation source "
                            "changed during worktree preparation; no lease was granted"
                        ),
                    )
                if current_dependency_recovery != dependency_recovery:
                    raise DependencyGitPreparationError(
                        task_id=task_id,
                        message=(
                            f"task {task_id} dependency recovery changed during "
                            "worktree preparation; no lease was granted"
                        ),
                    )
                if current_dependency_git_sources != dependency_git_sources:
                    raise DependencyGitPreparationError(
                        task_id=task_id,
                        message=(
                            f"task {task_id} dependency Git state changed during "
                            "worktree preparation; no lease was granted"
                        ),
                    )
                current_preparation_generation = _project_repo_task_preparation_generation(
                    current_state,
                    task_id=task_id,
                    effective_base_branch=effective_base_branch,
                )
                if current_preparation_generation != preparation_generation:
                    raise DependencyGitPreparationError(
                        task_id=task_id,
                        message=(
                            f"task {task_id} preparation generation changed during "
                            f"worktree preparation from {preparation_generation} to "
                            f"{current_preparation_generation}; no lease was granted"
                        ),
                    )

            last_conflict: TeamStateConflictError | None = None
            for _attempt in range(TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS):
                state_commit_attempt_count += 1
                state = repo.read()
                runtime = state.agent_runtimes.get(agent_id)
                if runtime is None or runtime.active_dispatch_id != dispatch_id:
                    raise RuntimeError(
                        f"dispatch {dispatch_id} no longer owns the wake lease for agent {agent_id}"
                    )
                _validate_prepared_dependency_provenance(state)
                existing = state.git_work_contexts.get(context_id)
                if existing is not None:
                    _validate_project_repo_git_work_context(
                        existing,
                        agent_id=agent_id,
                        task_id=task_id,
                        dispatch_id=dispatch_id,
                        agent_branch=agent_branch,
                        base_branch=effective_base_branch,
                        base_commit=recorded_work_context_base_commit,
                        worktree_path=worktree_path,
                        dependency_git_sources=dependency_git_sources,
                        dependency_composition_recovery_id=(dependency_composition_recovery_id),
                        manual_dependency_git_sources=manual_dependency_git_sources,
                        dependency_composition_manifest=(dependency_composition_manifest),
                        preparation_generation=task_git_work_context_generation(
                            state,
                            task_id,
                        ),
                    )
                    break
                try:
                    state, context_id = admin.create_git_work_context_with_id(
                        context_id=context_id,
                        agent_id=agent_id,
                        task_id=task_id,
                        agent_branch=agent_branch,
                        base_branch=effective_base_branch,
                        base_commit=recorded_work_context_base_commit,
                        dispatch_id=dispatch_id,
                        worktree_path=worktree_path,
                        expected_preparation_generation=preparation_generation,
                        preparation_attempt_id=(
                            preparation_attempt_id if use_preparation_receipt else None
                        ),
                        dependency_git_sources=dependency_git_sources,
                        dependency_composition_recovery_id=(dependency_composition_recovery_id),
                        manual_dependency_git_sources=manual_dependency_git_sources,
                        dependency_composition_manifest=(dependency_composition_manifest),
                        resource_provenance_validator=(_validate_prepared_dependency_provenance),
                        expected_revision=None,
                    )
                    break
                except TeamStateConflictError as exc:
                    last_conflict = exc
                    state_conflict_retry_count += 1
            else:
                assert last_conflict is not None
                raise last_conflict
            context_model = state.git_work_contexts[context_id]
            rows.append(_git_work_context_prompt_payload(context_model))
    return (
        state,
        rows,
        {
            "state_commit_attempt_count": state_commit_attempt_count,
            "state_conflict_retry_count": state_conflict_retry_count,
        },
    )


def _project_repo_git_work_context_id(
    *,
    agent_id: str,
    task_id: str,
    dispatch_id: str,
) -> str:
    material = "\0".join((agent_id, task_id, dispatch_id)).encode("utf-8")
    return f"gitctx-wake-{hashlib.sha256(material).hexdigest()[:24]}"


def _build_dependency_composition_manifest(
    context: Any,
    *,
    task_id: str,
    managed_repo_id: str,
    directory_path: str,
    base_branch: str,
    dependency_git_sources: Sequence[Mapping[str, str]],
    composition_result: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not dependency_git_sources:
        return None
    root_commit = _optional_text(composition_result.get("root_commit_sha"))
    composed_commit = _optional_text(composition_result.get("composed_commit_sha"))
    composed_tree = _optional_text(composition_result.get("composed_tree_hash"))
    if root_commit is None or composed_commit is None or composed_tree is None:
        return None
    managed_git = getattr(getattr(context, "admin_service", None), "managed_git_admin", None)
    reduce_frontier = getattr(managed_git, "reduce_commit_frontier", None)
    if not callable(reduce_frontier):
        return None
    frontier = reduce_frontier(
        managed_repo_id,
        directory_path=directory_path,
        base_commit_sha=root_commit,
        sources=dependency_git_sources,
    )
    base_tree = _required_text(frontier.get("base_tree_hash"), "base_tree_hash")
    source_fingerprint = dependency_composition_source_fingerprint(
        managed_repo_id=managed_repo_id,
        directory_path=directory_path,
        base_branch=base_branch,
        base_commit_sha=root_commit,
        base_tree_hash=base_tree,
        dependency_git_sources=dependency_git_sources,
    )
    return {
        "policy_version": 1,
        "target_task_id": task_id,
        "managed_repo_id": managed_repo_id,
        "directory_path": directory_path,
        "base_branch": base_branch,
        "base_commit_sha": root_commit,
        "base_tree_hash": base_tree,
        "dependency_git_sources": [dict(source) for source in dependency_git_sources],
        "source_fingerprint": source_fingerprint,
        "frontier_commit_shas": list(frontier.get("frontier_commit_shas") or []),
        "reduced_source_deliverable_ids": list(
            frontier.get("reduced_source_deliverable_ids") or []
        ),
        "composed_commit_sha": composed_commit,
        "composed_tree_hash": composed_tree,
    }


def _validate_project_repo_git_work_context(
    context_model: Any,
    *,
    agent_id: str,
    task_id: str,
    dispatch_id: str,
    agent_branch: str,
    base_branch: str | None,
    base_commit: str | None,
    worktree_path: str,
    dependency_git_sources: Sequence[Mapping[str, str]],
    dependency_composition_recovery_id: str | None = None,
    manual_dependency_git_sources: Sequence[Mapping[str, str]] = (),
    dependency_composition_manifest: Mapping[str, Any] | None = None,
    preparation_generation: str | None = None,
) -> None:
    def _normalized_comparison_value(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, Mapping):
            return {
                key: _normalized_comparison_value(item)
                for key, item in value.items()
                if item is not None
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [_normalized_comparison_value(item) for item in value]
        return value

    expected = {
        "agent_id": agent_id,
        "task_id": task_id,
        "dispatch_id": dispatch_id,
        "agent_branch": agent_branch,
        "base_branch": base_branch,
        "base_commit": base_commit,
        "worktree_path": worktree_path,
        "dependency_git_sources": [dict(row) for row in dependency_git_sources],
        "dependency_composition_recovery_id": dependency_composition_recovery_id,
        "manual_dependency_git_sources": [dict(row) for row in manual_dependency_git_sources],
        "dependency_composition_manifest": (
            DependencyCompositionManifest.model_validate(dependency_composition_manifest)
            if dependency_composition_manifest is not None
            else None
        ),
        "preparation_generation": preparation_generation,
    }
    mismatches = [
        field_name
        for field_name, expected_value in expected.items()
        if _normalized_comparison_value(
            [item for item in getattr(context_model, field_name, [])]
            if field_name
            in {
                "dependency_git_sources",
                "manual_dependency_git_sources",
            }
            else (
                getattr(context_model, field_name, None)
                if field_name == "dependency_composition_manifest"
                and getattr(context_model, field_name, None) is not None
                else getattr(context_model, field_name, None)
            )
        )
        != _normalized_comparison_value(expected_value)
    ]
    if mismatches:
        raise RuntimeError(
            f"Git work context {context_model.id} does not match wake dispatch "
            f"{dispatch_id}: {', '.join(mismatches)}"
        )


def _project_repo_agent_branch(
    session_id: str,
    *,
    task_id: str,
    candidate_commit_sha: str | None = None,
    preparation_generation: str | None = None,
) -> str:
    task_slug = _slug_id_part(task_id, fallback="task")[:40]
    task_hash = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:8]
    candidate_suffix = ""
    if candidate_commit_sha is not None:
        normalized_candidate = candidate_commit_sha.strip().casefold()
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", normalized_candidate):
            raise ValueError("candidate correction task requires a full Git object ID")
        candidate_suffix = f"-{normalized_candidate[:12]}"
    generation_suffix = ""
    if preparation_generation is not None:
        normalized_generation = preparation_generation.strip().casefold()
        if re.fullmatch(r"[0-9a-f]{20}", normalized_generation) is None:
            raise ValueError("preparation_generation must be a 20-character hex fingerprint")
        generation_suffix = f"-prep-{normalized_generation[:12]}"
    return validate_branch_name(
        f"agents/{session_id}/tasks/{task_slug}-{task_hash}{candidate_suffix}{generation_suffix}"
    )
