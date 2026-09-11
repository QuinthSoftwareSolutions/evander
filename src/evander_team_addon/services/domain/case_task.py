from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from ...models import (
    AgentTask,
    AdoWorkItemAttribution,
    PersistentAgent,
    ProjectGitIdentity,
    RolePermission,
    TeamCase,
    TeamCaseStatus,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    TeamTaskKind,
    TaskDeliverableContract,
    TaskOutcomeContract,
    TaskResolutionStatus,
    _unique_text_list,
    agent_has_project_assignment,
)
from ...git_naming import validate_branch_name

from .agent_access import (
    _agent_has_role_permission_for_project,
    _agent_in_project_hierarchy,
    _is_direct_subordinate,
    _is_project_lead_for,
    _is_team_manager,
    _supervises,
)
from .common import (
    _NON_TERMINAL_TASK_STATES,
    _append_unique,
    _generated_id,
    _mapping,
    _optional_text,
    _release_candidate_branch,
    _require_absent,
    _require_present,
    _required_text,
    _string_list,
    task_state_can_wake_owner,
    utc_now,
)
from .task_authorship import inherited_task_author, task_author_identity, inherited_work_item_attributions
from .work_due import _mark_agent_work_due
from .task_assessment import assess_task
from .case_events import append_case_closed_event
from .work_lineage import ensure_task_work_lineage


_POST_RELEASE_FEEDBACK_SOURCE = "post_release_feedback"
_POST_RELEASE_PROVENANCE_METADATA_KEYS = frozenset(
    {
        "evidence_refs",
        "release_package_id",
        "reporter_display_name",
        "reporter_user_id",
    }
)


@dataclass(frozen=True)
class _TaskCreationPlan:
    task_id: str
    owner_id: str | None
    project_id: str
    case_id: str | None
    task_state: TeamTaskState
    parent_task_id: str | None
    originating_task_id: str | None
    git_base_branch: str | None
    dependency_task_ids: list[str]
    priority: int
    title: str
    description: str
    task_kind: TeamTaskKind
    outcome_contract: TaskOutcomeContract | None
    integration_boundary: bool
    deliverable_contract: TaskDeliverableContract | None
    authored_by: ProjectGitIdentity | None = None
    work_item_attributions: list[AdoWorkItemAttribution] | None = None


class TaskGitBaseValidationError(ValueError):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TaskGitDefaultBranchResolver(Protocol):
    def __call__(
        self,
        *,
        managed_repo_id: str,
        directory_path: str,
    ) -> object: ...


def _normalized_task_git_base_branch(value: object) -> str | None:
    normalized = _optional_text(value)
    return validate_branch_name(normalized) if normalized is not None else None


def _project_default_task_git_base_branch(
    state: TeamState,
    *,
    project_id: str,
    git_default_branch_resolver: TaskGitDefaultBranchResolver | None,
) -> str | None:
    project = _require_present(state.projects, project_id, "project")
    if project.git is None:
        return None
    configured = _normalized_task_git_base_branch(project.git.base_branch)
    if configured is not None:
        return configured
    if git_default_branch_resolver is None:
        raise TaskGitBaseValidationError(
            code="git_base_unavailable",
            message=(
                f"project {project.id} does not select a Git base branch and "
                "managed repository default-branch resolution is unavailable"
            ),
        )
    try:
        resolved = _normalized_task_git_base_branch(
            git_default_branch_resolver(
                managed_repo_id=project.git.managed_repo_id,
                directory_path=project.git.directory_path,
            )
        )
    except Exception as exc:
        raise TaskGitBaseValidationError(
            code="git_base_unavailable",
            message=(
                f"could not resolve the default branch for managed repository "
                f"{project.git.managed_repo_id}: {exc}"
            ),
        ) from exc
    if resolved is None:
        raise TaskGitBaseValidationError(
            code="git_base_unavailable",
            message=(
                f"managed repository {project.git.managed_repo_id} has no concrete default branch"
            ),
        )
    return resolved


def _correction_case_git_base_branch(
    state: TeamState,
    *,
    case_id: str | None,
) -> str | None:
    case = state.cases.get(case_id) if case_id is not None else None
    if case is None:
        return None
    release_id: str | None = None
    if case.source == "human_release_review" and case.source_ref is not None:
        request = state.human_review_requests.get(case.source_ref)
        release_id = request.release_package_id if request is not None else None
    elif case.source == "post_release_feedback":
        release_id = _optional_text(case.metadata.get("release_package_id"))
    release = state.release_packages.get(release_id) if release_id is not None else None
    return (
        _release_candidate_branch(release) or release.git_base_branch
        if release is not None
        else None
    )


def _task_git_base_branch_locked(state: TeamState, *, task: AgentTask) -> bool:
    if task.deliverable_ids:
        return True
    if any(context.task_id == task.id for context in state.git_work_contexts.values()):
        return True
    deliverable_ids = {
        deliverable.id
        for deliverable in state.deliverables.values()
        if deliverable.task_id == task.id
    }
    if deliverable_ids:
        return True
    return any(
        promotion.task_id == task.id
        or (promotion.deliverable_id is not None and promotion.deliverable_id in deliverable_ids)
        for promotion in state.git_promotions.values()
    )


def _set_task_git_base_branch(
    state: TeamState,
    *,
    task: AgentTask,
    git_base_branch: object,
    git_default_branch_resolver: TaskGitDefaultBranchResolver | None = None,
) -> bool:
    normalized = _normalized_task_git_base_branch(git_base_branch)
    project = _require_present(state.projects, task.project_id, "project")
    correction_base = _correction_case_git_base_branch(
        state,
        case_id=task.case_id,
    )
    if correction_base is not None:
        if normalized is not None and normalized != correction_base:
            raise TaskGitBaseValidationError(
                code="git_base_invalid",
                message=(
                    f"correction task {task.id} must remain based on rejected "
                    f"candidate branch {correction_base}"
                ),
            )
        normalized = correction_base
    elif normalized is None and project.git is not None:
        parent = state.tasks.get(task.parent_task_id) if task.parent_task_id is not None else None
        normalized = (
            _normalized_task_git_base_branch(parent.git_base_branch)
            if parent is not None and parent.git_base_branch is not None
            else _project_default_task_git_base_branch(
                state,
                project_id=project.id,
                git_default_branch_resolver=git_default_branch_resolver,
            )
        )
    if normalized is not None and project.git is None:
        raise TaskGitBaseValidationError(
            code="git_base_invalid",
            message=(
                f"task {task.id} cannot select git_base_branch because project "
                f"{project.id} has no Git configuration"
            ),
        )
    if normalized == task.git_base_branch:
        return False
    if _task_git_base_branch_locked(state, task=task):
        raise TaskGitBaseValidationError(
            code="git_base_locked",
            message=(
                f"task {task.id} git_base_branch is locked after Git preparation, "
                "deliverable submission, or promotion creation"
            ),
        )
    task.git_base_branch = normalized
    return True


