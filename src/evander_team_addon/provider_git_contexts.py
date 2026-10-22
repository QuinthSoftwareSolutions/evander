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
    AgentTask,
    DependencyCompositionRecovery,
    TeamState,
    DependencyCompositionManifest,
    TeamGitPreparationAttemptState,
    TeamTaskKind,
)
from .provider_constants import (
    TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS,
)
from .provider_git_continuity import (
    apply_retained_task_work,
    pin_retained_task_work,
    retained_target_base,
    validate_handoff_writers,
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
    _project_repo_task_preparation_activation,
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
from .services.domain.correction_git_source import (
    RejectedCandidateGitSource,
    RejectedDeliverableContinuationGitSource,
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
    return _ProjectGitContextPreparation(
        context,
        repo,
        state=state,
        agent_id=agent_id,
        session_id=session_id,
        project_id=project_id,
        project_repo=project_repo,
        dispatch_id=dispatch_id,
        tasks=tasks,
        base_branch=base_branch,
        dependency_merge_author_identity=dependency_merge_author_identity,
    ).run()


class _ProjectGitContextPreparation:
    """Prepare one project's task contexts, sharing its state and retry metrics."""

    rows: list[dict[str, Any]]
    admin: TeamAdminService
    state_commit_attempt_count: int
    state_conflict_retry_count: int
    task_worktree_repo_name: str

    def __init__(
        self,
        context: Any,
        repo: TeamStateRepository,
        *,
        state: TeamState,
        agent_id: str,
        session_id: str,
        project_id: str,
        project_repo: Mapping[str, Any],
        dispatch_id: str,
        tasks: object,
        base_branch: str | None,
        dependency_merge_author_identity: GitIdentity,
    ) -> None:
        self.context = context
        self.repo = repo
        self.state = state
        self.agent_id = agent_id
        self.session_id = session_id
        self.project_id = project_id
        self.project_repo = project_repo
        self.dispatch_id = dispatch_id
        self.tasks = tasks
        self.base_branch = base_branch
        self.dependency_merge_author_identity = dependency_merge_author_identity

    def run(self) -> tuple[TeamState, list[dict[str, Any]], dict[str, int]]:
        self.rows: list[dict[str, Any]] = []
        self.admin = TeamAdminService(
            self.repo,
            managed_git_admin=getattr(
                getattr(self.context, "admin_service", None),
                "managed_git_admin",
                None,
            ),
        )
        self.state_commit_attempt_count = 0
        self.state_conflict_retry_count = 0
        self.task_worktree_repo_name = _required_text(
            self.project_repo.get("guest_repo_name"),
            "guest_repo_name",
        )
        for task in _project_repo_context_tasks(
            self.tasks,
            project_id=self.project_id,
            agent_id=self.agent_id,
        ):
            _TaskGitContextPreparation(batch=self, task=task).run()
        return (
            self.state,
            self.rows,
            {
                "state_commit_attempt_count": self.state_commit_attempt_count,
                "state_conflict_retry_count": self.state_conflict_retry_count,
            },
        )


class _TaskGitContextPreparation:
    """Select provenance and commit one task context under the cleanup lock."""

    task_id: str
    task_record: AgentTask | None
    task_author: GitIdentity
    managed_repo_id: str
    dependency_composition_mode: str
    dependency_composition_recovery_id: str | None
    resolved_dependency_branch: str | None
    resolved_dependency_commit_sha: str | None
    manual_dependency_git_sources: list[dict[str, str]]
    composition_manifest_git_sources: list[dict[str, str]]
    correction_source: RejectedCandidateGitSource | None
    continuation_sources: tuple[RejectedDeliverableContinuationGitSource, ...]
    continuation_identity_commit: str | None
    dependency_recovery: DependencyCompositionRecovery | None
    dependency_git_sources: list[dict[str, str]]
    effective_base_branch: str | None
    worktree_base_ref: str | None
    prior_base_commit: str | None
    pinned_base_commit: str | None
    pinned_base_tree: str | None
    composition_git_sources: list[dict[str, str]]
    preparation_generation: str
    activation_sequence: int
    candidate_identity_commit: str | None
    agent_branch: str
    worktree_name: str
    directory_path: str
    execution_backend: str
    preparation_attempt_id: str
    expected_worktree_path: str
    use_preparation_receipt: bool
    recovery_already_committed: bool
    composition_result: dict[str, Any]
    preparation_receipt_result: dict[str, Any]
    worktree_path: str
    base_commit: str
    recorded_work_context_base_commit: str
    dependency_composition_manifest: dict[str, Any] | None
    context_id: str

    def __init__(
        self,
        *,
        batch: _ProjectGitContextPreparation,
        task: Mapping[str, Any],
    ) -> None:
        self.batch = batch
        self.task = task

    def run(self) -> None:
        if not self._initialize_task():
            return
        self._resolve_sources()
        self._select_base_strategy()
        self._plan_worktree()
        self._reserve_attempt()
        with team_task_worktree_cleanup_lock(
            runtime_dir=_runtime_dir(self.batch.context),
            session_id=self.batch.session_id,
            repo_name=self.batch.task_worktree_repo_name,
        ):
            self._prepare_worktree()
            self._record_receipt()
            self._build_context_provenance()
            self._commit_context()
            self._finish_context()

    def _select_base_strategy(self) -> None:
        if self.dependency_recovery is not None:
            self._use_manual_recovery_base()
        elif self.continuation_sources:
            self._use_rejected_deliverable_base()
        elif self.correction_source is not None:
            self._use_rejected_candidate_base()
        else:
            self._use_ordinary_base()

    def _initialize_task(self) -> bool:
        self.task_id = _required_text(self.task.get("id"), "task.id")
        self.task_record = self.batch.state.tasks.get(self.task_id)
        if self.task_record is not None and self.task_record.task_kind == TeamTaskKind.COORDINATION:
            return False
        self.task_author = (
            _resolved_project_git_identity(self.task_record.authored_by)
            if self.task_record is not None and self.task_record.authored_by is not None
            else self.batch.dependency_merge_author_identity
        )
        self.managed_repo_id = _required_text(
            self.batch.project_repo.get("managed_repo_id"),
            "managed_repo_id",
        )
        return True

    def _resolve_sources(self) -> None:
        self.dependency_composition_mode = "automatic"
        self.dependency_composition_recovery_id: str | None = None
        self.resolved_dependency_branch: str | None = None
        self.resolved_dependency_commit_sha: str | None = None
        self.manual_dependency_git_sources: list[dict[str, str]] = []
        self.composition_manifest_git_sources: list[dict[str, str]] = []
        self.correction_source = _verified_rejected_candidate_git_source(
            self.batch.context,
            state=self.batch.state,
            task_id=self.task_id,
            managed_repo_id=self.managed_repo_id,
        )
        self.continuation_sources = _verified_rejected_deliverable_continuation_git_sources(
            self.batch.context,
            state=self.batch.state,
            task_id=self.task_id,
            managed_repo_id=self.managed_repo_id,
        )
        self.continuation_identity_commit = _rejected_deliverable_continuation_identity_commit(
            self.continuation_sources
        )
        self.dependency_recovery = recovery_for_task(
            self.batch.state,
            recovery_task_id=self.task_id,
        )

    def _use_manual_recovery_base(self) -> None:
        self.dependency_git_sources = [
            source.model_dump(mode="json")
            for source in self.dependency_recovery.dependency_git_sources
        ]
        self.effective_base_branch = self.dependency_recovery.base_branch
        self.worktree_base_ref = self.dependency_recovery.base_commit_sha
        self.prior_base_commit = self.dependency_recovery.base_commit_sha
        self.pinned_base_commit = self.dependency_recovery.base_commit_sha
        self.pinned_base_tree = self.dependency_recovery.base_tree_hash
        self.composition_git_sources = self.dependency_git_sources
        self.composition_manifest_git_sources = self.dependency_git_sources
        self.manual_dependency_git_sources = self.dependency_git_sources
        self.dependency_composition_mode = "manual_recovery"
        self.dependency_composition_recovery_id = self.dependency_recovery.id

    def _use_rejected_deliverable_base(self) -> None:
        continuation_dependency_sources = [
            _rejected_deliverable_continuation_dependency_source(
                task_id=self.task_id,
                source=source,
            )
            for source in self.continuation_sources
        ]
        self.dependency_git_sources = _merge_dependency_git_source_rows(
            continuation_dependency_sources,
            _project_repo_task_dependency_git_sources(
                self.batch.context,
                state=self.batch.state,
                task_id=self.task_id,
                managed_repo_id=self.managed_repo_id,
            ),
        )
        self.effective_base_branch = (
            _optional_text(self.task.get("git_base_branch")) or self.batch.base_branch
        )
        self.worktree_base_ref = self.effective_base_branch
        self.pinned_base_commit = _optional_text(self.task.get("git_base_commit_sha"))
        continuation_base_commits = {source.base_commit_sha for source in self.continuation_sources}
        if len(continuation_base_commits) != 1:
            raise DependencyGitPreparationError(
                task_id=self.task_id,
                message=(
                    "unsuperseded rejected deliverables record different original "
                    "Git bases: " + ", ".join(sorted(continuation_base_commits))
                ),
            )
        continuation_base_commit = next(iter(continuation_base_commits))
        if self.pinned_base_commit is not None and self.pinned_base_commit.casefold() != (
            continuation_base_commit
        ):
            raise DependencyGitPreparationError(
                task_id=self.task_id,
                message=(
                    "rejected deliverables "
                    + ", ".join(source.deliverable_id for source in self.continuation_sources)
                    + f" were prepared from {continuation_base_commit}, not task pin "
                    f"{self.pinned_base_commit}"
                ),
            )
        self.prior_base_commit = continuation_base_commit
        self.pinned_base_tree = (
            _managed_git_base_tree_hash(
                self.batch.context,
                task_id=self.task_id,
                managed_repo_id=self.managed_repo_id,
                directory_path=(
                    _optional_text(
                        getattr(
                            getattr(
                                self.batch.state.projects.get(self.batch.project_id), "git", None
                            ),
                            "directory_path",
                            None,
                        )
                    )
                    or "root"
                ),
                base_commit_sha=self.pinned_base_commit,
                sources=continuation_dependency_sources,
            )
            if self.pinned_base_commit is not None
            else None
        )
        # Treat the reviewed candidate as a distinct composition relationship:
        # its immutable pin becomes an ancestor of the replacement branch,
        # while the task's original base provenance remains unchanged.
        self.composition_git_sources = self.dependency_git_sources
        self.composition_manifest_git_sources = self.dependency_git_sources

    def _use_rejected_candidate_base(self) -> None:
        rejected_candidate_git_sources = [
            source.model_dump(mode="json")
            for source in self.correction_source.dependency_git_sources
        ]
        correction_task_git_sources = _project_repo_task_dependency_git_sources(
            self.batch.context,
            state=self.batch.state,
            task_id=self.task_id,
            managed_repo_id=self.managed_repo_id,
        )
        self.dependency_git_sources = _merge_dependency_git_source_rows(
            rejected_candidate_git_sources,
            correction_task_git_sources,
        )
        self.effective_base_branch = self.correction_source.release_branch
        self.worktree_base_ref = self.effective_base_branch
        self.prior_base_commit = self.correction_source.candidate_commit_sha
        self.pinned_base_commit = self.correction_source.candidate_commit_sha
        self.pinned_base_tree = self.correction_source.candidate_tree_hash
        # The rejected candidate already contains its original release
        # sources. Compose only newly accepted correction-task inputs on
        # top of that immutable base, and attest those current inputs in
        # the acceptance manifest.
        self.composition_git_sources = correction_task_git_sources
        self.composition_manifest_git_sources = correction_task_git_sources

    def _use_ordinary_base(self) -> None:
        self.dependency_git_sources = _project_repo_task_dependency_git_sources(
            self.batch.context,
            state=self.batch.state,
            task_id=self.task_id,
            managed_repo_id=self.managed_repo_id,
        )
        self.effective_base_branch = (
            _optional_text(self.task.get("git_base_branch")) or self.batch.base_branch
        )
        self.worktree_base_ref = self.effective_base_branch
        self.pinned_base_commit = _optional_text(self.task.get("git_base_commit_sha"))
        self.pinned_base_tree = None
        self.composition_git_sources = self.dependency_git_sources
        self.composition_manifest_git_sources = self.dependency_git_sources
        accepted_recovery = accepted_recovery_for_target(
            self.batch.state,
            target_task_id=self.task_id,
            dependency_git_sources=self.dependency_git_sources,
        )
        if accepted_recovery is not None:
            recovery_deliverable = self.batch.state.deliverables.get(
                accepted_recovery.accepted_deliverable_id or ""
            )
            if recovery_deliverable is None:
                raise DependencyGitPreparationError(
                    task_id=self.task_id,
                    message=(
                        f"accepted dependency recovery {accepted_recovery.id} "
                        "references a missing deliverable"
                    ),
                )
            self.resolved_dependency_branch = _optional_text(
                recovery_deliverable.git_refs.agent_branch
            )
            self.resolved_dependency_commit_sha = _optional_text(
                accepted_recovery.resolved_commit_sha
            )
            if (
                self.resolved_dependency_branch is None
                or self.resolved_dependency_commit_sha is None
            ):
                raise DependencyGitPreparationError(
                    task_id=self.task_id,
                    message=(
                        f"accepted dependency recovery {accepted_recovery.id} "
                        "has incomplete resolved Git provenance"
                    ),
                )
            self.effective_base_branch = accepted_recovery.base_branch
            self.worktree_base_ref = self.effective_base_branch
            self.pinned_base_commit = accepted_recovery.base_commit_sha
            self.pinned_base_tree = accepted_recovery.base_tree_hash
            self.dependency_composition_mode = "adopt_resolved"
            self.dependency_composition_recovery_id = accepted_recovery.id

    def _plan_worktree(self) -> None:
        self.preparation_generation = _project_repo_task_preparation_generation(
            self.batch.state,
            task_id=self.task_id,
            effective_base_branch=self.effective_base_branch,
        )
        self.activation_sequence = _project_repo_task_preparation_activation(
            self.batch.state,
            task_id=self.task_id,
            preparation_generation=self.preparation_generation,
        )
        # A failed automatic composition may already own a worktree and branch,
        # even though it never produced a preparation receipt. Give the reviewed
        # recovery its own stable identity on both backends, preserving that
        # checkout and keeping retries of the same recovery on the same branch.
        self.candidate_identity_commit = (
            self.continuation_identity_commit
            if self.continuation_identity_commit is not None
            else self.correction_source.candidate_commit_sha
            if self.correction_source is not None
            else self.resolved_dependency_commit_sha
        )
        self.agent_branch = _project_repo_agent_branch(
            self.batch.session_id,
            task_id=self.task_id,
            preparation_generation=self.preparation_generation,
            candidate_commit_sha=self.candidate_identity_commit,
            activation_sequence=self.activation_sequence,
        )
        try:
            if self.dependency_recovery is None:
                recorded_base_commit = _prior_project_repo_task_base_commit(
                    self.batch.state,
                    agent_id=self.batch.agent_id,
                    task_id=self.task_id,
                    agent_branch=self.agent_branch,
                    base_branch=self.effective_base_branch,
                    dependency_git_sources=self.dependency_git_sources,
                    preparation_generation=self.preparation_generation,
                    require_dependency_provenance_match=bool(self.continuation_sources),
                )
                # The rejected-candidate pin remains the immutable composition
                # root, while a retained correction resumes from its recorded
                # composed base after the first preparation.
                if recorded_base_commit is not None:
                    self.prior_base_commit = recorded_base_commit
                elif self.correction_source is None and not self.continuation_sources:
                    self.prior_base_commit = None
        except RuntimeError as exc:
            if self.dependency_git_sources:
                raise DependencyGitPreparationError(
                    task_id=self.task_id,
                    message=str(exc),
                ) from None
            raise
        self.worktree_name = _project_repo_task_worktree_name(
            task_id=self.task_id,
            preparation_generation=self.preparation_generation,
            identity_commit=(self.candidate_identity_commit or self.pinned_base_commit),
            activation_sequence=self.activation_sequence,
        )
        self.directory_path = (
            _optional_text(
                getattr(
                    getattr(self.batch.state.projects.get(self.batch.project_id), "git", None),
                    "directory_path",
                    None,
                )
            )
            or "root"
        )
        selected_backend, _selected_config = selected_execution_backend_for_target(
            self.batch.context,
            {"session_id": self.batch.session_id},
            directory_path=self.directory_path,
        )
        self.execution_backend = (
            _optional_text(self.batch.project_repo.get("execution_backend")) or selected_backend
        )
        self.preparation_attempt_id = _project_repo_git_preparation_attempt_id(
            agent_id=self.batch.agent_id,
            session_id=self.batch.session_id,
            task_id=self.task_id,
            managed_repo_id=self.managed_repo_id,
            branch=self.agent_branch,
            preparation_generation=self.preparation_generation,
        )
        self.expected_worktree_path = (
            f"/home/agent/worktrees/{self.batch.task_worktree_repo_name}/{self.worktree_name}"
        )

    def _reserve_attempt(self) -> None:
        self.batch.state = _ensure_project_repo_git_preparation_attempt(
            self.batch.repo,
            attempt_id=self.preparation_attempt_id,
            agent_id=self.batch.agent_id,
            session_id=self.batch.session_id,
            task_id=self.task_id,
            project_id=self.batch.project_id,
            dispatch_id=self.batch.dispatch_id,
            managed_repo_id=self.managed_repo_id,
            directory_path=self.directory_path,
            execution_backend=self.execution_backend,
            worktree_path=self.expected_worktree_path,
            branch=self.agent_branch,
            base_ref=self.worktree_base_ref or "HEAD",
            preparation_generation=self.preparation_generation,
            activation_sequence=self.activation_sequence,
        )
        preparation_attempt = self.batch.state.git_preparation_attempts[self.preparation_attempt_id]
        self.use_preparation_receipt = (
            preparation_attempt.state != TeamGitPreparationAttemptState.COMMITTED
        )
        if (
            preparation_attempt.state == TeamGitPreparationAttemptState.PREPARED
            and self.prior_base_commit is None
        ):
            self.prior_base_commit = preparation_attempt.base_commit_sha
        self.recovery_already_committed = (
            self.dependency_composition_mode == "adopt_resolved"
            and preparation_attempt.state == TeamGitPreparationAttemptState.COMMITTED
            and self.prior_base_commit == self.resolved_dependency_commit_sha
        )

    def _prepare_worktree(self) -> None:
        self.composition_result: dict[str, Any] = {}
        self.preparation_receipt_result: dict[str, Any] = {}
        try:
            self.retained_work = pin_retained_task_work(self)
            options = dict(
                project_repo=self.batch.project_repo,
                session_id=self.batch.session_id,
                task_id=self.task_id,
                agent_branch=self.agent_branch,
                base_ref=self.worktree_base_ref or "HEAD",
                worktree_name=self.worktree_name,
                **(
                    {"required_git_recovery_protocol": 1}
                    if any(
                        row.task_id == self.task_id
                        and row.preparation_retries
                        or self.task_id in row.recovery_task_ids
                        or self.task_id in row.evidence.get("consumer_successor_ids", {}).values()
                        for row in self.batch.state.git_recovery_incidents.values()
                    )
                    else {}
                ),
                preparation_attempt_id=(
                    self.preparation_attempt_id if self.use_preparation_receipt else None
                ),
                worktree_identity_commit=self.candidate_identity_commit,
                preparation_generation=self.preparation_generation,
                prior_base_commit=self.prior_base_commit,
                pinned_base_commit=self.pinned_base_commit,
                pinned_base_tree=self.pinned_base_tree,
                dependency_git_sources=self.composition_git_sources,
                # After adoption has committed, validate the retained task
                # checkout normally so later agent work is never reset to
                # the recovery candidate. Keep the recovery on the Team
                # context below, where it remains part of the provenance.
                dependency_composition_mode=(
                    "automatic"
                    if self.recovery_already_committed
                    else self.dependency_composition_mode
                ),
                dependency_composition_recovery_id=(
                    None
                    if self.recovery_already_committed
                    else self.dependency_composition_recovery_id
                ),
                resolved_dependency_branch=(
                    None if self.recovery_already_committed else self.resolved_dependency_branch
                ),
                resolved_dependency_commit_sha=(
                    None if self.recovery_already_committed else self.resolved_dependency_commit_sha
                ),
                dependency_merge_author_identity=self.task_author,
                task_author_identity=self.task_author,
                composition_result=self.composition_result,
                preparation_receipt_result=self.preparation_receipt_result,
            )
            attempt = self.batch.state.git_preparation_attempts[self.preparation_attempt_id]
            if (
                self.retained_work is not None
                and attempt.state == TeamGitPreparationAttemptState.PENDING
            ):
                # Do not seal a worker receipt at the old failed candidate. First
                # restore the pinned work, then let normal preparation attest it.
                path, base = _prepare_project_repo_task_worktree(
                    self.batch.context,
                    **{
                        **options,
                        "preparation_attempt_id": None,
                        "prior_base_commit": retained_target_base(self),
                        "preparation_receipt_result": {},
                        "composition_result": {},
                    },
                )
                apply_retained_task_work(self, worktree_path=path, base_commit=base)
                options["prior_base_commit"] = base
            self.worktree_path, self.base_commit = _prepare_project_repo_task_worktree(
                self.batch.context,
                **options,
            )
        except DependencyGitPreparationError as exc:
            if exc.conflict is not None:
                project = self.batch.state.projects.get(self.batch.project_id)
                project_git = getattr(project, "git", None)
                exc.conflict.update(
                    {
                        "target_task_id": self.task_id,
                        "project_id": self.batch.project_id,
                        "managed_repo_id": self.managed_repo_id,
                        "base_branch": self.effective_base_branch or "HEAD",
                        "directory_path": (
                            _optional_text(getattr(project_git, "directory_path", None)) or "root"
                        ),
                    }
                )
            raise

    def _record_receipt(self) -> None:
        composed_head_commit = _optional_text(self.composition_result.get("composed_commit_sha"))
        task_base_commit_pin = _optional_text(self.task.get("git_base_commit_sha"))
        self.recorded_work_context_base_commit = self.base_commit
        if self.continuation_sources and self.pinned_base_commit is not None:
            self.recorded_work_context_base_commit = self.pinned_base_commit
        if task_base_commit_pin is not None:
            self.recorded_work_context_base_commit = task_base_commit_pin
        if self.resolved_dependency_commit_sha is not None:
            # The task pin remains its required root; this context starts
            # at the separately reviewed merge and must reuse that identity.
            self.recorded_work_context_base_commit = self.resolved_dependency_commit_sha
        prepared_head_commit = (
            _optional_text(self.preparation_receipt_result.get("head_commit_sha"))
            or composed_head_commit
            or self.base_commit
        )
        if self.use_preparation_receipt:
            self.preparation_receipt_result.setdefault(
                "request_fingerprint",
                "provider:"
                + hashlib.sha256(
                    json.dumps(
                        {
                            "attempt_id": self.preparation_attempt_id,
                            "worktree_path": self.worktree_path,
                            "base_commit": self.base_commit,
                            "head_commit": prepared_head_commit,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            )
            self.preparation_receipt_result.setdefault(
                "head_commit_sha",
                prepared_head_commit,
            )
            self.batch.state = _record_project_repo_git_preparation_receipt(
                self.batch.repo,
                attempt_id=self.preparation_attempt_id,
                receipt=self.preparation_receipt_result,
                # The preparation receipt describes the physical checkout.
                # A pinned provenance root can intentionally differ from
                # evander.baseCommit after dependency composition.
                base_commit_sha=self.base_commit,
                worktree_path=self.worktree_path,
            )

    def _build_context_provenance(self) -> None:
        self.dependency_composition_manifest = _build_dependency_composition_manifest(
            self.batch.context,
            task_id=self.task_id,
            managed_repo_id=self.managed_repo_id,
            directory_path=(
                _optional_text(
                    getattr(
                        getattr(self.batch.state.projects.get(self.batch.project_id), "git", None),
                        "directory_path",
                        None,
                    )
                )
                or "root"
            ),
            base_branch=self.effective_base_branch or "HEAD",
            dependency_git_sources=self.composition_manifest_git_sources,
            composition_result=self.composition_result,
        )
        self.context_id = _project_repo_git_work_context_id(
            agent_id=self.batch.agent_id,
            task_id=self.task_id,
            dispatch_id=self.batch.dispatch_id,
        )

    def _validate_prepared_dependency_provenance(self, current_state: Any) -> None:
        validate_handoff_writers(self, current_state)
        current_correction_source = _verified_rejected_candidate_git_source(
            self.batch.context,
            state=current_state,
            task_id=self.task_id,
            managed_repo_id=self.managed_repo_id,
        )
        current_continuation_sources = _verified_rejected_deliverable_continuation_git_sources(
            self.batch.context,
            state=current_state,
            task_id=self.task_id,
            managed_repo_id=self.managed_repo_id,
        )
        current_dependency_recovery = recovery_for_task(
            current_state,
            recovery_task_id=self.task_id,
        )
        if current_dependency_recovery is not None:
            current_dependency_git_sources = [
                source.model_dump(mode="json")
                for source in current_dependency_recovery.dependency_git_sources
            ]
        elif current_continuation_sources:
            current_dependency_git_sources = _merge_dependency_git_source_rows(
                [
                    _rejected_deliverable_continuation_dependency_source(
                        task_id=self.task_id, source=source
                    )
                    for source in current_continuation_sources
                ],
                _project_repo_task_dependency_git_sources(
                    self.batch.context,
                    state=current_state,
                    task_id=self.task_id,
                    managed_repo_id=self.managed_repo_id,
                ),
            )
        elif current_correction_source is not None:
            current_dependency_git_sources = _merge_dependency_git_source_rows(
                [
                    source.model_dump(mode="json")
                    for source in current_correction_source.dependency_git_sources
                ],
                _project_repo_task_dependency_git_sources(
                    self.batch.context,
                    state=current_state,
                    task_id=self.task_id,
                    managed_repo_id=self.managed_repo_id,
                ),
            )
        else:
            current_dependency_git_sources = _project_repo_task_dependency_git_sources(
                self.batch.context,
                state=current_state,
                task_id=self.task_id,
                managed_repo_id=self.managed_repo_id,
            )
        if current_correction_source != self.correction_source:
            raise DependencyGitPreparationError(
                task_id=self.task_id,
                message=(
                    f"task {self.task_id} rejected-candidate Git source changed "
                    "during worktree preparation; no lease was granted"
                ),
            )
        if current_continuation_sources != self.continuation_sources:
            raise DependencyGitPreparationError(
                task_id=self.task_id,
                message=(
                    f"task {self.task_id} rejected-deliverable continuation source "
                    "changed during worktree preparation; no lease was granted"
                ),
            )
        if current_dependency_recovery != self.dependency_recovery:
            raise DependencyGitPreparationError(
                task_id=self.task_id,
                message=(
                    f"task {self.task_id} dependency recovery changed during "
                    "worktree preparation; no lease was granted"
                ),
            )
        if current_dependency_git_sources != self.dependency_git_sources:
            raise DependencyGitPreparationError(
                task_id=self.task_id,
                message=(
                    f"task {self.task_id} dependency Git state changed during "
                    "worktree preparation; no lease was granted"
                ),
            )
        current_preparation_generation = _project_repo_task_preparation_generation(
            current_state,
            task_id=self.task_id,
            effective_base_branch=self.effective_base_branch,
        )
        if current_preparation_generation != self.preparation_generation:
            raise DependencyGitPreparationError(
                task_id=self.task_id,
                message=(
                    f"task {self.task_id} preparation generation changed during "
                    f"worktree preparation from {self.preparation_generation} to "
                    f"{current_preparation_generation}; no lease was granted"
                ),
            )
        if _project_repo_task_preparation_activation(
            current_state,
            task_id=self.task_id,
            preparation_generation=current_preparation_generation,
        ) != self.activation_sequence:
            raise DependencyGitPreparationError(
                task_id=self.task_id,
                message="Task preparation activation changed during worktree preparation; no lease was granted",
            )

    def _commit_context(self) -> None:
        last_conflict: TeamStateConflictError | None = None
        for _attempt in range(TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS):
            self.batch.state_commit_attempt_count += 1
            self.batch.state = self.batch.repo.read()
            runtime = self.batch.state.agent_runtimes.get(self.batch.agent_id)
            if runtime is None or runtime.active_dispatch_id != self.batch.dispatch_id:
                raise RuntimeError(
                    f"dispatch {self.batch.dispatch_id} no longer owns the wake lease for agent {self.batch.agent_id}"
                )
            self._validate_prepared_dependency_provenance(self.batch.state)
            existing = self.batch.state.git_work_contexts.get(self.context_id)
            if existing is not None:
                _validate_project_repo_git_work_context(
                    existing,
                    agent_id=self.batch.agent_id,
                    task_id=self.task_id,
                    dispatch_id=self.batch.dispatch_id,
                    agent_branch=self.agent_branch,
                    base_branch=self.effective_base_branch,
                    base_commit=self.recorded_work_context_base_commit,
                    worktree_path=self.worktree_path,
                    dependency_git_sources=self.dependency_git_sources,
                    dependency_composition_recovery_id=(self.dependency_composition_recovery_id),
                    manual_dependency_git_sources=self.manual_dependency_git_sources,
                    dependency_composition_manifest=(self.dependency_composition_manifest),
                    preparation_generation=task_git_work_context_generation(
                        self.batch.state,
                        self.task_id,
                    ),
                    retained_work=self.retained_work,
                )
                break
            try:
                self.batch.state, self.context_id = (
                    self.batch.admin.create_git_work_context_with_id(
                        context_id=self.context_id,
                        agent_id=self.batch.agent_id,
                        task_id=self.task_id,
                        agent_branch=self.agent_branch,
                        base_branch=self.effective_base_branch,
                        base_commit=self.recorded_work_context_base_commit,
                        dispatch_id=self.batch.dispatch_id,
                        worktree_path=self.worktree_path,
                        expected_preparation_generation=self.preparation_generation,
                        preparation_attempt_id=(
                            self.preparation_attempt_id if self.use_preparation_receipt else None
                        ),
                        dependency_git_sources=self.dependency_git_sources,
                        dependency_composition_recovery_id=(
                            self.dependency_composition_recovery_id
                        ),
                        manual_dependency_git_sources=self.manual_dependency_git_sources,
                        dependency_composition_manifest=(self.dependency_composition_manifest),
                        retained_work=self.retained_work,
                        resource_provenance_validator=(
                            self._validate_prepared_dependency_provenance
                        ),
                        expected_revision=None,
                    )
                )
                break
            except TeamStateConflictError as exc:
                last_conflict = exc
                self.batch.state_conflict_retry_count += 1
        else:
            assert last_conflict is not None
            raise last_conflict

    def _finish_context(self) -> None:
        from .services.domain.git_recovery import mark_preparation_resumed

        if any(
            row.task_id == self.task_id and row.state in {"preparing", "awaiting_review"}
            for row in self.batch.state.git_recovery_incidents.values()
        ):

            def _resumed(current):
                self._validate_prepared_dependency_provenance(current)
                mark_preparation_resumed(current, self.task_id, context_id=self.context_id)
                return current

            self.batch.state = self.batch.repo.update(_resumed)
        context_model = self.batch.state.git_work_contexts[self.context_id]
        register = getattr(self.batch.context.admin_service, "register_git_submission_checkout", None)
        if callable(register):
            checkout_id = register(
                session_id=self.batch.session_id, managed_repo_id=self.managed_repo_id,
                guest_path=self.worktree_path, owner_kind="team-addon", owner_id=self.context_id,
                repository_name=self.batch.project_repo.get("guest_repo_name"),
                generation=context_model.preparation_generation or "",
                fixed_branch=context_model.agent_branch, prepared_base=self.base_commit,
                provenance_base=context_model.base_commit,
                base_branch=context_model.base_branch,
                preparation_attempt_id=self.preparation_attempt_id,
            )
            if checkout_id and context_model.checkout_id != checkout_id:
                def bind(current):
                    current.git_work_contexts[self.context_id].checkout_id = checkout_id
                    return current
                self.batch.state = self.batch.repo.update(bind)
                context_model = self.batch.state.git_work_contexts[self.context_id]
        self.batch.rows.append(_git_work_context_prompt_payload(context_model))


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
    retained_work: Any = None,
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
        "retained_work": retained_work,
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
    activation_sequence: int = 0,
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
    activation_suffix = f"-run-{activation_sequence}" if activation_sequence else ""
    return validate_branch_name(
        f"agents/{session_id}/tasks/{task_slug}-{task_hash}{candidate_suffix}{generation_suffix}{activation_suffix}"
    )
