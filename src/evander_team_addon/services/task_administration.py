from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..git_naming import validate_branch_name
from ..repository import (
    TeamStateConflictError,
)
from ..models import (
    AgentTask,
    TeamBlocker,
    TeamBlockerEventType,
    TeamBlockerSourceKind,
    TeamState,
    TaskCancellation,
    TaskDeliverableContract,
    TaskOutcomeContract,
    TeamTaskKind,
    TeamTaskState,
    _unique_text_list,
)
from .domain.task_authorship import (
    inherited_task_author,
    task_author_identity,
    inherited_work_item_attributions,
)
from .domain.case_task import (
    _correction_case_git_base_branch,
    _link_task_to_case,
    _normalized_task_git_base_commit,
    _resolve_new_task_git_base_branch,
    _set_task_git_base_branch,
    _sync_case_task_link,
    _task_coordination_handoff_error,
    _task_graph_edit_error,
    _task_integration_boundary_transition_error,
    _task_deliverable_contract_scope_error,
    inherited_task_case_id,
)
from .domain.outcome_catalog import (
    snapshot_task_outcome_contract,
)
from .domain.blockers import (
    active_blockers_for_source,
    append_blocker_event,
    open_or_get_blocker,
    reconcile_task_blockers_after_cancellation,
    resolve_blocker,
    task_cancellation_blocking_handoff_ids,
)
from .domain.common import (
    _NON_TERMINAL_TASK_STATES,
    _generated_id,
    _optional_text,
    _require_absent,
    _require_present,
    _required_text,
    _set_task_state,
    task_state_can_wake_owner,
)
from .domain.context_rotation import (
    _maybe_mark_idle_context_rotation_due,
)
from .domain.work_lineage import ensure_task_work_lineage
from .domain.git_work_contexts import finalize_active_git_work_contexts_for_task
from .domain.project_status import _recalculate_project_status
from .domain.task_assessment import (
    TaskAssessmentBoundaryError,
    assess_task,
    require_task_can_finish,
)
from .historical_lifecycle_migration import (
    HistoricalLifecycleMigrationPlan,
    apply_historical_lifecycle_migration,
    plan_historical_lifecycle_migration,
)
from .domain.task_supervision import queue_parent_resolution_transitions
from .domain.submitted_integration_recovery import (
    apply_submitted_integration_boundary_recovery,
    plan_submitted_integration_boundary_recovery,
)
from .domain.task_replacement import (
    apply_cancelled_task_dependency_repair,
    apply_task_dependency_rebinding,
    invalidate_rebound_integration_git_contexts,
    plan_task_dependency_rebinding,
    plan_cancelled_task_dependency_repair,
    queue_rebound_integration_preparation_recovery,
)
from .domain.task_preparation_invalidation import (
    invalidate_task_preparation_after_edit,
)
from .domain.wake import (
    _append_agent_wake_requested_event,
    _append_newly_ready_task_owner_wake_requested_events,
    _dependency_status_snapshot,
)
from .domain.work_due import _mark_agent_work_due
from .domain.common import DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS
from .domain.preparation_failures import (
    task_git_work_context_generation,
    task_preparation_generation,
)
from .domain.dependency_composition_recovery import (
    release_dependency_composition_recovery_after_task_cancellation,
)
from .commands.outbox import _team_event_outbox_records


def utc_now() -> datetime:
    # Keep the compatibility module as the patchable clock seam.
    from . import admin_service

    return admin_service.utc_now()


def _legacy_operator_repair_blocker(
    state: TeamState,
    *,
    task: AgentTask,
    actor: str | None,
    reason: str | None,
    now: datetime,
) -> TeamBlocker:
    """Create an audit-only blocker when legacy blocked state lacked one."""

    project = _require_present(state.projects, task.project_id, "project")
    blocker = TeamBlocker(
        id=f"blocker-{uuid4().hex}",
        team_id=project.team_id,
        project_id=project.id,
        task_id=task.id,
        source_kind=TeamBlockerSourceKind.TASK,
        source_id=task.id,
        reason_code="operator_repair",
        reporter_actor=actor,
        owner_agent_id=task.owner_agent_id,
        affected_task_ids=[task.id],
        summary=f"Legacy blocked-state repair: {task.title}",
        details=reason or "Operator repaired a legacy blocked-state contradiction.",
        created_at=now,
        updated_at=now,
    )
    state.blockers[blocker.id] = blocker
    append_blocker_event(
        state,
        blocker=blocker,
        event_type=TeamBlockerEventType.REPORTED,
        to_agent_id=task.owner_agent_id,
        note=blocker.details,
        metadata={
            "operator_actor": actor,
            "operator_repair": True,
            "legacy_missing_blocker": True,
        },
        now=now,
    )
    return blocker