def _resolve_new_task_git_base_branch(
    state: TeamState,
    *,
    project_id: str,
    explicit_git_base_branch: object = None,
    parent_task_id: str | None = None,
    case_id: str | None = None,
    git_default_branch_resolver: TaskGitDefaultBranchResolver | None = None,
) -> str | None:
    project = _require_present(state.projects, project_id, "project")
    correction_base = _correction_case_git_base_branch(state, case_id=case_id)
    if correction_base is not None:
        return correction_base
    explicit = _normalized_task_git_base_branch(explicit_git_base_branch)
    if explicit is not None:
        if project.git is None:
            raise ValueError(
                f"task cannot select git_base_branch because project {project.id} "
                "has no Git configuration"
            )
        return explicit
    if parent_task_id is not None:
        parent = _require_present(state.tasks, parent_task_id, "task parent")
        if parent.project_id != project_id:
            raise ValueError(f"task parent {parent.id} belongs to another project")
        if parent.git_base_branch is not None:
            return parent.git_base_branch
    return _project_default_task_git_base_branch(
        state,
        project_id=project.id,
        git_default_branch_resolver=git_default_branch_resolver,
    )


def inherited_task_case_id(
    state: TeamState,
    *,
    project_id: str,
    case_id: str | None = None,
    originating_task_id: str | None = None,
    parent_task_id: str | None = None,
    plans: Mapping[str, _TaskCreationPlan] | None = None,
    seen: frozenset[str] = frozenset(),
    resolved: dict[tuple[str, str], str | None] | None = None,
) -> str | None:
    """Preserve explicit cases; otherwise follow origin, then parent, within a project."""
    if resolved is None:
        resolved = {}
    if case_id is not None:
        case = _require_present(state.cases, case_id, "case")
        project = _require_present(state.projects, project_id, "project")
        if case.team_id != project.team_id or case.project_id not in (None, project_id):
            raise ValueError(
                f"task in project {project_id} cannot be linked to case {case_id}: "
                "case belongs to another project or team"
            )
        return case_id
    for source_id in dict.fromkeys((originating_task_id, parent_task_id)):
        if source_id is None:
            continue
        if source_id in seen:
            raise ValueError("task case lineage cycle")
        key = (project_id, source_id)
        if key not in resolved:
            source = (plans or {}).get(source_id) or state.tasks.get(source_id)
            if source is None:
                kind = "originating task" if source_id == originating_task_id else "parent"
                raise ValueError(f"task references missing {kind} {source_id}")
            if source.project_id != project_id:
                raise ValueError("task case source belongs to another project")
            resolved[key] = inherited_task_case_id(
                state,
                project_id=project_id,
                case_id=source.case_id,
                originating_task_id=source.originating_task_id,
                parent_task_id=source.parent_task_id,
                plans=plans,
                seen=seen | {source_id},
                resolved=resolved,
            )
        inherited = resolved[key]
        if inherited is not None:
            return inherited
    return None


def _resolve_task_creation_plan_git_bases(
    state: TeamState,
    plans: list[_TaskCreationPlan],
    *,
    git_default_branch_resolver: TaskGitDefaultBranchResolver | None = None,
) -> list[_TaskCreationPlan]:
    """Resolve cases, Git bases and task authors across a creation batch atomically."""

    original_plans = {plan.task_id: plan for plan in plans}
    resolved_cases: dict[tuple[str, str], str | None] = {}
    plans = [
        replace(
            plan,
            case_id=inherited_task_case_id(
                state,
                project_id=plan.project_id,
                case_id=plan.case_id,
                originating_task_id=plan.originating_task_id,
                parent_task_id=plan.parent_task_id,
                plans=original_plans,
                seen=frozenset({plan.task_id}),
                resolved=resolved_cases,
            ),
        )
        for plan in plans
    ]

    plans_by_id = {plan.task_id: plan for plan in plans}
    resolved: dict[str, str | None] = {}
    resolving: set[str] = set()
    project_defaults: dict[str, str | None] = {}

    def _project_default(project_id: str) -> str | None:
        if project_id not in project_defaults:
            project_defaults[project_id] = _project_default_task_git_base_branch(
                state,
                project_id=project_id,
                git_default_branch_resolver=git_default_branch_resolver,
            )
        return project_defaults[project_id]

    def _resolve(plan: _TaskCreationPlan) -> str | None:
        if plan.task_id in resolved:
            return resolved[plan.task_id]
        if plan.task_id in resolving:
            raise ValueError(f"task parent cycle includes {plan.task_id}")
        resolving.add(plan.task_id)
        project = _require_present(state.projects, plan.project_id, "project")
        correction_base = _correction_case_git_base_branch(
            state,
            case_id=plan.case_id,
        )
        if correction_base is not None:
            value = correction_base
        elif plan.git_base_branch is not None:
            if project.git is None:
                raise ValueError(
                    f"task {plan.task_id} cannot select git_base_branch because "
                    f"project {project.id} has no Git configuration"
                )
            value = _normalized_task_git_base_branch(plan.git_base_branch)
        elif plan.parent_task_id is not None:
            planned_parent = plans_by_id.get(plan.parent_task_id)
            if planned_parent is not None:
                if planned_parent.project_id != plan.project_id:
                    raise ValueError(
                        f"task parent {planned_parent.task_id} belongs to another project"
                    )
                value = _resolve(planned_parent)
            else:
                parent = state.tasks.get(plan.parent_task_id)
                value = parent.git_base_branch if parent is not None else None
                if value is None and project.git is not None:
                    value = _project_default(project.id)
        else:
            value = _project_default(project.id)
        resolving.remove(plan.task_id)
        resolved[plan.task_id] = _normalized_task_git_base_branch(value)
        return resolved[plan.task_id]

    authors: dict[str, ProjectGitIdentity | None] = {}
    resolving_authors: set[str] = set()

    def _resolve_author(plan: _TaskCreationPlan) -> ProjectGitIdentity | None:
        if plan.task_id in authors:
            return authors[plan.task_id]
        if plan.task_id in resolving_authors:
            raise ValueError(f"task authorship lineage cycle includes {plan.task_id}")
        resolving_authors.add(plan.task_id)
        identity = plan.authored_by
        if identity is None:
            source_id = plan.originating_task_id or plan.parent_task_id
            source_plan = plans_by_id.get(source_id or "")
            if source_plan is not None:
                if source_plan.project_id != plan.project_id:
                    raise ValueError("task author source belongs to another project")
                identity = _resolve_author(source_plan)
            else:
                identity = inherited_task_author(
                    state,
                    project_id=plan.project_id,
                    originating_task_id=plan.originating_task_id,
                    parent_task_id=plan.parent_task_id,
                )
        resolving_authors.remove(plan.task_id)
        authors[plan.task_id] = identity.model_copy(deep=True) if identity else None
        return authors[plan.task_id]

    def _resolve_attribution(plan: _TaskCreationPlan, seen: frozenset[str] = frozenset()) -> list[AdoWorkItemAttribution] | None:
        if plan.task_id in seen:
            raise ValueError("task attribution lineage cycle")
        source_plan = plans_by_id.get(plan.originating_task_id or plan.parent_task_id or "")
        if source_plan:
            if source_plan.project_id != plan.project_id:
                raise ValueError("task attribution source belongs to another project")
            return _resolve_attribution(source_plan, seen | {plan.task_id})
        return inherited_work_item_attributions(state, project_id=plan.project_id,
            originating_task_id=plan.originating_task_id, parent_task_id=plan.parent_task_id)

    return [
        replace(plan, git_base_branch=_resolve(plan), authored_by=_resolve_author(plan),
                work_item_attributions=_resolve_attribution(plan))
        for plan in plans
    ]


def _validate_case_status_transition(
    *,
    source: str | None,
    current_status: TeamCaseStatus | None,
    next_status: TeamCaseStatus,
) -> None:
    terminal_statuses = {TeamCaseStatus.RESOLVED, TeamCaseStatus.CLOSED}
    if (
        source == _POST_RELEASE_FEEDBACK_SOURCE
        and next_status != current_status
        and (next_status in terminal_statuses or current_status in terminal_statuses)
    ):
        raise ValueError(
            "post-release feedback cases require the evidence-backed post-release close operation"
        )


def _validate_generic_case_source(source: str | None) -> None:
    if source == _POST_RELEASE_FEEDBACK_SOURCE:
        raise ValueError(
            "post-release feedback case source is reserved for the evidence-backed report operation"
        )


def _validate_protected_case_update(
    state: TeamState,
    *,
    case: TeamCase,
    next_project_id: str | None,
    next_source: str | None,
    source_ref_present: bool,
    next_source_ref: str | None,
    metadata_present: bool,
    next_metadata: Mapping[str, Any] | None,
    next_task_ids: list[str] | None,
) -> None:
    if case.source != _POST_RELEASE_FEEDBACK_SOURCE:
        _validate_generic_case_source(next_source)
        return
    if next_project_id != case.project_id:
        raise ValueError("post-release feedback case project is immutable")
    if next_source != case.source:
        raise ValueError("post-release feedback case source is immutable")
    if source_ref_present and next_source_ref != case.source_ref:
        raise ValueError("post-release feedback case source_ref is immutable")
    if metadata_present:
        candidate_metadata = dict(next_metadata or {})
        protected_keys = set(_POST_RELEASE_PROVENANCE_METADATA_KEYS)
        if "post_release_feedback_resolution" in case.metadata:
            protected_keys.add("post_release_feedback_resolution")
        changed_keys = sorted(
            key for key in protected_keys if candidate_metadata.get(key) != case.metadata.get(key)
        )
        if changed_keys:
            raise ValueError(
                "post-release feedback case provenance metadata is immutable: "
                + ", ".join(changed_keys)
            )
    if next_task_ids is None:
        return
    unresolved_removed_task_ids = sorted(
        task_id
        for task_id in case.task_ids
        if task_id not in next_task_ids
        and (
            task_id not in state.tasks
            or assess_task(state, task_id).resolution_status != TaskResolutionStatus.RESOLVED
        )
    )
    if unresolved_removed_task_ids:
        raise ValueError(
            "post-release feedback case cannot remove unresolved tasks: "
            + ", ".join(unresolved_removed_task_ids)
        )


def _validate_case_scope(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    owner_agent_id: str | None,
) -> None:
    _require_present(state.teams, team_id, "team")
    if project_id is not None:
        project = _require_present(state.projects, project_id, "project")
        if project.team_id != team_id:
            raise ValueError(f"project {project_id} does not belong to team {team_id}")
    if owner_agent_id is not None:
        owner = _require_present(state.agents, owner_agent_id, "case owner")
        if owner.team_id != team_id:
            raise ValueError(f"case owner {owner_agent_id} does not belong to team {team_id}")


def _case_can_link_task(state: TeamState, *, case: TeamCase, task: AgentTask) -> bool:
    project = state.projects.get(task.project_id)
    if project is None:
        return False
    if project.team_id != case.team_id:
        return False
    if case.project_id is not None and task.project_id != case.project_id:
        return False
    return True


def _link_task_to_case(
    state: TeamState,
    *,
    task_id: str,
    case_id: str,
    now: datetime,
) -> None:
    case = _require_present(state.cases, case_id, "case")
    task = _require_present(state.tasks, task_id, "task")
    if not _case_can_link_task(state, case=case, task=task):
        raise ValueError(f"task {task_id} cannot be linked to case {case_id}")
    if task.case_id is not None and task.case_id != case_id:
        _unlink_task_from_case(state, task_id=task_id, case_id=task.case_id, now=now)
    task.case_id = case_id
    task.updated_at = now
    case.task_ids = _append_unique(case.task_ids, task_id)
    case.updated_at = now


def _unlink_task_from_case(
    state: TeamState,
    *,
    task_id: str,
    case_id: str,
    now: datetime,
) -> None:
    case = state.cases.get(case_id)
    if case is not None and task_id in case.task_ids:
        _validate_protected_case_update(
            state,
            case=case,
            next_project_id=case.project_id,
            next_source=case.source,
            source_ref_present=False,
            next_source_ref=case.source_ref,
            metadata_present=False,
            next_metadata=None,
            next_task_ids=[item for item in case.task_ids if item != task_id],
        )
        case.task_ids = [item for item in case.task_ids if item != task_id]
        case.updated_at = now
    task = state.tasks.get(task_id)
    if task is not None and task.case_id == case_id:
        task.case_id = None
        task.updated_at = now


def _sync_case_task_link(
    state: TeamState,
    *,
    task: AgentTask,
    old_case_id: str | None,
    new_case_id: str | None,
    now: datetime,
) -> None:
    if old_case_id == new_case_id:
        if new_case_id is not None:
            _link_task_to_case(state, task_id=task.id, case_id=new_case_id, now=now)
        return
    if old_case_id is not None:
        _unlink_task_from_case(state, task_id=task.id, case_id=old_case_id, now=now)
    if new_case_id is not None:
        _link_task_to_case(state, task_id=task.id, case_id=new_case_id, now=now)


def _snapshot_case_task_state(
    state: TeamState,
) -> tuple[dict[str, TeamCase], dict[str, AgentTask]]:
    return (
        {case_id: case.model_copy(deep=True) for case_id, case in state.cases.items()},
        {task_id: task.model_copy(deep=True) for task_id, task in state.tasks.items()},
    )


def _restore_case_task_state(
    state: TeamState,
    snapshot: tuple[dict[str, TeamCase], dict[str, AgentTask]],
) -> None:
    state.cases, state.tasks = snapshot