class TeamTaskAdministration:
    """Task creation, editing, cancellation, and recovery operations."""

    def create_or_reprioritize_task(
        self,
        *,
        task_id: str | None,
        owner_agent_id: str,
        project_id: str,
        case_id: str | None = None,
        priority: int,
        title: str,
        description: str,
        created_by_agent_id: str | None = None,
        created_by_actor: str | None = None,
        state: TeamTaskState = TeamTaskState.PENDING,
        parent_task_id: str | None = None,
        originating_task_id: str | None = None,
        authored_by: object = None,
        work_item_attributions: object = None,
        set_authored_by: bool = False,
        git_base_branch: str | None = None,
        git_base_commit_sha: str | None = None,
        set_git_base_branch: bool = False,
        set_parent_task_id: bool = False,
        set_case_id: bool = False,
        dependency_task_ids: list[str] | None = None,
        task_kind: TeamTaskKind | str | None = None,
        outcome_contract: Mapping[str, Any] | None = None,
        set_outcome_contract: bool = False,
        integration_boundary: bool | None = None,
        deliverable_contract: Mapping[str, Any] | None = None,
        set_deliverable_contract: bool = False,
        expected_revision: int | None = None,
    ) -> TeamState:
        state_model, _effective_task_id, _created = self.create_or_reprioritize_task_with_result(
            task_id=task_id,
            owner_agent_id=owner_agent_id,
            project_id=project_id,
            case_id=case_id,
            priority=priority,
            title=title,
            description=description,
            created_by_agent_id=created_by_agent_id,
            created_by_actor=created_by_actor,
            state=state,
            parent_task_id=parent_task_id,
            originating_task_id=originating_task_id,
            authored_by=authored_by,
            work_item_attributions=work_item_attributions,
            set_authored_by=set_authored_by,
            git_base_branch=git_base_branch,
            git_base_commit_sha=git_base_commit_sha,
            set_git_base_branch=set_git_base_branch,
            set_parent_task_id=set_parent_task_id,
            set_case_id=set_case_id,
            dependency_task_ids=dependency_task_ids,
            task_kind=task_kind,
            outcome_contract=outcome_contract,
            set_outcome_contract=set_outcome_contract,
            integration_boundary=integration_boundary,
            deliverable_contract=deliverable_contract,
            set_deliverable_contract=set_deliverable_contract,
            expected_revision=expected_revision,
        )
        return state_model

    def create_or_reprioritize_task_with_result(
        self,
        *,
        task_id: str | None,
        owner_agent_id: str,
        project_id: str,
        case_id: str | None = None,
        priority: int,
        title: str,
        description: str,
        created_by_agent_id: str | None = None,
        created_by_actor: str | None = None,
        state: TeamTaskState = TeamTaskState.PENDING,
        parent_task_id: str | None = None,
        originating_task_id: str | None = None,
        authored_by: object = None,
        work_item_attributions: object = None,
        set_authored_by: bool = False,
        git_base_branch: str | None = None,
        git_base_commit_sha: str | None = None,
        set_git_base_branch: bool = False,
        set_parent_task_id: bool = False,
        set_case_id: bool = False,
        dependency_task_ids: list[str] | None = None,
        task_kind: TeamTaskKind | str | None = None,
        outcome_contract: Mapping[str, Any] | None = None,
        set_outcome_contract: bool = False,
        integration_boundary: bool | None = None,
        deliverable_contract: Mapping[str, Any] | None = None,
        set_deliverable_contract: bool = False,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, str, bool]:
        normalized_task_id = _optional_text(task_id)
        normalized_case_id = _optional_text(case_id)
        normalized_title = _required_text(title, "title")
        normalized_description = _required_text(description, "description")
        normalized_owner_agent_id = _required_text(owner_agent_id, "owner_agent_id")
        normalized_project_id = _required_text(project_id, "project_id")
        normalized_parent_task_id = _optional_text(parent_task_id)
        normalized_originating_task_id = _optional_text(originating_task_id)
        normalized_authored_by = task_author_identity(authored_by)
        normalized_git_base_branch = _optional_text(git_base_branch)
        if normalized_git_base_branch is not None:
            normalized_git_base_branch = validate_branch_name(normalized_git_base_branch)
        normalized_git_base_commit_sha = _optional_text(git_base_commit_sha)
        if normalized_git_base_commit_sha is not None:
            normalized_git_base_commit_sha = normalized_git_base_commit_sha.casefold()
            if len(normalized_git_base_commit_sha) not in {40, 64} or any(
                char not in "0123456789abcdef" for char in normalized_git_base_commit_sha
            ):
                raise ValueError("git_base_commit_sha must be a full Git object ID")
        normalized_dependency_task_ids = (
            _unique_text_list(dependency_task_ids, "dependency_task_ids")
            if dependency_task_ids is not None
            else None
        )
        normalized_task_kind = TeamTaskKind(task_kind) if task_kind is not None else None
        normalized_outcome_contract = (
            TaskOutcomeContract.model_validate(outcome_contract)
            if outcome_contract is not None
            else None
        )
        normalized_deliverable_contract = (
            TaskDeliverableContract.model_validate(deliverable_contract)
            if deliverable_contract is not None
            else None
        )
        if normalized_task_kind == TeamTaskKind.COORDINATION:
            if normalized_outcome_contract is not None:
                raise ValueError("coordination task cannot define outcome_contract")
            if integration_boundary:
                raise ValueError("coordination task cannot be an integration boundary")
            if normalized_deliverable_contract is not None:
                raise ValueError("coordination task cannot define deliverable_contract")
            if normalized_git_base_commit_sha is not None:
                raise ValueError("coordination task cannot pin a Git base commit")
        effective_requested_kind = normalized_task_kind or TeamTaskKind.SUPPORT
        if state in {TeamTaskState.ACCEPTED, TeamTaskState.CANCELLED}:
            raise ValueError(
                f"Admin task state {state.value} requires its dedicated "
                "acceptance or cancellation lifecycle"
            )
        if state == TeamTaskState.FINISHED and effective_requested_kind == TeamTaskKind.OUTCOME:
            raise ValueError(
                "outcome tasks must resolve through an accepted deliverable, not direct finish"
            )
        if state == TeamTaskState.FINISHED and integration_boundary:
            raise ValueError(
                "integration boundary tasks must resolve through an accepted "
                "deliverable, not direct finish"
            )
        effective_task_id: str | None = None
        created = False
        blocker_result: dict[str, Any] = {"events": []}

        def _mutate(state_model: TeamState) -> TeamState:
            nonlocal created, effective_task_id
            now = utc_now()
            effective_outcome_contract = (
                snapshot_task_outcome_contract(
                    state_model,
                    project_id=normalized_project_id,
                    contract=normalized_outcome_contract,
                )
                if normalized_outcome_contract is not None
                else None
            )
            if normalized_task_id is not None and normalized_task_id in state_model.tasks:
                task = state_model.tasks[normalized_task_id]
                if task.project_id != normalized_project_id:
                    raise ValueError(
                        f"task {task.id} belongs to project {task.project_id}, "
                        f"not {normalized_project_id}"
                    )
                if (
                    normalized_originating_task_id is not None
                    and normalized_originating_task_id != task.originating_task_id
                ):
                    raise ValueError(
                        f"task {task.id} originating_task_id is immutable after creation"
                    )
                if (
                    normalized_git_base_commit_sha is not None
                    and normalized_git_base_commit_sha != task.git_base_commit_sha
                ):
                    raise ValueError(
                        f"task {task.id} git_base_commit_sha is immutable after creation"
                    )
                proposed_task_kind = normalized_task_kind or task.task_kind
                if proposed_task_kind != task.task_kind and TeamTaskKind.COORDINATION in {
                    proposed_task_kind,
                    task.task_kind,
                }:
                    raise ValueError(
                        f"task {task.id} cannot change to or from coordination after creation; "
                        "create a fresh task with the intended task_kind"
                    )
                proposed_parent_task_id = (
                    normalized_parent_task_id
                    if set_parent_task_id or parent_task_id is not None
                    else task.parent_task_id
                )
                proposed_dependency_task_ids = (
                    normalized_dependency_task_ids
                    if normalized_dependency_task_ids is not None
                    else list(task.dependency_task_ids)
                )
                deliverable_policy_request = (
                    integration_boundary is not None
                    or set_deliverable_contract
                    or deliverable_contract is not None
                )
                proposed_integration_boundary = (
                    integration_boundary
                    if integration_boundary is not None
                    else task.integration_boundary
                )
                if integration_boundary is False and not (
                    set_deliverable_contract or deliverable_contract is not None
                ):
                    proposed_deliverable_contract = None
                else:
                    proposed_deliverable_contract = (
                        normalized_deliverable_contract
                        if set_deliverable_contract or deliverable_contract is not None
                        else task.deliverable_contract
                    )
                if proposed_task_kind == TeamTaskKind.COORDINATION:
                    if proposed_integration_boundary:
                        raise ValueError("coordination task cannot be an integration boundary")
                    if proposed_deliverable_contract is not None:
                        raise ValueError("coordination task cannot define deliverable_contract")
                    if normalized_outcome_contract is not None:
                        raise ValueError("coordination task cannot define outcome_contract")
                    if normalized_git_base_commit_sha is not None:
                        raise ValueError("coordination task cannot pin a Git base commit")
                coordination_handoff_error = _task_coordination_handoff_error(
                    state_model,
                    integration_boundary=proposed_integration_boundary,
                    parent_task_id=proposed_parent_task_id,
                    originating_task_id=task.originating_task_id,
                    deliverable_contract=proposed_deliverable_contract,
                )
                if coordination_handoff_error is not None:
                    raise ValueError(coordination_handoff_error)
                deliverable_policy_edit = deliverable_policy_request and (
                    proposed_integration_boundary != task.integration_boundary
                    or proposed_deliverable_contract != task.deliverable_contract
                )
                transition_error = _task_integration_boundary_transition_error(
                    state_model,
                    task=task,
                    proposed_integration_boundary=proposed_integration_boundary,
                )
                if transition_error is not None:
                    raise ValueError(transition_error)
                if task.mutation_policy is not None:
                    locked_fields = set(task.mutation_policy.locked_fields)
                    changed_locked_fields: list[str] = []
                    if (
                        "parent_task_id" in locked_fields
                        and proposed_parent_task_id != task.parent_task_id
                    ):
                        changed_locked_fields.append("parent_task_id")
                    if (
                        "dependency_task_ids" in locked_fields
                        and proposed_dependency_task_ids != task.dependency_task_ids
                    ):
                        changed_locked_fields.append("dependency_task_ids")
                    if (
                        "task_kind" in locked_fields
                        and normalized_task_kind is not None
                        and normalized_task_kind != task.task_kind
                    ):
                        changed_locked_fields.append("task_kind")
                    if deliverable_policy_edit:
                        if (
                            "integration_boundary" in locked_fields
                            and proposed_integration_boundary != task.integration_boundary
                        ):
                            changed_locked_fields.append("integration_boundary")
                        if (
                            "deliverable_contract" in locked_fields
                            and proposed_deliverable_contract != task.deliverable_contract
                        ):
                            changed_locked_fields.append("deliverable_contract")
                    if (
                        "git_base_branch" in locked_fields
                        and (set_git_base_branch or git_base_branch is not None)
                        and normalized_git_base_branch != task.git_base_branch
                    ):
                        changed_locked_fields.append("git_base_branch")
                    if changed_locked_fields:
                        raise ValueError(
                            f"task {task.id} mutation policy "
                            f"{task.mutation_policy.policy_id} locks: "
                            + ", ".join(changed_locked_fields)
                        )
                graph_error = _task_graph_edit_error(
                    state_model,
                    task_id=task.id,
                    project_id=task.project_id,
                    parent_task_id=proposed_parent_task_id,
                    dependency_task_ids=proposed_dependency_task_ids,
                )
                if graph_error is not None:
                    raise ValueError(graph_error)
                if deliverable_policy_edit:
                    if task.state not in _NON_TERMINAL_TASK_STATES:
                        raise ValueError(
                            f"terminal task {task.id} cannot change its "
                            "integration deliverable contract"
                        )
                    if task.deliverable_ids:
                        raise ValueError(
                            f"task {task.id} cannot change its integration "
                            "deliverable contract after a deliverable was submitted"
                        )
                    contract_error = _task_deliverable_contract_scope_error(
                        state_model,
                        task_id=task.id,
                        project_id=task.project_id,
                        dependency_task_ids=proposed_dependency_task_ids,
                        integration_boundary=proposed_integration_boundary,
                        deliverable_contract=proposed_deliverable_contract,
                    )
                    if contract_error is not None:
                        raise ValueError(contract_error)
                effective_task_id = task.id
                created = False
                task.priority = priority
                task.title = normalized_title
                task.description = normalized_description
                case_link_changed = False
                if set_parent_task_id or parent_task_id is not None:
                    task.parent_task_id = normalized_parent_task_id
                if set_case_id or case_id is not None:
                    old_case_id = task.case_id
                    task.case_id = normalized_case_id
                    case_link_changed = old_case_id != task.case_id
                    _sync_case_task_link(
                        state_model,
                        task=task,
                        old_case_id=old_case_id,
                        new_case_id=normalized_case_id,
                        now=now,
                    )
                if normalized_dependency_task_ids is not None:
                    task.dependency_task_ids = normalized_dependency_task_ids
                if normalized_task_kind is not None:
                    task.task_kind = normalized_task_kind
                if set_outcome_contract or outcome_contract is not None:
                    task.outcome_contract = effective_outcome_contract
                if deliverable_policy_edit:
                    task.integration_boundary = proposed_integration_boundary
                    task.deliverable_contract = proposed_deliverable_contract
                if set_authored_by or authored_by is not None:
                    task.authored_by = normalized_authored_by
                if set_git_base_branch or git_base_branch is not None:
                    _set_task_git_base_branch(
                        state_model,
                        task=task,
                        git_base_branch=normalized_git_base_branch,
                        git_default_branch_resolver=(self._resolve_managed_git_default_branch),
                    )
                elif (
                    case_link_changed
                    and _correction_case_git_base_branch(
                        state_model,
                        case_id=task.case_id,
                    )
                    is not None
                ):
                    _set_task_git_base_branch(
                        state_model,
                        task=task,
                        git_base_branch=None,
                        git_default_branch_resolver=(self._resolve_managed_git_default_branch),
                    )
                task.updated_at = now
                if task_state_can_wake_owner(task.state):
                    _mark_agent_work_due(
                        state_model,
                        agent_id=task.owner_agent_id,
                        now=now,
                    )
                else:
                    _maybe_mark_idle_context_rotation_due(
                        state_model,
                        agent_id=task.owner_agent_id,
                        now=now,
                        result=blocker_result,
                    )
                return state_model
            effective_task_id = normalized_task_id or _generated_id(
                prefix="task",
                labels=[normalized_project_id, normalized_title],
                existing_ids=state_model.tasks,
            )
            created = True
            _require_absent(state_model.tasks, effective_task_id, "task")
            if normalized_originating_task_id is not None:
                origin = _require_present(
                    state_model.tasks,
                    normalized_originating_task_id,
                    "originating task",
                )
                origin_project = _require_present(
                    state_model.projects,
                    origin.project_id,
                    "originating task project",
                )
                project = _require_present(
                    state_model.projects,
                    normalized_project_id,
                    "project",
                )
                if (
                    origin.project_id != normalized_project_id
                    or origin_project.team_id != project.team_id
                ):
                    raise ValueError(
                        f"originating task {origin.id} must belong to project "
                        f"{normalized_project_id} and team {project.team_id}"
                    )
            graph_error = _task_graph_edit_error(
                state_model,
                task_id=effective_task_id,
                project_id=normalized_project_id,
                parent_task_id=normalized_parent_task_id,
                dependency_task_ids=normalized_dependency_task_ids or [],
            )
            if graph_error is not None:
                raise ValueError(graph_error)
            effective_integration_boundary = bool(integration_boundary)
            coordination_handoff_error = _task_coordination_handoff_error(
                state_model,
                integration_boundary=effective_integration_boundary,
                parent_task_id=normalized_parent_task_id,
                originating_task_id=normalized_originating_task_id,
                deliverable_contract=normalized_deliverable_contract,
            )
            if coordination_handoff_error is not None:
                raise ValueError(coordination_handoff_error)
            contract_error = _task_deliverable_contract_scope_error(
                state_model,
                task_id=effective_task_id,
                project_id=normalized_project_id,
                dependency_task_ids=normalized_dependency_task_ids or [],
                integration_boundary=effective_integration_boundary,
                deliverable_contract=normalized_deliverable_contract,
            )
            if contract_error is not None:
                raise ValueError(contract_error)
            inherited_case_id = inherited_task_case_id(
                state_model,
                project_id=normalized_project_id,
                case_id=normalized_case_id,
                originating_task_id=normalized_originating_task_id,
                parent_task_id=normalized_parent_task_id,
            )
            resolved_git_base_branch = _resolve_new_task_git_base_branch(
                state_model,
                project_id=normalized_project_id,
                explicit_git_base_branch=normalized_git_base_branch,
                parent_task_id=normalized_parent_task_id,
                case_id=inherited_case_id,
                git_default_branch_resolver=(self._resolve_managed_git_default_branch),
            )
            effective_git_base_commit_sha = normalized_git_base_commit_sha
            project = _require_present(
                state_model.projects,
                normalized_project_id,
                "project",
            )
            if project.git is not None and (
                effective_integration_boundary or effective_git_base_commit_sha is not None
            ):
                if resolved_git_base_branch is None:
                    raise ValueError(
                        f"integration boundary task {effective_task_id} has no concrete "
                        "Git base branch to pin"
                    )
                try:
                    resolved_branch_commit = _normalized_task_git_base_commit(
                        self._resolve_managed_git_branch_commit(
                            managed_repo_id=project.git.managed_repo_id,
                            directory_path=project.git.directory_path,
                            branch=resolved_git_base_branch,
                            required_ancestor_shas=(
                                [effective_git_base_commit_sha]
                                if effective_git_base_commit_sha is not None
                                else None
                            ),
                        )
                    )
                except Exception as exc:
                    raise ValueError(
                        f"could not validate supplied Git base commit "
                        f"{effective_git_base_commit_sha} against managed branch "
                        f"{resolved_git_base_branch} for task {effective_task_id}: {exc}"
                        if effective_git_base_commit_sha is not None
                        else f"could not resolve immutable Git base commit for integration "
                        f"boundary task {effective_task_id}: {exc}"
                    ) from exc
                if resolved_branch_commit is None:
                    raise ValueError(
                        f"managed branch {resolved_git_base_branch} has no commit against "
                        f"which to validate supplied Git base commit "
                        f"{effective_git_base_commit_sha} for task {effective_task_id}"
                        if effective_git_base_commit_sha is not None
                        else f"managed branch {resolved_git_base_branch} has no commit to pin "
                        f"for integration boundary task {effective_task_id}"
                    )
                effective_git_base_commit_sha = (
                    effective_git_base_commit_sha or resolved_branch_commit
                )
            state_model.tasks[effective_task_id] = AgentTask(
                id=effective_task_id,
                owner_agent_id=normalized_owner_agent_id,
                project_id=normalized_project_id,
                case_id=inherited_case_id,
                priority=priority,
                state=state,
                state_entered_at=now,
                title=normalized_title,
                description=normalized_description,
                created_by_agent_id=created_by_agent_id,
                created_by_actor=created_by_actor,
                parent_task_id=normalized_parent_task_id,
                originating_task_id=normalized_originating_task_id,
                work_item_attributions=work_item_attributions
                if work_item_attributions is not None
                else inherited_work_item_attributions(
                    state_model,
                    project_id=normalized_project_id,
                    originating_task_id=normalized_originating_task_id,
                    parent_task_id=normalized_parent_task_id,
                ),
                authored_by=inherited_task_author(
                    state_model,
                    project_id=normalized_project_id,
                    authored_by=normalized_authored_by,
                    originating_task_id=normalized_originating_task_id,
                    parent_task_id=normalized_parent_task_id,
                ),
                git_base_branch=resolved_git_base_branch,
                git_base_commit_sha=effective_git_base_commit_sha,
                dependency_task_ids=normalized_dependency_task_ids or [],
                task_kind=normalized_task_kind or TeamTaskKind.SUPPORT,
                outcome_contract=effective_outcome_contract,
                integration_boundary=effective_integration_boundary,
                deliverable_contract=normalized_deliverable_contract,
                created_at=now,
                updated_at=now,
            )
            ensure_task_work_lineage(state_model, state_model.tasks[effective_task_id])
            if state == TeamTaskState.BLOCKED:
                project = _require_present(
                    state_model.projects,
                    normalized_project_id,
                    "project",
                )
                open_or_get_blocker(
                    state_model,
                    source_kind=TeamBlockerSourceKind.TASK,
                    source_id=effective_task_id,
                    summary=f"Task blocked: {normalized_title}",
                    details=normalized_description,
                    reporter_agent_id=normalized_owner_agent_id,
                    team_id=project.team_id,
                    project_id=project.id,
                    task_id=effective_task_id,
                    result=blocker_result,
                    now=now,
                )
            if inherited_case_id is not None:
                _link_task_to_case(
                    state_model,
                    task_id=effective_task_id,
                    case_id=inherited_case_id,
                    now=now,
                )
            if task_state_can_wake_owner(state):
                _mark_agent_work_due(state_model, agent_id=normalized_owner_agent_id, now=now)
            else:
                _maybe_mark_idle_context_rotation_due(
                    state_model,
                    agent_id=normalized_owner_agent_id,
                    now=now,
                    result=blocker_result,
                )
            return state_model

        state_model = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(blocker_result.get("events") or []),
            expected_revision=expected_revision,
        )
        if effective_task_id is None:
            raise RuntimeError("task mutation completed without resolving an effective task id")
        return state_model, effective_task_id, created

    def update_task(
        self,
        *,
        task_id: str,
        priority: int | None = None,
        state: TeamTaskState | None = None,
        title: str | None = None,
        description: str | None = None,
        case_id: str | None = None,
        set_case_id: bool = False,
        parent_task_id: str | None = None,
        set_parent_task_id: bool = False,
        authored_by: object = None,
        work_item_attributions: object = None,
        set_authored_by: bool = False,
        git_base_branch: str | None = None,
        set_git_base_branch: bool = False,
        dependency_task_ids: list[str] | None = None,
        integration_boundary: bool | None = None,
        deliverable_contract: Mapping[str, Any] | None = None,
        set_deliverable_contract: bool = False,
        operator_repair_reason: str | None = None,
        operator_repair_actor: str | None = None,
        operator_repair_reference_ids: Sequence[str] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_case_id = _optional_text(case_id)
        normalized_parent_task_id = _optional_text(parent_task_id)
        normalized_authored_by = task_author_identity(authored_by)
        normalized_git_base_branch = _optional_text(git_base_branch)
        if normalized_git_base_branch is not None:
            normalized_git_base_branch = validate_branch_name(normalized_git_base_branch)
        normalized_dependency_task_ids = (
            _unique_text_list(dependency_task_ids, "dependency_task_ids")
            if dependency_task_ids is not None
            else None
        )
        normalized_deliverable_contract = (
            TaskDeliverableContract.model_validate(deliverable_contract)
            if deliverable_contract is not None
            else None
        )
        normalized_repair_reason = _optional_text(operator_repair_reason)
        normalized_repair_actor = _optional_text(operator_repair_actor)
        normalized_repair_reference_ids = _unique_text_list(
            operator_repair_reference_ids or [],
            "operator_repair_reference_ids",
        )
        blocker_result: dict[str, Any] = {"events": []}

        def _mutate(state_model: TeamState) -> TeamState:
            task = _require_present(state_model.tasks, normalized_task_id, "task")
            preparation_generation_before = task_git_work_context_generation(
                state_model,
                task.id,
            )
            now = utc_now()
            if task.owner_agent_id is None and state in {
                TeamTaskState.IN_PROGRESS,
                TeamTaskState.WAITING_FOR_REVIEW,
                TeamTaskState.FINISHED,
                TeamTaskState.ACCEPTED,
            }:
                raise ValueError("role task must be assigned before execution or completion")
            previous_state = task.state
            crosses_blocked_boundary = (
                state is not None
                and state != previous_state
                and (state == TeamTaskState.BLOCKED or previous_state == TeamTaskState.BLOCKED)
            )
            if crosses_blocked_boundary and (
                normalized_repair_reason is None or normalized_repair_actor is None
            ):
                raise ValueError(
                    "direct task state edits into or out of blocked are disabled; "
                    "use the reason-required operator repair endpoint"
                )
            if (
                normalized_repair_reason is not None
                or normalized_repair_actor is not None
                or normalized_repair_reference_ids
            ) and not crosses_blocked_boundary:
                raise ValueError(
                    "operator task-state repair requires a transition into or out of blocked"
                )
            if set_parent_task_id or normalized_dependency_task_ids is not None:
                graph_error = _task_graph_edit_error(
                    state_model,
                    task_id=task.id,
                    project_id=task.project_id,
                    parent_task_id=(
                        normalized_parent_task_id if set_parent_task_id else task.parent_task_id
                    ),
                    dependency_task_ids=(
                        normalized_dependency_task_ids
                        if normalized_dependency_task_ids is not None
                        else list(task.dependency_task_ids)
                    ),
                )
                if graph_error is not None:
                    raise ValueError(graph_error)
            deliverable_policy_edit = (
                integration_boundary is not None
                or set_deliverable_contract
                or deliverable_contract is not None
            )
            if deliverable_policy_edit:
                proposed_integration_boundary = (
                    integration_boundary
                    if integration_boundary is not None
                    else task.integration_boundary
                )
                if integration_boundary is False and not (
                    set_deliverable_contract or deliverable_contract is not None
                ):
                    proposed_deliverable_contract = None
                else:
                    proposed_deliverable_contract = (
                        normalized_deliverable_contract
                        if set_deliverable_contract or deliverable_contract is not None
                        else task.deliverable_contract
                    )
                deliverable_policy_edit = (
                    proposed_integration_boundary != task.integration_boundary
                    or proposed_deliverable_contract != task.deliverable_contract
                )
                transition_error = _task_integration_boundary_transition_error(
                    state_model,
                    task=task,
                    proposed_integration_boundary=proposed_integration_boundary,
                )
                if transition_error is not None:
                    raise ValueError(transition_error)
            if task.mutation_policy is not None:
                locked_fields = set(task.mutation_policy.locked_fields)
                changed_locked_fields: list[str] = []
                if (
                    set_parent_task_id
                    and "parent_task_id" in locked_fields
                    and normalized_parent_task_id != task.parent_task_id
                ):
                    changed_locked_fields.append("parent_task_id")
                if (
                    normalized_dependency_task_ids is not None
                    and "dependency_task_ids" in locked_fields
                    and normalized_dependency_task_ids != task.dependency_task_ids
                ):
                    changed_locked_fields.append("dependency_task_ids")
                if (
                    set_git_base_branch
                    and "git_base_branch" in locked_fields
                    and normalized_git_base_branch != task.git_base_branch
                ):
                    changed_locked_fields.append("git_base_branch")
                if deliverable_policy_edit:
                    if (
                        "integration_boundary" in locked_fields
                        and proposed_integration_boundary != task.integration_boundary
                    ):
                        changed_locked_fields.append("integration_boundary")
                    if (
                        "deliverable_contract" in locked_fields
                        and proposed_deliverable_contract != task.deliverable_contract
                    ):
                        changed_locked_fields.append("deliverable_contract")
                if changed_locked_fields:
                    raise ValueError(
                        f"task {task.id} mutation policy {task.mutation_policy.policy_id} "
                        "locks: " + ", ".join(changed_locked_fields)
                    )
            if deliverable_policy_edit:
                if task.state not in _NON_TERMINAL_TASK_STATES:
                    raise ValueError(
                        f"terminal task {task.id} cannot change its integration "
                        "deliverable contract"
                    )
                if task.deliverable_ids:
                    raise ValueError(
                        f"task {task.id} cannot change its integration deliverable "
                        "contract after a deliverable was submitted"
                    )
                proposed_dependency_task_ids = (
                    normalized_dependency_task_ids
                    if normalized_dependency_task_ids is not None
                    else list(task.dependency_task_ids)
                )
                contract_error = _task_deliverable_contract_scope_error(
                    state_model,
                    task_id=task.id,
                    project_id=task.project_id,
                    dependency_task_ids=proposed_dependency_task_ids,
                    integration_boundary=proposed_integration_boundary,
                    deliverable_contract=proposed_deliverable_contract,
                )
                if contract_error is not None:
                    raise ValueError(contract_error)
            if state == TeamTaskState.FINISHED:
                try:
                    require_task_can_finish(
                        state_model,
                        task.id,
                        dependency_task_ids=normalized_dependency_task_ids,
                    )
                except TaskAssessmentBoundaryError as exc:
                    raise ValueError(exc.message) from None
            elif state in {
                TeamTaskState.ACCEPTED,
                TeamTaskState.CANCELLED,
            }:
                raise ValueError(
                    f"Admin task state {state.value} requires its dedicated "
                    "acceptance or cancellation lifecycle"
                )
            if priority is not None:
                task.priority = priority
            if state is not None:
                _set_task_state(task, state, now=now)
                if task.state not in _NON_TERMINAL_TASK_STATES:
                    finalize_active_git_work_contexts_for_task(
                        state_model,
                        task_id=task.id,
                        now=now,
                    )
            if title is not None:
                task.title = title
            if description is not None:
                task.description = description
            case_link_changed = False
            if set_case_id or case_id is not None:
                old_case_id = task.case_id
                task.case_id = normalized_case_id
                case_link_changed = old_case_id != task.case_id
                _sync_case_task_link(
                    state_model,
                    task=task,
                    old_case_id=old_case_id,
                    new_case_id=normalized_case_id,
                    now=now,
                )
            if set_parent_task_id:
                task.parent_task_id = normalized_parent_task_id
            if normalized_dependency_task_ids is not None:
                task.dependency_task_ids = normalized_dependency_task_ids
            if deliverable_policy_edit:
                task.integration_boundary = proposed_integration_boundary
                task.deliverable_contract = proposed_deliverable_contract
            if set_authored_by or authored_by is not None:
                task.authored_by = normalized_authored_by
            if set_git_base_branch:
                _set_task_git_base_branch(
                    state_model,
                    task=task,
                    git_base_branch=normalized_git_base_branch,
                    git_default_branch_resolver=(self._resolve_managed_git_default_branch),
                )
            elif (
                case_link_changed
                and _correction_case_git_base_branch(
                    state_model,
                    case_id=task.case_id,
                )
                is not None
            ):
                _set_task_git_base_branch(
                    state_model,
                    task=task,
                    git_base_branch=None,
                    git_default_branch_resolver=(self._resolve_managed_git_default_branch),
                )
            changed_preparation_fields = [
                field_name
                for field_name, requested in (
                    ("state", state is not None),
                    ("title", title is not None),
                    ("description", description is not None),
                    ("case_id", set_case_id or case_id is not None),
                    ("parent_task_id", set_parent_task_id),
                    ("git_base_branch", set_git_base_branch),
                    ("dependency_task_ids", normalized_dependency_task_ids is not None),
                    ("integration_boundary", integration_boundary is not None),
                    (
                        "deliverable_contract",
                        set_deliverable_contract or deliverable_contract is not None,
                    ),
                )
                if requested
            ]
            invalidate_task_preparation_after_edit(
                state_model,
                task_id=task.id,
                preparation_generation_before=preparation_generation_before,
                changed_fields=changed_preparation_fields,
                sender_agent_id=None,
                result=blocker_result,
                now=now,
            )
            task.updated_at = now
            if previous_state != TeamTaskState.BLOCKED and task.state == TeamTaskState.BLOCKED:
                project = _require_present(state_model.projects, task.project_id, "project")
                blocker, created = open_or_get_blocker(
                    state_model,
                    source_kind=TeamBlockerSourceKind.TASK,
                    source_id=task.id,
                    summary=f"Task blocked: {task.title}",
                    details=normalized_repair_reason or task.description,
                    reason_code=(
                        "operator_repair" if normalized_repair_reason is not None else "unspecified"
                    ),
                    reporter_agent_id=task.owner_agent_id,
                    reporter_actor=normalized_repair_actor,
                    team_id=project.team_id,
                    project_id=project.id,
                    task_id=task.id,
                    affected_task_ids=[task.id],
                    result=blocker_result,
                    now=now,
                )
                if not created:
                    append_blocker_event(
                        state_model,
                        blocker=blocker,
                        event_type=TeamBlockerEventType.PROGRESS,
                        note=normalized_repair_reason or task.description,
                        reference_ids=normalized_repair_reference_ids,
                        metadata={
                            "operator_actor": normalized_repair_actor,
                            "operator_repair": True,
                        },
                        now=now,
                    )
            elif previous_state == TeamTaskState.BLOCKED and task.state != TeamTaskState.BLOCKED:
                active_blockers = active_blockers_for_source(
                    state_model,
                    source_kind=TeamBlockerSourceKind.TASK,
                    source_id=task.id,
                )
                if not active_blockers:
                    active_blockers = [
                        _legacy_operator_repair_blocker(
                            state_model,
                            task=task,
                            actor=normalized_repair_actor,
                            reason=normalized_repair_reason,
                            now=now,
                        )
                    ]
                for blocker in active_blockers:
                    resolve_blocker(
                        state_model,
                        blocker=blocker,
                        actor_agent_id=None,
                        dispatch_id=None,
                        note=(normalized_repair_reason or f"Task moved to {task.state.value}."),
                        reference_ids=[
                            task.id,
                            *normalized_repair_reference_ids,
                        ],
                        metadata={
                            "operator_actor": normalized_repair_actor,
                            "operator_repair": normalized_repair_reason is not None,
                        },
                        now=now,
                    )
            if task_state_can_wake_owner(task.state):
                _mark_agent_work_due(
                    state_model,
                    agent_id=task.owner_agent_id,
                    now=task.updated_at,
                )
            else:
                _maybe_mark_idle_context_rotation_due(
                    state_model,
                    agent_id=task.owner_agent_id,
                    now=task.updated_at,
                    result=blocker_result,
                )
            if previous_state != task.state and task.state in {
                TeamTaskState.ACCEPTED,
                TeamTaskState.CANCELLED,
                TeamTaskState.FINISHED,
            }:
                queue_parent_resolution_transitions(
                    state_model,
                    child_task_id=task.id,
                    sender_agent_id=task.owner_agent_id,
                    now=now,
                )
            from .domain.role_task_queue import append_role_queue_wake_events

            append_role_queue_wake_events(
                state_model,
                result=blocker_result,
                now=now,
                project_id=task.project_id,
            )
            return state_model

        return self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(blocker_result.get("events") or []),
            expected_revision=expected_revision,
        )

    def repair_task_state(
        self,
        *,
        task_id: str,
        state: TeamTaskState,
        reason: str,
        actor: str,
        reference_ids: Sequence[str] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        return self.update_task(
            task_id=task_id,
            state=state,
            operator_repair_reason=_required_text(reason, "reason"),
            operator_repair_actor=_required_text(actor, "actor"),
            operator_repair_reference_ids=reference_ids,
            expected_revision=expected_revision,
        )

    def plan_historical_lifecycle_migration(
        self,
        *,
        project_id: str,
    ) -> tuple[TeamState, HistoricalLifecycleMigrationPlan]:
        state = self._repository.read()
        return (
            state,
            plan_historical_lifecycle_migration(
                state,
                project_id=_required_text(project_id, "project_id"),
            ),
        )

    def migrate_historical_task_lifecycle(
        self,
        *,
        project_id: str,
        actor: str,
        reason: str,
        reference_ids: Sequence[str] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        """Atomically repair supported historical lifecycle contradictions.

        This is deliberately narrower than ordinary task editing.  It only
        handles audited terminal-state defects, preserves all historical task
        and deliverable rows, and writes one resolved audit blocker per task.
        """

        normalized_project_id = _required_text(project_id, "project_id")
        normalized_actor = _required_text(actor, "actor")
        normalized_reason = _required_text(reason, "reason")
        normalized_reference_ids = _unique_text_list(
            reference_ids or [],
            "reference_ids",
        )
        planned_state = self._repository.read()
        if expected_revision is not None and planned_state.revision != expected_revision:
            raise TeamStateConflictError(
                expected_revision=expected_revision,
                current_revision=planned_state.revision,
            )
        plan = plan_historical_lifecycle_migration(
            planned_state,
            project_id=normalized_project_id,
        )
        if not plan.task_repairs:
            return (
                planned_state,
                {
                    "applied": False,
                    "migration_id": None,
                    "project_id": normalized_project_id,
                    "task_repairs": [],
                    "audit_blocker_ids": [],
                },
            )

        result: dict[str, Any] = {}

        def _mutate(state_model: TeamState) -> TeamState:
            nonlocal result
            result = apply_historical_lifecycle_migration(
                state_model,
                plan=plan,
                actor=normalized_actor,
                reason=normalized_reason,
                reference_ids=normalized_reference_ids,
                now=utc_now(),
            )
            return state_model

        state = self._repository.update(
            _mutate,
            expected_revision=(
                expected_revision if expected_revision is not None else planned_state.revision
            ),
        )
        return state, result

    def cancel_release_package(
        self,
        *,
        release_package_id: str,
        operator_actor_id: str,
        reason: str = "",
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        from .domain.release_cancellation import cancel_release

        release_id = _required_text(release_package_id, "release_package_id")
        actor_id = _required_text(operator_actor_id, "operator_actor_id")
        result: dict[str, Any] = {}

        def _mutate(state: TeamState) -> TeamState:
            release = _require_present(state.release_packages, release_id, "release package")
            result.update(
                cancel_release(
                    state,
                    release=release,
                    actor_id=actor_id,
                    reason=_optional_text(reason) or "",
                )
            )
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result.update(
            {
                "success": True,
                "revision": state.revision,
                "release_package": state.release_packages[release_id].model_dump(mode="json"),
            }
        )
        return state, result

    def cancel_task(
        self,
        *,
        task_id: str,
        reason_code: str,
        reason: str,
        actor_id: str,
        successor_task_id: str | None = None,
        rebind_dependents: bool = False,
        expected_dependent_task_ids: Sequence[str] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_reason_code = _required_text(reason_code, "reason_code")
        normalized_reason = _required_text(reason, "reason")
        normalized_actor_id = _required_text(actor_id, "actor_id")
        normalized_successor_id = _optional_text(successor_task_id)
        if not isinstance(rebind_dependents, bool):
            raise ValueError("rebind_dependents must be a boolean")
        normalized_rebind_dependents = rebind_dependents
        normalized_expected_dependent_ids = (
            None
            if expected_dependent_task_ids is None
            else tuple(
                sorted(
                    _unique_text_list(
                        expected_dependent_task_ids,
                        "expected_dependent_task_ids",
                    )
                )
            )
        )
        if normalized_rebind_dependents and normalized_expected_dependent_ids is None:
            raise ValueError(
                "expected_dependent_task_ids is required when rebind_dependents is true"
            )
        if not normalized_rebind_dependents and normalized_expected_dependent_ids:
            raise ValueError("expected_dependent_task_ids requires rebind_dependents=true")
        cancellation_result: dict[str, Any] = {"events": []}

        def _mutate(state_model: TeamState) -> TeamState:
            dependency_statuses_before = _dependency_status_snapshot(state_model)
            task = _require_present(state_model.tasks, normalized_task_id, "task")
            if task.state not in {
                *_NON_TERMINAL_TASK_STATES,
                TeamTaskState.REJECTED,
            }:
                raise ValueError(f"task {task.id} is already {task.state.value}")
            assessment = assess_task(state_model, task.id)
            if assessment.unresolved_descendant_ids:
                raise ValueError(
                    f"task {task.id} cannot be cancelled while descendants remain "
                    f"unresolved: {', '.join(assessment.unresolved_descendant_ids)}"
                )
            (
                blocking_acceptance_ids,
                blocking_review_request_ids,
            ) = task_cancellation_blocking_handoff_ids(
                state_model,
                acceptance_ids=assessment.active_acceptance_ids,
                review_request_ids=assessment.active_review_request_ids,
            )
            blocking_handoff_ids = sorted({*blocking_acceptance_ids, *blocking_review_request_ids})
            if blocking_handoff_ids:
                raise ValueError(
                    f"task {task.id} cannot be cancelled while acceptance or review "
                    f"handoffs remain active: {', '.join(blocking_handoff_ids)}"
                )
            if normalized_successor_id is not None:
                successor = _require_present(
                    state_model.tasks,
                    normalized_successor_id,
                    "successor task",
                )
                if successor.id == task.id or successor.project_id != task.project_id:
                    raise ValueError(
                        "a cancellation successor must be a different task in "
                        f"project {task.project_id}"
                    )
            if normalized_rebind_dependents and normalized_successor_id is None:
                raise ValueError("rebind_dependents requires a same-project successor task")
            replacement_plan = None
            if normalized_rebind_dependents and normalized_successor_id is not None:
                replacement_plan = plan_task_dependency_rebinding(
                    state_model,
                    predecessor_task_id=task.id,
                    successor_task_id=normalized_successor_id,
                )
                assert normalized_expected_dependent_ids is not None
                if replacement_plan.dependent_task_ids != normalized_expected_dependent_ids:
                    raise ValueError(
                        "task replacement affected set changed; expected "
                        f"{list(normalized_expected_dependent_ids)!r}, current "
                        f"{list(replacement_plan.dependent_task_ids)!r}"
                    )
            preparation_generations_before = (
                {
                    dependent_id: task_preparation_generation(state_model, dependent_id)
                    for dependent_id in replacement_plan.dependent_task_ids
                }
                if replacement_plan is not None
                else {}
            )
            now = utc_now()
            rebound_dependent_task_ids = (
                apply_task_dependency_rebinding(
                    state_model,
                    plan=replacement_plan,
                    now=now,
                )
                if replacement_plan is not None
                else []
            )
            invalidated_git_context_ids_by_task = (
                invalidate_rebound_integration_git_contexts(
                    state_model,
                    plan=replacement_plan,
                    now=now,
                )
                if replacement_plan is not None
                else {}
            )
            affected_integration_boundary_task_ids = (
                list(replacement_plan.affected_integration_boundary_task_ids)
                if replacement_plan is not None
                else []
            )
            invalidated_git_work_context_ids = sorted(
                context_id
                for context_ids in invalidated_git_context_ids_by_task.values()
                for context_id in context_ids
            )
            task.cancellation = TaskCancellation(
                reason_code=normalized_reason_code,
                reason=normalized_reason,
                actor_kind="operator",
                actor_id=normalized_actor_id,
                successor_task_id=normalized_successor_id,
                rebind_dependents=normalized_rebind_dependents,
                rebound_dependent_task_ids=rebound_dependent_task_ids,
                rebound_integration_boundary_task_ids=(affected_integration_boundary_task_ids),
                invalidated_git_work_context_ids=invalidated_git_work_context_ids,
                cancelled_at=now,
            )
            _set_task_state(task, TeamTaskState.CANCELLED, now=now)
            task.updated_at = now
            release_dependency_composition_recovery_after_task_cancellation(
                state_model,
                task_id=task.id,
                actor_agent_id=None,
                dispatch_id=None,
                operator_actor_id=normalized_actor_id,
                now=now,
            )
            finalize_active_git_work_contexts_for_task(
                state_model,
                task_id=task.id,
                now=now,
            )
            reconcile_task_blockers_after_cancellation(
                state_model,
                task_id=task.id,
                actor_agent_id=None,
                dispatch_id=None,
                now=now,
            )
            queue_parent_resolution_transitions(
                state_model,
                child_task_id=task.id,
                sender_agent_id=task.owner_agent_id,
                now=now,
            )
            if replacement_plan is not None:
                queue_rebound_integration_preparation_recovery(
                    state_model,
                    plan=replacement_plan,
                    invalidated_git_context_ids_by_task=(invalidated_git_context_ids_by_task),
                    preparation_generations_before=preparation_generations_before,
                    sender_agent_id=None,
                    result=cancellation_result,
                    now=now,
                )
            _maybe_mark_idle_context_rotation_due(
                state_model,
                agent_id=task.owner_agent_id,
                now=now,
                result=cancellation_result,
            )
            _recalculate_project_status(state_model, task.project_id)
            _append_newly_ready_task_owner_wake_requested_events(
                state_model,
                dependency_statuses_before=dependency_statuses_before,
                result=cancellation_result,
                now=now,
            )
            return state_model

        return self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(
                cancellation_result.get("events") or []
            ),
            expected_revision=expected_revision,
        )

    def repair_cancelled_task_dependencies(
        self,
        *,
        predecessor_task_id: str,
        successor_task_id: str,
        expected_dependent_task_ids: Sequence[str],
        reason: str,
        actor_id: str,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_predecessor_id = _required_text(
            predecessor_task_id,
            "predecessor_task_id",
        )
        normalized_successor_id = _required_text(successor_task_id, "successor_task_id")
        normalized_expected_dependent_ids = tuple(
            sorted(
                _unique_text_list(
                    expected_dependent_task_ids,
                    "expected_dependent_task_ids",
                )
            )
        )
        normalized_reason = _required_text(reason, "reason")
        normalized_actor_id = _required_text(actor_id, "actor_id")
        repair_result: dict[str, Any] = {"events": []}

        def _mutate(state_model: TeamState) -> TeamState:
            now = utc_now()
            dependency_statuses_before = _dependency_status_snapshot(state_model)
            plan = plan_cancelled_task_dependency_repair(
                state_model,
                predecessor_task_id=normalized_predecessor_id,
                successor_task_id=normalized_successor_id,
                expected_dependent_task_ids=normalized_expected_dependent_ids,
            )
            _changed_domain_ids, state_changed = apply_cancelled_task_dependency_repair(
                state_model,
                plan=plan,
                actor_kind="operator",
                actor_id=normalized_actor_id,
                reason=normalized_reason,
                expected_dependent_task_ids=normalized_expected_dependent_ids,
                result=repair_result,
                now=now,
            )
            predecessor = state_model.tasks[normalized_predecessor_id]
            if state_changed:
                _recalculate_project_status(state_model, predecessor.project_id)
                _append_newly_ready_task_owner_wake_requested_events(
                    state_model,
                    dependency_statuses_before=dependency_statuses_before,
                    result=repair_result,
                    now=now,
                )
            return state_model

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(repair_result.get("events") or []),
            expected_revision=expected_revision,
        )
        return state, repair_result

    def recover_submitted_integration_boundary(
        self,
        *,
        predecessor_task_id: str,
        successor_task_id: str,
        integration_task_id: str,
        replacement_task_id: str,
        expected_dependency_task_ids: Sequence[str],
        expected_required_contribution_task_ids: Sequence[str],
        reason: str,
        actor_id: str,
        git_base_commit_sha: str | None = None,
        git_base_branch: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_predecessor_id = _required_text(
            predecessor_task_id,
            "predecessor_task_id",
        )
        normalized_successor_id = _required_text(successor_task_id, "successor_task_id")
        normalized_integration_id = _required_text(
            integration_task_id,
            "integration_task_id",
        )
        normalized_replacement_id = _required_text(
            replacement_task_id,
            "replacement_task_id",
        )
        normalized_expected_dependency_ids = tuple(
            sorted(
                _unique_text_list(
                    expected_dependency_task_ids,
                    "expected_dependency_task_ids",
                )
            )
        )
        normalized_expected_contribution_ids = tuple(
            sorted(
                _unique_text_list(
                    expected_required_contribution_task_ids,
                    "expected_required_contribution_task_ids",
                )
            )
        )
        normalized_reason = _required_text(reason, "reason")
        normalized_actor_id = _required_text(actor_id, "actor_id")
        normalized_git_base_commit_sha = _optional_text(git_base_commit_sha)
        normalized_git_base_branch = _optional_text(git_base_branch)
        recovery_result: dict[str, Any] = {"events": []}

        def _mutate(state_model: TeamState) -> TeamState:
            now = utc_now()
            plan = plan_submitted_integration_boundary_recovery(
                state_model,
                predecessor_task_id=normalized_predecessor_id,
                successor_task_id=normalized_successor_id,
                integration_task_id=normalized_integration_id,
                replacement_task_id=normalized_replacement_id,
                expected_dependency_task_ids=normalized_expected_dependency_ids,
                expected_required_contribution_task_ids=(normalized_expected_contribution_ids),
                reason=normalized_reason,
                git_base_commit_sha=normalized_git_base_commit_sha,
                git_base_branch=normalized_git_base_branch,
                actor_kind="operator",
            )
            before_blocker_event_ids = set(state_model.blocker_events)
            _changed_domain_ids, state_changed = apply_submitted_integration_boundary_recovery(
                state_model,
                plan=plan,
                actor_kind="operator",
                actor_id=normalized_actor_id,
                result=recovery_result,
                now=now,
            )
            blocker_event_ids = sorted(set(state_model.blocker_events) - before_blocker_event_ids)
            if blocker_event_ids:
                recovery_result["blocker_event_ids"] = blocker_event_ids
            if state_changed:
                replacement = state_model.tasks[normalized_replacement_id]
                if assess_task(state_model, replacement.id).readiness.value == "actionable":
                    _append_agent_wake_requested_event(
                        state_model,
                        agent_id=replacement.owner_agent_id,
                        result=recovery_result,
                        reason="task_ready",
                        now=now,
                        delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                        payload_extra={
                            "task_id": replacement.id,
                            "recovery_kind": ("submitted_integration_boundary_recovery"),
                        },
                    )
                _recalculate_project_status(state_model, replacement.project_id)
            return state_model

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(recovery_result.get("events") or []),
            expected_revision=expected_revision,
        )
        return state, recovery_result