def _apply_case_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: Mapping[str, Any],
    case_closed_events: list[Mapping[str, Any] | dict[str, Any]] | None = None,
) -> tuple[str, bool]:
    raw_case_id = _optional_text(update.get("case_id")) or _optional_text(update.get("id"))
    now = utc_now()
    if raw_case_id is not None and raw_case_id in state.cases:
        case = state.cases[raw_case_id]
        previous_status = case.status
        error = _case_edit_error(state, actor=actor, case=case)
        if error is not None:
            raise ValueError(error)
        next_project_id = case.project_id
        next_owner_agent_id = case.owner_agent_id
        next_source = case.source
        if "project_id" in update:
            next_project_id = _optional_text(update.get("project_id"))
        if "owner_agent_id" in update:
            next_owner_agent_id = _optional_text(update.get("owner_agent_id"))
        if "source" in update:
            next_source = _optional_text(update.get("source"))
        next_source_ref = (
            _optional_text(update.get("source_ref")) if "source_ref" in update else case.source_ref
        )
        next_metadata = (
            _mapping(update.get("metadata") or {}, "metadata") if "metadata" in update else None
        )
        next_task_ids = (
            _unique_text_list(update.get("task_ids"), "task_ids") if "task_ids" in update else None
        )
        _validate_case_scope(
            state,
            team_id=case.team_id,
            project_id=next_project_id,
            owner_agent_id=next_owner_agent_id,
        )
        _validate_protected_case_update(
            state,
            case=case,
            next_project_id=next_project_id,
            next_source=next_source,
            source_ref_present="source_ref" in update,
            next_source_ref=next_source_ref,
            metadata_present="metadata" in update,
            next_metadata=next_metadata,
            next_task_ids=next_task_ids,
        )
        if "title" in update:
            case.title = _required_text(update.get("title"), "title")
        if "description" in update:
            case.description = _optional_text(update.get("description")) or ""
        if "project_id" in update:
            case.project_id = next_project_id
        if "status" in update:
            previous_status = case.status
            next_status = TeamCaseStatus(_required_text(update.get("status"), "status"))
            _validate_case_status_transition(
                source=case.source,
                current_status=previous_status,
                next_status=next_status,
            )
            if next_source != case.source:
                _validate_case_status_transition(
                    source=next_source,
                    current_status=previous_status,
                    next_status=next_status,
                )
            case.status = next_status
            if case.status == TeamCaseStatus.CLOSED and previous_status != TeamCaseStatus.CLOSED:
                case.closed_at = now
            elif case.status != TeamCaseStatus.CLOSED:
                case.closed_at = None
        if "priority" in update:
            case.priority = TeamInboxPriority(_required_text(update.get("priority"), "priority"))
        if "owner_agent_id" in update:
            case.owner_agent_id = next_owner_agent_id
        if "source" in update:
            case.source = next_source
        if "source_ref" in update:
            case.source_ref = next_source_ref
        if "tags" in update:
            case.tags = _unique_text_list(update.get("tags"), "tags")
        if "metadata" in update:
            case.metadata = dict(next_metadata or {})
        if "task_ids" in update:
            for task_id in list(case.task_ids):
                if task_id not in (next_task_ids or []):
                    _unlink_task_from_case(state, task_id=task_id, case_id=case.id, now=now)
            for task_id in next_task_ids or []:
                _link_task_to_case(state, task_id=task_id, case_id=case.id, now=now)
        else:
            for task_id in list(case.task_ids):
                task = state.tasks.get(task_id)
                if task is not None and not _case_can_link_task(state, case=case, task=task):
                        _unlink_task_from_case(state, task_id=task_id, case_id=case.id, now=now)
        case.updated_at = now
        if case_closed_events is not None:
            append_case_closed_event(
                case_closed_events,
                case,
                previous_status=previous_status,
            )
        return case.id, False

    team_id = _optional_text(update.get("team_id")) or actor.team_id
    if team_id is None:
        raise ValueError("case team_id is required for teamless agents")
    if actor.team_id != team_id:
        raise ValueError(f"agent {actor.id} cannot create a case for team {team_id}")
    project_id = _optional_text(update.get("project_id"))
    if project_id is not None and not (
        _is_team_manager(state, actor) or agent_has_project_assignment(state, actor.id, project_id)
    ):
        raise ValueError(f"agent {actor.id} cannot create a case for project {project_id}")
    owner_agent_id = _optional_text(update.get("owner_agent_id"))
    _validate_case_scope(
        state,
        team_id=team_id,
        project_id=project_id,
        owner_agent_id=owner_agent_id,
    )
    title = _required_text(update.get("title"), "title")
    case_id = raw_case_id or _generated_id(
        prefix="case",
        labels=[project_id or team_id, title],
        existing_ids=state.cases,
    )
    _require_absent(state.cases, case_id, "case")
    status = TeamCaseStatus(_optional_text(update.get("status")) or TeamCaseStatus.OPEN.value)
    source = _optional_text(update.get("source"))
    _validate_generic_case_source(source)
    _validate_case_status_transition(
        source=source,
        current_status=None,
        next_status=status,
    )
    case = TeamCase(
        id=case_id,
        team_id=team_id,
        project_id=project_id,
        title=title,
        description=_optional_text(update.get("description")) or "",
        status=status,
        priority=TeamInboxPriority(
            _optional_text(update.get("priority")) or TeamInboxPriority.NORMAL.value
        ),
        owner_agent_id=owner_agent_id,
        source=source,
        source_ref=_optional_text(update.get("source_ref")),
        tags=_unique_text_list(update.get("tags") or [], "tags"),
        metadata=_mapping(update.get("metadata") or {}, "metadata"),
        task_ids=[],
        created_at=now,
        updated_at=now,
        closed_at=now if status == TeamCaseStatus.CLOSED else None,
    )
    state.cases[case.id] = case
    for task_id in _unique_text_list(update.get("task_ids") or [], "task_ids"):
        _link_task_to_case(state, task_id=task_id, case_id=case.id, now=now)
    return case.id, True


def _case_edit_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    case: TeamCase,
) -> str | None:
    if actor.team_id != case.team_id:
        return f"agent {actor.id} cannot update case {case.id}"
    if case.owner_agent_id == actor.id:
        return None
    if _is_team_manager(state, actor):
        return None
    if case.project_id is not None and _is_project_lead_for(state, actor.id, case.project_id):
        return None
    if case.owner_agent_id is not None and _supervises(state, actor.id, case.owner_agent_id):
        return None
    return f"agent {actor.id} cannot update case {case.id}"


def _task_creation_mandate_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    project_id: str,
    parent_task_id: str | None,
) -> str | None:
    can_create_root = _agent_has_role_permission_for_project(
        state,
        actor,
        RolePermission.CREATE_ROOT_TASK,
        project_id,
    )
    if parent_task_id is None:
        if can_create_root:
            return None
        return (
            f"agent {actor.id} cannot create a top-level task without "
            f"{RolePermission.CREATE_ROOT_TASK.value} permission"
        )
    parent = state.tasks.get(parent_task_id)
    if parent is None:
        return None
    if parent.state not in _NON_TERMINAL_TASK_STATES:
        return f"task parent {parent_task_id} is not active"
    if parent.project_id != project_id:
        return None
    contracted_leaf_error = _contracted_leaf_child_error(
        parent_task_id=parent.id,
        integration_boundary=parent.integration_boundary,
        deliverable_contract=parent.deliverable_contract,
    )
    if contracted_leaf_error is not None:
        return contracted_leaf_error
    if can_create_root:
        return None
    if parent.owner_agent_id == actor.id:
        return None
    return (
        f"agent {actor.id} can only create child tasks under its own active tasks "
        f"without {RolePermission.CREATE_ROOT_TASK.value} permission"
    )


def _contracted_leaf_child_error(
    *,
    parent_task_id: str,
    integration_boundary: bool,
    deliverable_contract: TaskDeliverableContract | None,
) -> str | None:
    if integration_boundary or deliverable_contract is None:
        return None
    return (
        f"task parent {parent_task_id} is a contracted leaf and cannot own child "
        "tasks; submit the work directly on that leaf, create independent work "
        "with originating_task_id, or designate the parent as an integration "
        "boundary before its first deliverable"
    )


def _upward_recipient_error(
    agent: PersistentAgent,
    recipient_id: str | None,
    kind: str,
) -> str | None:
    if agent.reports_to_agent_id is None:
        return (
            f"agent {agent.id} is a top-level manager and must use operator_escalations "
            f"instead of {kind}"
        )
    if recipient_id != agent.reports_to_agent_id:
        return (
            f"agent {agent.id} can only send {kind} to its direct superior "
            f"{agent.reports_to_agent_id}"
        )
    return None


def _plan_task_creation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    creation: dict[str, Any],
    result: dict[str, Any],
    reserved_task_ids: set[str],
) -> _TaskCreationPlan | None:
    owner_id = _required_text(creation.get("owner_agent_id"), "owner_agent_id")
    project_id = _required_text(creation.get("project_id"), "project_id")
    owner_error = _task_creation_owner_assignment_error(
        state,
        actor=actor,
        owner_id=owner_id,
        project_id=project_id,
    )
    if owner_error is not None:
        result["errors"].append(owner_error)
        return None
    project = state.projects[project_id]
    raw_state = _optional_text(creation.get("state"))
    if raw_state is not None:
        try:
            task_state = TeamTaskState(raw_state)
        except ValueError:
            result["errors"].append(f"task creation has invalid state {raw_state}")
            return None
        if task_state == TeamTaskState.WAITING_FOR_INPUT:
            result["errors"].append(
                "waiting_for_input is reserved for operator-owned workflows"
            )
            return None
        if task_state not in _NON_TERMINAL_TASK_STATES:
            result["errors"].append(f"task creation cannot start in terminal state {raw_state}")
            return None
    else:
        task_state = TeamTaskState.PENDING
    explicit_task_id = _optional_text(creation.get("task_id"))
    legacy_task_id = _optional_text(creation.get("id"))
    if (
        explicit_task_id is not None
        and legacy_task_id is not None
        and explicit_task_id != legacy_task_id
    ):
        result["errors"].append("task creation has conflicting task_id and id")
        return None
    task_id = explicit_task_id or legacy_task_id
    if task_id is None:
        task_id = _generated_id(
            prefix="task",
            labels=[project_id, creation.get("title")],
            existing_ids=set(state.tasks) | set(reserved_task_ids),
        )
    if task_id in state.tasks or task_id in reserved_task_ids:
        result["errors"].append(f"task {task_id} already exists")
        return None
    parent_task_id = _optional_text(creation.get("parent_task_id"))
    parent_task = state.tasks.get(parent_task_id or "")
    if (
        parent_task is not None
        and parent_task.mutation_policy is not None
        and not parent_task.mutation_policy.allow_child_tasks
    ):
        result["errors"].append(
            f"workflow-managed task {parent_task.id} does not allow child tasks; "
            "continue or reassign that same task"
        )
        return None
    try:
        git_base_branch = _normalized_task_git_base_branch(creation.get("git_base_branch"))
    except ValueError as exc:
        result["errors"].append(f"task creation has invalid git_base_branch: {exc}")
        return None
    originating_task_id = _optional_text(creation.get("originating_task_id"))
    if originating_task_id is not None:
        origin = state.tasks.get(originating_task_id)
        if origin is None:
            result["errors"].append(
                f"task creation references missing originating task {originating_task_id}"
            )
            return None
        origin_project = state.projects.get(origin.project_id)
        if (
            origin.project_id != project_id
            or origin_project is None
            or origin_project.team_id != project.team_id
        ):
            result["errors"].append(
                f"originating task {originating_task_id} must belong to project "
                f"{project_id} and team {project.team_id}"
            )
            return None
        origin_owner = state.agents.get(origin.owner_agent_id)
        origin_creator = state.agents.get(origin.created_by_agent_id or "")
        origin_visible = (
            agent_has_project_assignment(state, actor.id, project_id)
            or _is_project_lead_for(state, actor.id, project_id)
            or _is_team_manager(state, actor)
            or origin.owner_agent_id == actor.id
            or origin.created_by_agent_id == actor.id
            or (origin_owner is not None and origin_owner.team_id == actor.team_id)
            or (origin_creator is not None and origin_creator.team_id == actor.team_id)
        )
        if not origin_visible:
            result["errors"].append(
                f"originating task {originating_task_id} is not visible to agent {actor.id}"
            )
            return None
    case_id = _optional_text(creation.get("case_id"))
    if case_id is not None:
        case = state.cases.get(case_id)
        if case is None:
            result["errors"].append(f"task creation references missing case {case_id}")
            return None
        if case.team_id != project.team_id:
            result["errors"].append(f"case {case_id} belongs to another team")
            return None
        if case.project_id is not None and case.project_id != project_id:
            result["errors"].append(f"case {case_id} belongs to project {case.project_id}")
            return None
    dependency_task_ids = _string_list(creation.get("dependency_task_ids"), "dependency_task_ids")
    mandate_error = _task_creation_mandate_error(
        state,
        actor=actor,
        project_id=project_id,
        parent_task_id=parent_task_id,
    )
    if mandate_error is not None:
        result["errors"].append(mandate_error)
        return None
    priority = creation.get("priority")
    try:
        task_kind = TeamTaskKind(
            _optional_text(creation.get("task_kind")) or TeamTaskKind.SUPPORT.value
        )
        raw_contract = creation.get("outcome_contract")
        outcome_contract = (
            TaskOutcomeContract.model_validate(raw_contract) if raw_contract is not None else None
        )
        if task_kind == TeamTaskKind.OUTCOME and outcome_contract is None:
            raise ValueError("outcome task requires outcome_contract")
        if task_kind == TeamTaskKind.SUPPORT and outcome_contract is not None:
            raise ValueError("support task cannot define outcome_contract")
        integration_boundary = creation.get("integration_boundary", False)
        if not isinstance(integration_boundary, bool):
            raise ValueError("integration_boundary must be a boolean")
        raw_deliverable_contract = creation.get("deliverable_contract")
        deliverable_contract = (
            TaskDeliverableContract.model_validate(raw_deliverable_contract)
            if raw_deliverable_contract is not None
            else None
        )
        if integration_boundary and deliverable_contract is None:
            raise ValueError("integration boundary task requires deliverable_contract")
        if (
            not integration_boundary
            and deliverable_contract is not None
            and deliverable_contract.required_contribution_task_ids
        ):
            raise ValueError("leaf deliverable_contract cannot require contribution tasks")
        if (
            deliverable_contract is not None
            and task_id in deliverable_contract.required_contribution_task_ids
        ):
            raise ValueError("task cannot require itself as a deliverable contribution")
    except ValueError as exc:
        result["errors"].append(str(exc))
        return None
    return _TaskCreationPlan(
        task_id=task_id,
        owner_id=owner_id,
        project_id=project_id,
        case_id=case_id,
        task_state=task_state,
        parent_task_id=parent_task_id,
        originating_task_id=originating_task_id,
        git_base_branch=git_base_branch,
        dependency_task_ids=dependency_task_ids,
        priority=int(priority) if priority is not None else 0,
        title=_required_text(creation.get("title"), "title"),
        description=_required_text(creation.get("description"), "description"),
        task_kind=task_kind,
        outcome_contract=outcome_contract,
        integration_boundary=integration_boundary,
        deliverable_contract=deliverable_contract,
        authored_by=task_author_identity(creation.get("authored_by")),
    )


def _task_creation_owner_assignment_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    owner_id: str,
    project_id: str,
) -> str | None:
    owner = state.agents.get(owner_id)
    if owner is None:
        return f"task creation references missing owner {owner_id}"
    project = state.projects.get(project_id)
    if project is None:
        return f"task creation references missing project {project_id}"
    if owner.team_id != actor.team_id or project.team_id != actor.team_id:
        return f"agent {actor.id} cannot create tasks outside its team"
    actor_can_target_project = _is_project_lead_for(
        state, actor.id, project_id
    ) or _is_team_manager(state, actor)
    if (
        not agent_has_project_assignment(state, owner.id, project_id)
        and not actor_can_target_project
    ):
        return f"agent {actor.id} cannot create a task for {owner_id} outside project {project_id}"
    permitted = (
        owner.id == actor.id
        or _is_direct_subordinate(actor, owner.id)
        or (
            _is_project_lead_for(state, actor.id, project_id)
            and _agent_in_project_hierarchy(state, owner.id, project_id)
        )
        or (_is_team_manager(state, actor) and _is_project_lead_for(state, owner.id, project_id))
    )
    if not permitted:
        return f"agent {actor.id} is not permitted to create tasks for {owner_id}"
    return None


def _apply_task_creation_plan(
    state: TeamState,
    *,
    plan: _TaskCreationPlan,
    actor: PersistentAgent,
    chat_source_refs: list[Any] | None = None,
    assign_work_lineage: bool = True,
    assignment_role_id: str | None = None,
) -> str:
    now = utc_now()
    state.tasks[plan.task_id] = AgentTask(
        id=plan.task_id,
        owner_agent_id=plan.owner_id,
        assignment_mode="role" if assignment_role_id else "agent",
        assignment_role_id=assignment_role_id,
        project_id=plan.project_id,
        case_id=plan.case_id,
        priority=plan.priority,
        state=plan.task_state,
        task_kind=plan.task_kind,
        outcome_contract=plan.outcome_contract,
        integration_boundary=plan.integration_boundary,
        deliverable_contract=plan.deliverable_contract,
        title=plan.title,
        description=plan.description,
        created_by_agent_id=actor.id,
        parent_task_id=plan.parent_task_id,
        originating_task_id=plan.originating_task_id,
        authored_by=plan.authored_by,
        work_item_attributions=plan.work_item_attributions,
        git_base_branch=plan.git_base_branch,
        dependency_task_ids=plan.dependency_task_ids,
        created_at=now,
        updated_at=now,
    )
    if assign_work_lineage:
        ensure_task_work_lineage(
            state,
            state.tasks[plan.task_id],
            chat_source_refs=chat_source_refs or (),
        )
    if plan.case_id is not None:
        _link_task_to_case(state, task_id=plan.task_id, case_id=plan.case_id, now=now)
    if plan.owner_id is not None and task_state_can_wake_owner(plan.task_state):
        _mark_agent_work_due(state, agent_id=plan.owner_id, now=now)
    return plan.task_id


def _task_owner_reassignment_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    task: AgentTask,
    new_owner_id: str,
) -> str | None:
    if task.state not in _NON_TERMINAL_TASK_STATES:
        return f"task {task.id} is terminal and cannot be reassigned"
    from .role_task_queue import can_manage_unclaimed_role_task

    queue_manager = can_manage_unclaimed_role_task(state, task, actor.id)
    if (
        not queue_manager
        and actor.id != task.owner_agent_id
        and not _supervises(
            state,
            actor.id,
            task.owner_agent_id,
        )
    ):
        return f"agent {actor.id} is not permitted to reassign task {task.id}"
    new_owner = state.agents.get(new_owner_id)
    if new_owner is None:
        return f"task reassignment references missing owner {new_owner_id}"
    project = state.projects.get(task.project_id)
    if project is None:
        return f"task {task.id} references missing project {task.project_id}"
    if new_owner.team_id != actor.team_id or project.team_id != actor.team_id:
        return f"agent {actor.id} cannot reassign task {task.id} outside its team"
    if new_owner.id != actor.id and not _supervises(state, actor.id, new_owner.id):
        return f"agent {actor.id} cannot reassign task {task.id} to {new_owner.id}"
    if not agent_has_project_assignment(state, new_owner.id, task.project_id):
        return (
            f"agent {actor.id} cannot reassign task {task.id} to {new_owner.id} "
            f"outside project {task.project_id}"
        )
    return None


def _subordinate_task_finish_allowed(
    state: TeamState,
    *,
    actor: PersistentAgent,
    task: AgentTask,
    raw_state: object,
    new_owner_id: str | None,
) -> bool:
    try:
        next_state = TeamTaskState(str(raw_state))
    except ValueError:
        return False
    if next_state != TeamTaskState.FINISHED:
        return False
    if new_owner_id is not None and new_owner_id != actor.id:
        return False
    return (
        _task_owner_reassignment_error(
            state,
            actor=actor,
            task=task,
            new_owner_id=actor.id,
        )
        is None
    )


def _task_creation_batch_graph_errors(
    state: TeamState,
    plans: list[_TaskCreationPlan],
    *,
    actor: PersistentAgent,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    while True:
        valid_plans = [plan for plan in plans if plan.task_id not in errors]
        if not valid_plans:
            return errors
        task_projects = {task_id: task.project_id for task_id, task in state.tasks.items()}
        task_owners = {task_id: task.owner_agent_id for task_id, task in state.tasks.items()}
        task_states = {task_id: task.state for task_id, task in state.tasks.items()}
        parent_map = {task_id: task.parent_task_id for task_id, task in state.tasks.items()}
        integration_boundary_map = {
            task_id: task.integration_boundary for task_id, task in state.tasks.items()
        }
        deliverable_contract_map = {
            task_id: task.deliverable_contract for task_id, task in state.tasks.items()
        }
        dependency_map = {
            task_id: list(task.dependency_task_ids) for task_id, task in state.tasks.items()
        }
        for plan in valid_plans:
            task_projects[plan.task_id] = plan.project_id
            task_owners[plan.task_id] = plan.owner_id
            task_states[plan.task_id] = plan.task_state
            parent_map[plan.task_id] = plan.parent_task_id
            integration_boundary_map[plan.task_id] = plan.integration_boundary
            deliverable_contract_map[plan.task_id] = plan.deliverable_contract
            dependency_map[plan.task_id] = list(plan.dependency_task_ids)

        next_errors: dict[str, str] = {}
        for plan in valid_plans:
            dead_end_dependency = next(
                (
                    dependency_id
                    for dependency_id in plan.dependency_task_ids
                    if task_states.get(dependency_id)
                    in {TeamTaskState.CANCELLED, TeamTaskState.REJECTED}
                ),
                None,
            )
            if dead_end_dependency is not None:
                dependency_state = task_states[dead_end_dependency]
                next_errors[plan.task_id] = (
                    f"task cannot add dependency {dead_end_dependency} because it is "
                    f"{dependency_state.value}"
                )
                continue
            graph_error = _task_creation_plan_graph_error(
                plan,
                task_projects=task_projects,
                parent_map=parent_map,
                integration_boundary_map=integration_boundary_map,
                deliverable_contract_map=deliverable_contract_map,
                dependency_map=dependency_map,
            )
            if graph_error is not None:
                next_errors[plan.task_id] = graph_error
                continue
            mandate_error = _task_creation_plan_batch_mandate_error(
                state,
                actor=actor,
                plan=plan,
                task_projects=task_projects,
                task_owners=task_owners,
                task_states=task_states,
            )
            if mandate_error is not None:
                next_errors[plan.task_id] = mandate_error
        if not next_errors:
            return errors
        for task_id, graph_error in next_errors.items():
            errors.setdefault(task_id, graph_error)


def _task_creation_plan_batch_mandate_error(
    state: TeamState,
    *,
    actor: PersistentAgent,
    plan: _TaskCreationPlan,
    task_projects: dict[str, str],
    task_owners: dict[str, str],
    task_states: dict[str, TeamTaskState],
) -> str | None:
    parent_task_id = plan.parent_task_id
    can_create_root = _agent_has_role_permission_for_project(
        state,
        actor,
        RolePermission.CREATE_ROOT_TASK,
        plan.project_id,
    )
    if parent_task_id is None:
        if can_create_root:
            return None
        return (
            f"agent {actor.id} cannot create a top-level task without "
            f"{RolePermission.CREATE_ROOT_TASK.value} permission"
        )
    parent_project_id = task_projects.get(parent_task_id)
    parent_owner_id = task_owners.get(parent_task_id)
    parent_state = task_states.get(parent_task_id)
    if parent_project_id is None or parent_owner_id is None or parent_state is None:
        return None
    if parent_state not in _NON_TERMINAL_TASK_STATES:
        return f"task parent {parent_task_id} is not active"
    if parent_project_id != plan.project_id:
        return None
    if can_create_root:
        return None
    if parent_owner_id == actor.id:
        return None
    return (
        f"agent {actor.id} can only create child tasks under its own active tasks "
        f"without {RolePermission.CREATE_ROOT_TASK.value} permission"
    )


def _task_creation_plan_graph_error(
    plan: _TaskCreationPlan,
    *,
    task_projects: dict[str, str],
    parent_map: dict[str, str | None],
    integration_boundary_map: dict[str, bool],
    deliverable_contract_map: dict[str, TaskDeliverableContract | None],
    dependency_map: dict[str, list[str]],
) -> str | None:
    if plan.parent_task_id == plan.task_id:
        return "task cannot be its own parent"
    if plan.task_id in plan.dependency_task_ids:
        return "task cannot depend on itself"
    if plan.parent_task_id is not None:
        parent_project_id = task_projects.get(plan.parent_task_id)
        if parent_project_id is None:
            return f"task references missing parent {plan.parent_task_id}"
        if parent_project_id != plan.project_id:
            return f"task parent {plan.parent_task_id} belongs to project {parent_project_id}"
        contracted_leaf_error = _contracted_leaf_child_error(
            parent_task_id=plan.parent_task_id,
            integration_boundary=integration_boundary_map.get(
                plan.parent_task_id,
                False,
            ),
            deliverable_contract=deliverable_contract_map.get(plan.parent_task_id),
        )
        if contracted_leaf_error is not None:
            return contracted_leaf_error
    for dependency_id in plan.dependency_task_ids:
        dependency_project_id = task_projects.get(dependency_id)
        if dependency_project_id is None:
            return f"task references missing dependency {dependency_id}"
        if dependency_project_id != plan.project_id:
            return f"task dependency {dependency_id} belongs to project {dependency_project_id}"
    if plan.deliverable_contract is not None:
        for contribution_task_id in plan.deliverable_contract.required_contribution_task_ids:
            contribution_project_id = task_projects.get(contribution_task_id)
            if contribution_project_id is None:
                return (
                    "deliverable contract references missing contribution task "
                    f"{contribution_task_id}"
                )
            if contribution_project_id != plan.project_id:
                return (
                    f"deliverable contribution task {contribution_task_id} belongs "
                    f"to project {contribution_project_id}"
                )
            if (
                contribution_task_id not in plan.dependency_task_ids
                and parent_map.get(contribution_task_id) != plan.task_id
            ):
                return (
                    f"deliverable contribution task {contribution_task_id} must be "
                    f"a direct child or dependency of integration task {plan.task_id}"
                )
    current = plan.parent_task_id
    seen: set[str] = set()
    while current is not None:
        if current == plan.task_id:
            return f"task parent cycle includes {plan.task_id}"
        if current in seen:
            break
        seen.add(current)
        current = parent_map.get(current)
    for dependency_id in plan.dependency_task_ids:
        if _dependency_path_exists(
            dependency_map,
            start_task_id=dependency_id,
            target_task_id=plan.task_id,
        ):
            return f"task dependency cycle includes {plan.task_id}"
    wait_map = {task_id: list(dependencies) for task_id, dependencies in dependency_map.items()}
    for task_id, parent_task_id in parent_map.items():
        if parent_task_id is not None:
            wait_map.setdefault(parent_task_id, []).append(task_id)
    if _has_dependency_cycle(wait_map):
        return f"task hierarchy/dependency cycle includes {plan.task_id}"
    return None


def _dependency_path_exists(
    dependency_map: dict[str, list[str]],
    *,
    start_task_id: str,
    target_task_id: str,
) -> bool:
    stack = [start_task_id]
    seen: set[str] = set()
    while stack:
        task_id = stack.pop()
        if task_id == target_task_id:
            return True
        if task_id in seen:
            continue
        seen.add(task_id)
        stack.extend(dependency_map.get(task_id, []))
    return False


def _has_dependency_cycle(dependency_map: dict[str, list[str]]) -> bool:
    """Iterative DFS mirroring TeamState._validate_no_dependency_cycle."""

    visited: set[str] = set()
    visiting: set[str] = set()
    for start in dependency_map:
        if start in visited:
            continue
        stack: list[str] = [start]
        while stack:
            node = stack[-1]
            visiting.add(node)
            advanced = False
            for dependency_id in dependency_map.get(node, []):
                if dependency_id not in dependency_map:
                    continue
                if dependency_id in visited:
                    continue
                if dependency_id in visiting:
                    return True
                stack.append(dependency_id)
                advanced = True
                break
            if not advanced:
                visiting.discard(node)
                visited.add(node)
                stack.pop()
    return False


def _task_graph_edit_error(
    state: TeamState,
    *,
    task_id: str | None,
    project_id: str,
    parent_task_id: str | None,
    dependency_task_ids: list[str],
) -> str | None:
    """Pre-check the Step 1 task-graph invariants for a proposed create/edit.

    Mirrors TeamState._validate_task_graph but simulates the proposed edge(s) so
    apply_agent_action can reject the item instead of letting the post-mutation
    re-validation abort the whole action. ``task_id`` is None for a new task.
    """

    existing_task = state.tasks.get(task_id) if task_id is not None else None
    if task_id is not None:
        if (
            existing_task is not None
            and existing_task.state not in _NON_TERMINAL_TASK_STATES
            and (
                parent_task_id != existing_task.parent_task_id
                or dependency_task_ids != existing_task.dependency_task_ids
            )
        ):
            return f"terminal task {task_id} cannot change its parent or dependency task IDs"
        if parent_task_id == task_id:
            return "task cannot be its own parent"
        if task_id in dependency_task_ids:
            return "task cannot depend on itself"
    if parent_task_id is not None:
        parent = state.tasks.get(parent_task_id)
        if parent is None:
            return f"task references missing parent {parent_task_id}"
        if parent.project_id != project_id:
            return f"task parent {parent_task_id} belongs to project {parent.project_id}"
        parent_edge_changed = (
            existing_task is None or existing_task.parent_task_id != parent_task_id
        )
        if parent_edge_changed:
            if (
                parent.mutation_policy is not None
                and not parent.mutation_policy.allow_child_tasks
            ):
                return (
                    f"workflow-managed task {parent.id} does not allow child tasks; "
                    "continue or reassign that same task"
                )
            contracted_leaf_error = _contracted_leaf_child_error(
                parent_task_id=parent.id,
                integration_boundary=parent.integration_boundary,
                deliverable_contract=parent.deliverable_contract,
            )
            if contracted_leaf_error is not None:
                return contracted_leaf_error
        if parent.state not in _NON_TERMINAL_TASK_STATES and (
            existing_task is None or existing_task.parent_task_id != parent_task_id
        ):
            return f"task parent {parent_task_id} is not active"
    for dependency_id in dependency_task_ids:
        dependency = state.tasks.get(dependency_id)
        if dependency is None:
            return f"task references missing dependency {dependency_id}"
        if dependency.project_id != project_id:
            return f"task dependency {dependency_id} belongs to project {dependency.project_id}"
        dependency_edge_is_new = (
            existing_task is None
            or dependency_id not in existing_task.dependency_task_ids
        )
        if dependency_edge_is_new and dependency.state in {
            TeamTaskState.CANCELLED,
            TeamTaskState.REJECTED,
        }:
            return (
                f"task cannot add dependency {dependency_id} because it is "
                f"{dependency.state.value}"
            )
    proposed_parent_map = {
        existing_id: existing_task.parent_task_id
        for existing_id, existing_task in state.tasks.items()
    }
    proposed_dependency_map = {
        existing_id: list(existing_task.dependency_task_ids)
        for existing_id, existing_task in state.tasks.items()
    }
    if task_id is not None:
        proposed_parent_map[task_id] = parent_task_id
        proposed_dependency_map[task_id] = list(dependency_task_ids)
    for integration_task in state.tasks.values():
        contract = integration_task.deliverable_contract
        if contract is None:
            continue
        effective_dependency_ids = set(proposed_dependency_map.get(integration_task.id, ()))
        for contribution_task_id in contract.required_contribution_task_ids:
            if (
                contribution_task_id not in effective_dependency_ids
                and proposed_parent_map.get(contribution_task_id) != integration_task.id
            ):
                return (
                    f"deliverable contribution task {contribution_task_id} must "
                    f"remain a direct child or dependency of integration task "
                    f"{integration_task.id}"
                )
    # Walk up the parent chain from the proposed parent; reaching task_id is a cycle.
    if task_id is not None and parent_task_id is not None:
        seen: set[str] = set()
        current: str | None = parent_task_id
        while current is not None:
            if current == task_id:
                return f"task parent cycle includes {task_id}"
            if current in seen:
                break
            seen.add(current)
            current_task = state.tasks.get(current)
            current = current_task.parent_task_id if current_task is not None else None
    # Simulate the dependency graph with the proposed edges and look for a cycle.
    if task_id is not None:
        dependency_map = {
            existing_id: list(task.dependency_task_ids) for existing_id, task in state.tasks.items()
        }
        dependency_map[task_id] = list(dependency_task_ids)
        if _has_dependency_cycle(dependency_map):
            return f"task dependency cycle includes {task_id}"
        wait_map = {
            existing_id: list(dependencies) for existing_id, dependencies in dependency_map.items()
        }
        for existing_id, existing_task in state.tasks.items():
            effective_parent_id = (
                parent_task_id if existing_id == task_id else existing_task.parent_task_id
            )
            if effective_parent_id is not None:
                wait_map.setdefault(effective_parent_id, []).append(existing_id)
        if task_id not in state.tasks and parent_task_id is not None:
            wait_map.setdefault(parent_task_id, []).append(task_id)
            wait_map.setdefault(task_id, list(dependency_task_ids))
        if _has_dependency_cycle(wait_map):
            return f"task hierarchy/dependency cycle includes {task_id}"
    return None


def _task_deliverable_contract_scope_error(
    state: TeamState,
    *,
    task_id: str,
    project_id: str,
    dependency_task_ids: list[str],
    integration_boundary: bool,
    deliverable_contract: TaskDeliverableContract | None,
) -> str | None:
    if integration_boundary and deliverable_contract is None:
        return "integration boundary task requires deliverable_contract"
    if (
        not integration_boundary
        and deliverable_contract is not None
        and deliverable_contract.required_contribution_task_ids
    ):
        return "leaf deliverable_contract cannot require contribution tasks"
    if not integration_boundary and deliverable_contract is not None:
        direct_child_ids = {
            candidate.id
            for candidate in state.tasks.values()
            if candidate.parent_task_id == task_id
        }
        if direct_child_ids:
            return (
                f"task {task_id} has direct children and cannot become a contracted "
                "leaf; designate it as an integration boundary with an exact "
                "deliverable contribution contract"
            )
    if deliverable_contract is None:
        return None
    if task_id in deliverable_contract.required_contribution_task_ids:
        return "task cannot require itself as a deliverable contribution"
    direct_child_ids = {
        candidate.id for candidate in state.tasks.values() if candidate.parent_task_id == task_id
    }
    dependency_ids = set(dependency_task_ids)
    for contribution_task_id in deliverable_contract.required_contribution_task_ids:
        contribution_task = state.tasks.get(contribution_task_id)
        if contribution_task is None:
            return (
                f"deliverable contract references missing contribution task {contribution_task_id}"
            )
        if contribution_task.project_id != project_id:
            return (
                f"deliverable contribution task {contribution_task_id} belongs "
                f"to project {contribution_task.project_id}"
            )
        if (
            contribution_task_id not in direct_child_ids
            and contribution_task_id not in dependency_ids
        ):
            return (
                f"deliverable contribution task {contribution_task_id} must be "
                f"a direct child or dependency of integration task {task_id}"
            )
    return None


def _apply_goal_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    project_id = _required_text(update.get("project_id"), "project_id")
    goal_id = _required_text(update.get("goal_id"), "goal_id")
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(f"goal update references missing project {project_id}")
        return None
    permitted = _is_project_lead_for(state, actor.id, project_id) or (
        _is_team_manager(state, actor) and project.team_id == actor.team_id
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to update goals on project {project_id}"
        )
        return None
    raw_state = _required_text(update.get("state"), "state")
    try:
        goal_state = TeamRecordState(raw_state)
    except ValueError:
        result["errors"].append(f"goal update has invalid state {raw_state}")
        return None
    goal = next((item for item in project.goals if item.id == goal_id), None)
    if goal is None:
        result["errors"].append(f"goal {goal_id} does not exist on project {project_id}")
        return None
    goal.state = goal_state
    project.updated_at = utc_now()
    projects_to_recalc.add(project_id)
    return goal_id


__all__ = [
    "_TaskCreationPlan",
    "TaskGitDefaultBranchResolver",
    "TaskGitBaseValidationError",
    "_validate_case_scope",
    "_case_can_link_task",
    "_link_task_to_case",
    "_unlink_task_from_case",
    "_sync_case_task_link",
    "_snapshot_case_task_state",
    "_restore_case_task_state",
    "_apply_case_update",
    "_case_edit_error",
    "_task_creation_mandate_error",
    "_upward_recipient_error",
    "_plan_task_creation",
    "_task_creation_owner_assignment_error",
    "_apply_task_creation_plan",
    "_resolve_task_creation_plan_git_bases",
    "_resolve_new_task_git_base_branch",
    "_set_task_git_base_branch",
    "_task_git_base_branch_locked",
    "_task_owner_reassignment_error",
    "_subordinate_task_finish_allowed",
    "_task_creation_batch_graph_errors",
    "_task_creation_plan_batch_mandate_error",
    "_task_creation_plan_graph_error",
    "_dependency_path_exists",
    "_has_dependency_cycle",
    "_task_graph_edit_error",
    "_task_deliverable_contract_scope_error",
    "_apply_goal_update",
]
