from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from evander.models import DirectoryPath

from ..git_naming import validate_branch_name
from .domain import common as _team_common
from ..repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from ..sqlite_store import TeamIdempotencyConflictError
from ..models import (
    AgentAvatar,
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentProjectAssignment,
    AgentProjectAssignmentKind,
    AgentRuntime,
    AgentStatus,
    AgentTask,
    AgentVoiceProfile,
    Deliverable,
    DeliverableOutcome,
    EphemeralAgentPolicy,
    ExternalSourceEvidenceAcceptanceCriterion,
    ExternalSourceEvidenceBody,
    ExternalSourceEvidenceDiscussionEntry,
    ExternalSourceEvidenceFreshnessStatus,
    ExternalSourceEvidenceLink,
    ExternalSourceEvidenceSnapshot,
    GovernanceAgendaItem,
    GovernanceAgendaItemState,
    GovernanceAgendaItemType,
    GovernanceGoal,
    GovernanceGroup,
    DependencyCompositionManifest,
    DependencyGitSource,
    DependencyCompositionRecoveryState,
    GitWorkContext,
    OutcomeKindDefinition,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ProjectReleasePolicy,
    ProjectGoal,
    ProjectStatus,
    ReviewCriterionResult,
    ReviewProfile,
    ReviewProfileCriterion,
    ReviewRequest,
    RolePermission,
    Team,
    TeamBlocker,
    TeamBlockerEventType,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamCase,
    TeamCaseStatus,
    TeamCostBudgetRenewalMode,
    TeamCostBudgetWindow,
    TeamCostBudgetWindowKind,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamGitPreparationAttemptState,
    TeamIdempotencyRecord,
    TeamMemoryKind,
    TeamMemoryRecord,
    TeamMemoryScope,
    TeamMemoryStatus,
    TeamRecordState,
    TeamRole,
    TeamState,
    TaskCancellation,
    TaskCompletion,
    TaskDeliverableContract,
    TaskOutcomeContract,
    TeamTaskKind,
    TeamTaskState,
    _ensure_unique,
    _unique_text_list,
    agent_project_ids,
    external_source_evidence_content_digest,
    outcome_kind_snapshots,
    release_package_payload_with_derived,
)
from ..templates import get_template
from .domain.agent_access import (
    _agent_for_session,
    _agent_governs_team,
    _agent_team_removal_blockers,
    _default_role_id,
    _normalize_role_permissions,
    _project_assignment_id,
    _validate_role_assignment,
    _validate_role_filter_assignment,
)
from .domain.case_task import (
    _correction_case_git_base_branch,
    _case_can_link_task,
    _link_task_to_case,
    _resolve_new_task_git_base_branch,
    _set_task_git_base_branch,
    _sync_case_task_link,
    _task_graph_edit_error,
    _task_deliverable_contract_scope_error,
    _unlink_task_from_case,
    _validate_generic_case_source,
    _validate_protected_case_update,
    _validate_case_scope,
    _validate_case_status_transition,
)
from .domain.case_events import append_case_closed_event
from .domain.outcome_catalog import (
    active_outcome_kind_references,
    snapshot_deliverable_outcomes,
    snapshot_review_profile_outcomes,
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
    _append_unique,
    _cost_warning_ratio,
    _generated_id,
    _mapping,
    _materialize_role_inputs,
    _materialized_template_record_id,
    _optional_bool,
    _optional_cost_limit,
    _optional_text,
    _ordered_template_roles,
    _project_git_config,
    _project_goals_with_generated_ids,
    _remove_value,
    _require_absent,
    _require_present,
    _required_mapping,
    _required_text,
    _set_task_state,
    _unique_id_from_base,
    task_state_can_wake_owner,
)
from .domain.context_rotation import (
    _context_clearing_idle_grace_seconds_from_value,
    _maybe_mark_idle_context_rotation_due,
    _resolved_context_clearing_policy,
    _set_context_clearing_idle_grace_seconds,
    _set_context_clearing_policy,
)
from .domain.work_lineage import ensure_task_work_lineage, ensure_task_work_lineages
from .domain.deliverables import (
    _superseded_deliverable_for_rework,
    _validate_deliverable_outcomes_against_task_contract,
)
from .domain.acceptance_verification import apply_formal_review_acceptance_policy
from .domain.git_work_contexts import finalize_active_git_work_contexts_for_task
from .domain.review_repository_snapshots import deliverable_review_repository_snapshot
from .domain.governance import (
    _governance_agenda_effective_team_id,
    _governance_goals_with_generated_ids,
    _record_governance_membership_change_agenda,
)
from .domain.project_status import _recalculate_project_status
from .domain.human_reviews import (
    HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY,
    HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE,
    close_post_release_feedback_case,
    reassign_human_review,
    record_human_finding_resolution_proposal,
    record_human_review_decision,
    record_human_review_preparation_result,
    report_post_release_feedback,
    route_human_finding_correction,
    resume_human_review,
)
from .domain.release_packages import (
    ReleasePackageValidationError,
    _build_release_package,
)
from .domain.release_gates import retry_release_gate
from .domain.review import (
    _apply_finding_resolutions,
    _cascade_review_decision,
    _is_review_decision_state,
    _record_open_review_findings,
    _reopen_accepted_deliverable_for_pending_review,
    _review_decision_policy_error,
    _review_profile_for_request,
    retire_terminal_review,
    withdraw_pending_review,
)
from .domain.task_assessment import (
    TaskAssessmentBoundaryError,
    assess_task,
    require_deliverable_submission,
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
    create_rejected_candidate_correction_preparation_recovery_inbox,
    task_git_work_context_generation,
    task_preparation_generation,
)
from .domain.dependency_composition_recovery import (
    release_dependency_composition_recovery_after_task_cancellation,
    start_dependency_composition_recovery,
)
from .commands.blocker_commands import _prepare_blocker_source_for_resolution
from .commands.outbox import _team_event_outbox_records
from .candidate_correction_reconciliation_service import (
    recover_candidate_correction_successor,
)


def utc_now() -> datetime:
    return _team_common.utc_now()


def _cost_budget_renewal_mode(value: object) -> TeamCostBudgetRenewalMode:
    text = _optional_text(value)
    if text is None:
        return TeamCostBudgetRenewalMode.MANUAL
    try:
        return TeamCostBudgetRenewalMode(text)
    except ValueError:
        allowed = ", ".join(item.value for item in TeamCostBudgetRenewalMode)
        raise ValueError(f"cost_budget_renewal_mode must be one of: {allowed}") from None


def _active_team_cost_budget_window(team: Team) -> TeamCostBudgetWindow | None:
    return next((window for window in team.cost_budget_windows if window.ended_at is None), None)


def _close_active_team_cost_budget_window(team: Team, *, ended_at: datetime) -> None:
    active_window = _active_team_cost_budget_window(team)
    if active_window is not None:
        active_window.ended_at = ended_at


def _open_team_cost_budget_window(
    team: Team,
    *,
    now: datetime,
    kind: TeamCostBudgetWindowKind,
    actor: str,
    reason: str,
) -> TeamCostBudgetWindow:
    if team.max_cost_usd is None:
        raise ValueError("a cost budget window requires max_cost_usd")
    _close_active_team_cost_budget_window(team, ended_at=now)
    window = TeamCostBudgetWindow(
        id=f"team-cost-budget-{uuid4().hex}",
        started_at=now,
        kind=kind,
        actor=actor,
        reason=reason,
        max_cost_usd=team.max_cost_usd,
    )
    team.cost_budget_windows.append(window)
    return window


def _next_utc_month_start(value: datetime) -> datetime:
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if normalized.month == 12:
        return datetime(normalized.year + 1, 1, 1, tzinfo=UTC)
    return datetime(normalized.year, normalized.month + 1, 1, tzinfo=UTC)


def _advance_team_cost_budget_window(team: Team, *, now: datetime) -> bool:
    if (
        team.max_cost_usd is None
        or team.cost_budget_renewal_mode != TeamCostBudgetRenewalMode.MONTHLY_UTC
    ):
        return False
    active_window = _active_team_cost_budget_window(team)
    if active_window is None:
        # A pre-window Team stays lifetime-based until an operator resets it.
        return False
    effective_now = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    next_boundary = _next_utc_month_start(active_window.started_at)
    if active_window.monthly_renewal_not_before is not None:
        next_boundary = max(next_boundary, active_window.monthly_renewal_not_before)
    advanced = False
    while next_boundary <= effective_now:
        _open_team_cost_budget_window(
            team,
            now=next_boundary,
            kind=TeamCostBudgetWindowKind.MONTHLY_RENEWAL,
            actor="system:team-cost-budget-renewal",
            reason="UTC monthly cost budget renewal.",
        )
        next_boundary = _next_utc_month_start(next_boundary)
        advanced = True
    return advanced


def _team_cost_budget_window_advance_due(team: Team, *, now: datetime) -> bool:
    """Return whether advancing this Team would open a new monthly window."""

    if (
        team.max_cost_usd is None
        or team.cost_budget_renewal_mode != TeamCostBudgetRenewalMode.MONTHLY_UTC
    ):
        return False
    active_window = _active_team_cost_budget_window(team)
    if active_window is None:
        return False
    effective_now = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    next_boundary = _next_utc_month_start(active_window.started_at)
    if active_window.monthly_renewal_not_before is not None:
        next_boundary = max(next_boundary, active_window.monthly_renewal_not_before)
    return next_boundary <= effective_now


def _default_memory_scope(project_id: str | None, agent_id: str | None) -> str:
    if agent_id is not None:
        return TeamMemoryScope.AGENT_PRIVATE.value
    if project_id is not None:
        return TeamMemoryScope.PROJECT.value
    return TeamMemoryScope.TEAM_GLOBAL.value


def _validate_resolved_human_release_policy(
    release_policy: ProjectReleasePolicy | None,
) -> None:
    requirement = release_policy.human_review_requirement if release_policy is not None else None
    if requirement is None:
        return
    if requirement.directory_path is None:
        raise ValueError("human review policy must be resolved to an Admin directory")
    if not requirement.resolved_reviewer_user_ids:
        raise ValueError("human review policy must resolve at least one decision-eligible reviewer")


def _validate_admin_memory_scope(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    agent_id: str | None,
    scope: TeamMemoryScope,
) -> None:
    if project_id is not None:
        project = _require_present(state.projects, project_id, "project")
        if project.team_id != team_id:
            raise ValueError(f"project {project_id} belongs to another team")
    if agent_id is not None:
        agent = _require_present(state.agents, agent_id, "agent")
        if agent.team_id != team_id:
            raise ValueError(f"agent {agent_id} belongs to another team")
    if scope == TeamMemoryScope.PROJECT and project_id is None:
        raise ValueError("project-scoped memory requires project_id")
    if scope == TeamMemoryScope.AGENT_PRIVATE and agent_id is None:
        raise ValueError("agent-private memory requires agent_id")


def _human_command_created_domain_ids(result: Mapping[str, Any]) -> list[str]:
    created_ids: list[str] = []
    for field_name, raw_value in result.items():
        if field_name.startswith("created_") and field_name.endswith("_ids"):
            values: Sequence[Any] = (
                raw_value
                if isinstance(raw_value, Sequence)
                and not isinstance(raw_value, (str, bytes, bytearray))
                else ()
            )
        elif field_name in {
            "case_id",
            "correction_task_id",
            "human_review_decision_id",
            "triage_task_id",
        }:
            values = (raw_value,)
        else:
            continue
        for value in values:
            text = str(value or "").strip()
            if text and text not in created_ids:
                created_ids.append(text)
    return created_ids


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


def _evidence_utc_datetime(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{label} must be an ISO 8601 datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _attach_external_source_evidence_snapshot(
    state: TeamState,
    *,
    snapshot: ExternalSourceEvidenceSnapshot,
    case_ids: Sequence[str],
    task_ids: Sequence[str],
    now: datetime,
) -> None:
    affected_active_task_ids: set[str] = set()
    for case_id in case_ids:
        case = _require_present(state.cases, case_id, "case")
        if case.team_id != snapshot.team_id:
            raise ValueError(f"case {case.id} does not belong to snapshot team {snapshot.team_id}")
        if snapshot.project_id is not None and case.project_id != snapshot.project_id:
            raise ValueError(
                f"case {case.id} does not belong to snapshot project {snapshot.project_id}"
            )
        if snapshot.id in case.source_evidence_snapshot_ids:
            continue
        case.source_evidence_snapshot_ids = _append_unique(
            case.source_evidence_snapshot_ids,
            snapshot.id,
        )
        case.updated_at = now
        for linked_task_id in case.task_ids:
            linked_task = _require_present(state.tasks, linked_task_id, "task")
            if linked_task.state in _NON_TERMINAL_TASK_STATES:
                affected_active_task_ids.add(linked_task.id)
    for task_id in task_ids:
        task = _require_present(state.tasks, task_id, "task")
        project = _require_present(state.projects, task.project_id, "project")
        if project.team_id != snapshot.team_id:
            raise ValueError(f"task {task.id} does not belong to snapshot team {snapshot.team_id}")
        if snapshot.project_id is not None and task.project_id != snapshot.project_id:
            raise ValueError(
                f"task {task.id} does not belong to snapshot project {snapshot.project_id}"
            )
        if snapshot.id in task.source_evidence_snapshot_ids:
            continue
        task.source_evidence_snapshot_ids = _append_unique(
            task.source_evidence_snapshot_ids,
            snapshot.id,
        )
        task.updated_at = now
        if task.state in _NON_TERMINAL_TASK_STATES:
            affected_active_task_ids.add(task.id)
    for affected_task_id in sorted(affected_active_task_ids):
        affected_task = state.tasks[affected_task_id]
        affected_task.updated_at = now
        _mark_agent_work_due(
            state,
            agent_id=affected_task.owner_agent_id,
            now=now,
        )


def _unfrozen_project_worktree_context_ids(
    state: TeamState,
    *,
    project: Project,
) -> list[str]:
    newest_by_lineage: dict[tuple[str, str, str, str], GitWorkContext] = {}
    unresolved_contexts: list[GitWorkContext] = []
    for context in state.git_work_contexts.values():
        if context.worktree_path is None:
            continue
        task = state.tasks.get(context.task_id)
        if task is None:
            continue
        session_id = _git_work_context_session_id_for_cleanup_scope(state, context)
        if session_id is None:
            unresolved_contexts.append(context)
            continue
        lineage = (
            context.agent_id,
            session_id,
            str(PurePosixPath(context.worktree_path)),
            context.agent_branch,
        )
        current = newest_by_lineage.get(lineage)
        if current is None or (
            _team_common._ensure_utc(context.created_at),
            _team_common._ensure_utc(context.updated_at),
            context.id,
        ) > (
            _team_common._ensure_utc(current.created_at),
            _team_common._ensure_utc(current.updated_at),
            current.id,
        ):
            newest_by_lineage[lineage] = context

    context_ids: list[str] = []
    for context in (*newest_by_lineage.values(), *unresolved_contexts):
        task = state.tasks.get(context.task_id)
        if task is None or task.project_id != project.id:
            continue
        covered = any(
            candidate.project_id == project.id and candidate.git_work_context_id == context.id
            for candidate in state.worktree_cleanup_candidates.values()
        )
        if not covered:
            context_ids.append(context.id)
    return sorted(context_ids)


def _git_work_context_session_id_for_cleanup_scope(
    state: TeamState,
    context: GitWorkContext,
) -> str | None:
    if context.dispatch_id is not None:
        dispatch = state.dispatches.get(context.dispatch_id)
        if (
            dispatch is None
            or dispatch.agent_id != context.agent_id
            or context.task_id not in dispatch.task_ids
        ):
            return None
        return dispatch.session_id
    agent = state.agents.get(context.agent_id)
    return agent.session_id if agent is not None else None


class TeamAdminService:
    _PROJECT_REPOSITORY_BINDING_KIND = "team_projects"

    def __init__(
        self,
        repository: TeamStateRepository,
        *,
        managed_git_admin: Any | None = None,
        human_review_eligibility_resolver: Callable[[Any], list[str]] | None = None,
    ) -> None:
        self._repository = repository
        self._managed_git_admin = managed_git_admin
        self._human_review_eligibility_resolver = human_review_eligibility_resolver

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    def _run_idempotent_human_command(
        self,
        *,
        operation: str,
        caller_user_id: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
        normalized_payload: Mapping[str, Any],
        apply_command: Callable[[TeamState, dict[str, Any]], None],
        expected_state_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        """Apply one human-facing transition with state, outbox, and replay audit atomically."""

        normalized_operation = _required_text(operation, "operation")
        normalized_caller = _required_text(caller_user_id, "caller_user_id")
        normalized_scope_kind = _required_text(scope_kind, "scope_kind")
        normalized_scope_id = _required_text(scope_id, "scope_id")
        normalized_request_id = _required_text(request_id, "request_id")
        payload_digest = self._repository.normalized_payload_digest(dict(normalized_payload))

        def _transaction(state: TeamState, tx: Any):
            replay = tx.get_idempotency_record(
                tool_name=normalized_operation,
                caller_identity=normalized_caller,
                scope_kind=normalized_scope_kind,
                scope_id=normalized_scope_id,
                request_id=normalized_request_id,
            )
            if replay is not None:
                if replay.normalized_payload_digest != payload_digest:
                    raise TeamIdempotencyConflictError(
                        tool_name=normalized_operation,
                        caller_identity=normalized_caller,
                        scope_kind=normalized_scope_kind,
                        scope_id=normalized_scope_id,
                        request_id=normalized_request_id,
                        existing_digest=replay.normalized_payload_digest,
                        attempted_digest=payload_digest,
                    )
                response = dict(replay.response_payload)
                response["idempotent_replay"] = True
                return state, response
            if expected_state_revision is not None and state.revision != expected_state_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_state_revision,
                    current_revision=state.revision,
                )

            result: dict[str, Any] = {
                "success": False,
                "events": [],
                "idempotent_replay": False,
            }
            apply_command(state, result)
            now = utc_now()
            state.revision += 1
            state.updated_at = now

            outbox_records = [
                tx.enqueue_outbox_record(record)
                for record in _team_event_outbox_records(result.get("events") or [])
            ]
            result["success"] = True
            result["revision"] = state.revision
            result["outbox_record_ids"] = [record.id for record in outbox_records]
            created_domain_ids = _human_command_created_domain_ids(result)
            for record in outbox_records:
                if record.id not in created_domain_ids:
                    created_domain_ids.append(record.id)
            tx.store_idempotency_record(
                TeamIdempotencyRecord(
                    id=f"idempotency-{uuid4().hex}",
                    tool_name=normalized_operation,
                    caller_identity=normalized_caller,
                    scope_kind=normalized_scope_kind,
                    scope_id=normalized_scope_id,
                    request_id=normalized_request_id,
                    normalized_payload_digest=payload_digest,
                    response_payload=result,
                    created_domain_ids=created_domain_ids,
                )
            )
            return state, result

        return self._repository.store.update_state_with_command_transaction(_transaction)

    def _project_repository_binding_exists(
        self,
        managed_repo_id: str,
        project_id: str,
    ) -> bool:
        managed_git = self._managed_git_admin
        if managed_git is None:
            return False
        record = managed_git.get_repository(managed_repo_id)
        return any(
            isinstance(binding, Mapping)
            and binding.get("kind") == self._PROJECT_REPOSITORY_BINDING_KIND
            and binding.get("binding_id") == project_id
            for binding in record.get("bindings") or []
        )

    def _resolve_managed_git_default_branch(
        self,
        *,
        managed_repo_id: str,
        directory_path: str,
    ) -> object:
        managed_git = self._managed_git_admin
        if managed_git is None:
            raise RuntimeError("managed Git default-branch resolution is unavailable")
        get_repository = getattr(managed_git, "get_repository", None)
        if not callable(get_repository):
            raise RuntimeError("managed Git repository lookup is unavailable")
        record = get_repository(
            managed_repo_id,
            directory_path=directory_path,
        )
        if not isinstance(record, Mapping):
            raise RuntimeError("managed Git repository lookup returned an invalid record")
        return record.get("default_branch")

    def _set_project_repository_binding(
        self,
        managed_repo_id: str,
        project_id: str,
        *,
        bound: bool,
    ) -> None:
        managed_git = self._managed_git_admin
        if managed_git is None:
            return
        operation = managed_git.bind if bound else managed_git.unbind
        operation(
            managed_repo_id,
            kind=self._PROJECT_REPOSITORY_BINDING_KIND,
            binding_id=project_id,
        )

    @contextmanager
    def _project_repository_binding_lock(self):
        lock_path = self._repository.store.path.parent / "project-repository-bindings.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _update_with_project_repository_binding(
        self,
        mutate: Any,
        *,
        project_id: str,
        next_managed_repo_id: str | None,
        expected_revision: int | None,
    ) -> TeamState:
        managed_git = self._managed_git_admin
        if managed_git is None:
            return self._repository.update(mutate, expected_revision=expected_revision)

        with self._project_repository_binding_lock():
            current_project = self._repository.read().projects.get(project_id)
            previous_managed_repo_id = (
                current_project.git.managed_repo_id
                if current_project is not None and current_project.git is not None
                else None
            )
            return self._update_with_locked_project_repository_binding(
                mutate,
                project_id=project_id,
                previous_managed_repo_id=previous_managed_repo_id,
                next_managed_repo_id=next_managed_repo_id,
                expected_revision=expected_revision,
            )

    def _update_with_locked_project_repository_binding(
        self,
        mutate: Any,
        *,
        project_id: str,
        previous_managed_repo_id: str | None,
        next_managed_repo_id: str | None,
        expected_revision: int | None,
    ) -> TeamState:
        previous_was_bound = bool(
            previous_managed_repo_id
            and self._project_repository_binding_exists(
                previous_managed_repo_id,
                project_id,
            )
        )
        next_was_bound = bool(
            next_managed_repo_id
            and (
                (next_managed_repo_id == previous_managed_repo_id and previous_was_bound)
                or self._project_repository_binding_exists(next_managed_repo_id, project_id)
            )
        )

        def restore_original_bindings() -> None:
            rollback_errors: list[BaseException] = []
            if previous_managed_repo_id and previous_was_bound:
                try:
                    self._set_project_repository_binding(
                        previous_managed_repo_id,
                        project_id,
                        bound=True,
                    )
                except BaseException as exc:
                    rollback_errors.append(exc)
            if next_managed_repo_id and not next_was_bound:
                try:
                    self._set_project_repository_binding(
                        next_managed_repo_id,
                        project_id,
                        bound=False,
                    )
                except BaseException as exc:
                    rollback_errors.append(exc)
            if rollback_errors:
                raise RuntimeError(
                    "could not restore managed repository bindings after Team project failure"
                ) from rollback_errors[0]

        try:
            if next_managed_repo_id and not next_was_bound:
                self._set_project_repository_binding(
                    next_managed_repo_id,
                    project_id,
                    bound=True,
                )
            if (
                previous_managed_repo_id
                and previous_managed_repo_id != next_managed_repo_id
                and previous_was_bound
            ):
                self._set_project_repository_binding(
                    previous_managed_repo_id,
                    project_id,
                    bound=False,
                )
        except BaseException:
            restore_original_bindings()
            raise

        try:
            return self._repository.update(mutate, expected_revision=expected_revision)
        except BaseException:
            restore_original_bindings()
            raise

    def dismiss_action_record_attention(
        self,
        *,
        action_record_id: str,
        dismissed_by: str | None = None,
        dismissed_reason: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            record = _require_present(
                state.action_records,
                _required_text(action_record_id, "action_record_id"),
                "action record",
            )
            if record.success:
                raise ValueError(
                    f"action record {record.id} is successful and does not need dismissal"
                )
            record.dismissed_at = utc_now()
            record.dismissed_by = _optional_text(dismissed_by) or "operator"
            record.dismissed_reason = _optional_text(dismissed_reason)
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def resolve_terminal_blocker_as_operator(
        self,
        *,
        blocker_id: str,
        note: str,
        operator_actor_id: str,
        resolution_refs: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_blocker_id = _required_text(blocker_id, "blocker_id")
        normalized_note = _required_text(note, "note")
        normalized_actor_id = _required_text(operator_actor_id, "operator_actor_id")
        normalized_refs = _unique_text_list(resolution_refs or [], "resolution_refs")
        result: dict[str, Any] = {
            "success": False,
            "blocker_id": normalized_blocker_id,
            "operator_actor_id": normalized_actor_id,
        }

        def _mutate(state: TeamState) -> TeamState:
            blocker = _require_present(
                state.blockers,
                normalized_blocker_id,
                "blocker",
            )
            if blocker.state != TeamBlockerState.OPEN:
                raise ValueError(f"blocker {blocker.id} is {blocker.state.value}")
            if blocker.route not in {
                TeamBlockerRoute.GOVERNANCE,
                TeamBlockerRoute.OPERATOR,
            }:
                raise ValueError(
                    f"blocker {blocker.id} is routed to {blocker.route.value}; "
                    "operator resolution requires governance or operator routing"
                )
            if blocker.source_kind == TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST:
                recovery_action = (
                    "reassign"
                    if blocker.reason_code == HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE
                    else "resume"
                )
                raise ValueError(
                    f"blocker {blocker.id} belongs to human review request "
                    f"{blocker.source_id}; recover it with POST "
                    f"/human-review-requests/{blocker.source_id}/{recovery_action}; "
                    "generic blocker resolution cannot bypass the human-review lifecycle"
                )
            if blocker.source_kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR:
                raise ValueError(
                    f"blocker {blocker.id} belongs to candidate-correction successor "
                    f"recovery; use POST /candidate-correction-recoveries/{blocker.id} "
                    "with retry or create_verified_replacement"
                )
            now = utc_now()
            route_before_resolution = blocker.route.value
            if (
                blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE
                and blocker.reason_code.startswith("deliverable_acceptance_deferred:")
            ):
                from .domain.review import retry_deferred_deliverable_acceptance

                append_blocker_event(
                    state,
                    blocker=blocker,
                    event_type=TeamBlockerEventType.PROGRESS,
                    note=normalized_note,
                    reference_ids=normalized_refs,
                    metadata={"operator_actor_id": normalized_actor_id},
                    now=now,
                )
                retry_deferred_deliverable_acceptance(
                    state,
                    blocker_id=blocker.id,
                    actor_agent_id=None,
                    result=result,
                    now=now,
                )
                result.update(
                    {
                        "success": True,
                        "event_id": next(
                            (
                                event.id
                                for event in reversed(list(state.blocker_events.values()))
                                if event.blocker_id == blocker.id
                            ),
                            None,
                        ),
                        "route_before_resolution": route_before_resolution,
                    }
                )
                return state
            _prepare_blocker_source_for_resolution(
                state,
                blocker=blocker,
                now=now,
            )
            affected_task_ids = {
                task_id
                for task_id in (
                    blocker.task_id,
                    *blocker.affected_task_ids,
                    (
                        blocker.source_id
                        if blocker.source_kind == TeamBlockerSourceKind.TASK
                        else None
                    ),
                )
                if task_id is not None
            }
            event = resolve_blocker(
                state,
                blocker=blocker,
                actor_agent_id=None,
                dispatch_id=None,
                note=normalized_note,
                reference_ids=normalized_refs,
                metadata={"operator_actor_id": normalized_actor_id},
                now=now,
            )
            deliverable_acceptance_route_handled = False
            if blocker.source_kind == TeamBlockerSourceKind.DELIVERABLE:
                from .domain.review import retry_deliverable_acceptance_if_reviewed

                acceptance_completed = retry_deliverable_acceptance_if_reviewed(
                    state,
                    deliverable_id=blocker.source_id,
                    actor_agent_id=None,
                    result=result,
                    now=now,
                )
                deliverable_acceptance_route_handled = bool(
                    acceptance_completed or result.get("deliverable_acceptance_deferred")
                )
            if blocker.source_kind == TeamBlockerSourceKind.AGENT_EXECUTION_STALL:
                _mark_agent_work_due(
                    state,
                    agent_id=blocker.source_id,
                    now=now,
                )
                _append_agent_wake_requested_event(
                    state,
                    agent_id=blocker.source_id,
                    result=result,
                    reason="blocker_resolved",
                    now=now,
                    delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                    idempotency_suffix=blocker.id,
                )
            preparation_recovery_item = (
                create_rejected_candidate_correction_preparation_recovery_inbox(
                    state,
                    blocker=blocker,
                    result=result,
                    now=now,
                )
            )
            ready_owner_ids: set[str] = set()
            if not deliverable_acceptance_route_handled:
                for task_id in sorted(affected_task_ids):
                    task = state.tasks.get(task_id)
                    if task is None or task.owner_agent_id is None:
                        continue
                    try:
                        readiness = assess_task(state, task.id).readiness.value
                    except TaskAssessmentBoundaryError:
                        continue
                    if readiness == "actionable":
                        ready_owner_ids.add(task.owner_agent_id)
            if preparation_recovery_item is not None:
                ready_owner_ids.discard(preparation_recovery_item.recipient_agent_id)
            for ready_owner_id in sorted(ready_owner_ids):
                _mark_agent_work_due(
                    state,
                    agent_id=ready_owner_id,
                    now=now,
                )
                _append_agent_wake_requested_event(
                    state,
                    agent_id=ready_owner_id,
                    result=result,
                    reason="task_ready",
                    now=now,
                    delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                    idempotency_suffix=blocker.id,
                )
            result.update(
                {
                    "success": True,
                    "event_id": event.id,
                    "route_before_resolution": route_before_resolution,
                }
            )
            return state

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
            expected_revision=expected_revision,
        )
        result["revision"] = state.revision
        result["blocker"] = state.blockers[normalized_blocker_id].model_dump(mode="json")
        return state, result

    def authorize_deliverable_acceptance_verification_waiver_as_operator(
        self,
        *,
        deliverable_id: str,
        reason: str,
        operator_actor_id: str,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, Deliverable]:
        normalized_deliverable_id = _required_text(deliverable_id, "deliverable_id")
        _required_text(reason, "reason")
        _required_text(operator_actor_id, "operator_actor_id")

        # Keep the endpoint callable for one compatibility release, but do not
        # mutate state or create a waiver that a new release could consume.
        state = self._repository.read()
        if normalized_deliverable_id not in state.deliverables:
            raise ValueError(f"deliverable {normalized_deliverable_id} does not exist")
        return state, state.deliverables[normalized_deliverable_id]

    def start_dependency_composition_recovery_as_operator(
        self,
        *,
        blocker_id: str,
        resolver_agent_id: str,
        operator_actor_id: str,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_blocker_id = _required_text(blocker_id, "blocker_id")
        normalized_resolver_id = _required_text(resolver_agent_id, "resolver_agent_id")
        normalized_operator_id = _required_text(operator_actor_id, "operator_actor_id")
        result: dict[str, Any] = {
            "success": False,
            "blocker_id": normalized_blocker_id,
            "resolver_agent_id": normalized_resolver_id,
            "operator_actor_id": normalized_operator_id,
            "created_task_ids": [],
            "created_inbox_item_ids": [],
            "blocker_event_ids": [],
            "events": [],
        }

        def _mutate(state: TeamState) -> TeamState:
            blocker = _require_present(state.blockers, normalized_blocker_id, "blocker")
            resolver = _require_present(state.agents, normalized_resolver_id, "resolver agent")
            recoveries = [
                recovery
                for recovery in state.dependency_composition_recoveries.values()
                if recovery.blocker_id == blocker.id
                and recovery.state
                in {
                    DependencyCompositionRecoveryState.AWAITING_ASSIGNMENT,
                    DependencyCompositionRecoveryState.IN_PROGRESS,
                }
            ]
            if len(recoveries) != 1:
                raise ValueError(
                    f"blocker {blocker.id} has {len(recoveries)} actionable "
                    "dependency composition recoveries"
                )
            task = start_dependency_composition_recovery(
                state,
                recovery=recoveries[0],
                blocker=blocker,
                actor=None,
                operator_actor_id=normalized_operator_id,
                resolver=resolver,
                dispatch_id=None,
                result=result,
                now=utc_now(),
            )
            result.update(
                {
                    "success": True,
                    "recovery_id": recoveries[0].id,
                    "recovery_task_id": task.id,
                    "state": recoveries[0].state.value,
                }
            )
            return state

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
            expected_revision=expected_revision,
        )
        result["revision"] = state.revision
        return state, result

    def recover_candidate_correction_successor_as_operator(
        self,
        *,
        blocker_id: str,
        action: str,
        operator_actor_id: str,
        reason: str | None = None,
        instruction: str | None = None,
        owner_agent_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_blocker_id = _required_text(blocker_id, "blocker_id")
        normalized_action = _required_text(action, "action")
        if normalized_action not in {
            "retry",
            "create_verified_replacement",
        }:
            raise ValueError(f"unsupported candidate correction recovery action {action!r}")
        normalized_actor_id = _required_text(operator_actor_id, "operator_actor_id")
        normalized_reason = _optional_text(reason)
        normalized_instruction = _optional_text(instruction)
        normalized_owner_id = _optional_text(owner_agent_id)
        if (
            normalized_instruction is not None
            and normalized_action != "create_verified_replacement"
        ):
            raise ValueError("instruction is supported only when creating a verified replacement")
        result: dict[str, Any] = {
            "success": False,
            "action": normalized_action,
            "blocker_id": normalized_blocker_id,
            "operator_actor_id": normalized_actor_id,
            "events": [],
        }

        def _mutate(state: TeamState) -> TeamState:
            recover_candidate_correction_successor(
                state,
                blocker_id=normalized_blocker_id,
                action=normalized_action,
                operator_actor_id=normalized_actor_id,
                reason=normalized_reason,
                instruction=normalized_instruction,
                owner_agent_id=normalized_owner_id,
                result=result,
            )
            return state

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
            expected_revision=expected_revision,
        )
        result["revision"] = state.revision
        result["blocker"] = state.blockers[normalized_blocker_id].model_dump(mode="json")
        if recovery_task_id := result.get("recovery_task_id"):
            result["recovery_task"] = state.tasks[recovery_task_id].model_dump(mode="json")
        if release_id := result.get("release_package_id"):
            result["release_package"] = state.release_packages[release_id].model_dump(mode="json")
        return state, result

    def mark_action_feedback_delivered(
        self,
        *,
        agent_id: str,
        source_dispatch_id: str,
        delivery_dispatch_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_source_dispatch_id = _required_text(
            source_dispatch_id,
            "source_dispatch_id",
        )
        normalized_delivery_dispatch_id = _required_text(
            delivery_dispatch_id,
            "delivery_dispatch_id",
        )

        def _mutate(state: TeamState) -> TeamState:
            delivered_at = utc_now()
            for record in state.action_records.values():
                if record.agent_id != normalized_agent_id:
                    continue
                if record.dispatch_id != normalized_source_dispatch_id:
                    continue
                if record.success or record.feedback_delivered_at is not None:
                    continue
                record.feedback_delivered_at = delivered_at
                record.feedback_delivered_by_dispatch_id = normalized_delivery_dispatch_id
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def create_or_update_memory(
        self,
        *,
        memory_id: str | None,
        team_id: str,
        kind: str,
        title: str,
        summary: str,
        project_id: str | None = None,
        codebase_id: str | None = None,
        agent_id: str | None = None,
        scope: str | None = None,
        body: str | None = None,
        tags: list[str] | None = None,
        source_refs: list[str] | None = None,
        artifact_refs: list[str] | None = None,
        outcomes: list[Mapping[str, Any]] | None = None,
        confidence: float | None = None,
        supersedes_memory_id: str | None = None,
        created_by: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, TeamMemoryRecord]:
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_memory_id = _optional_text(memory_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        normalized_scope = TeamMemoryScope(
            _optional_text(scope)
            or _default_memory_scope(normalized_project_id, normalized_agent_id)
        )
        normalized_kind = TeamMemoryKind(_required_text(kind, "kind"))
        normalized_title = _required_text(title, "title")
        normalized_summary = _required_text(summary, "summary")
        normalized_tags = [value.casefold() for value in _unique_text_list(tags or [], "tags")]
        normalized_source_refs = _unique_text_list(source_refs or [], "source_refs")
        normalized_artifact_refs = _unique_text_list(artifact_refs or [], "artifact_refs")
        normalized_supersedes = _optional_text(supersedes_memory_id)

        created_record: TeamMemoryRecord | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal created_record
            _require_present(state.teams, normalized_team_id, "team")
            _validate_admin_memory_scope(
                state,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                agent_id=normalized_agent_id,
                scope=normalized_scope,
            )
            effective_memory_id = normalized_memory_id or _generated_id(
                prefix="memory",
                labels=[normalized_team_id, normalized_title],
                existing_ids=state.memories,
            )
            now = utc_now()
            memory = state.memories.get(effective_memory_id)
            if memory is None:
                memory = TeamMemoryRecord(
                    id=effective_memory_id,
                    team_id=normalized_team_id,
                    kind=normalized_kind,
                    title=normalized_title,
                    summary=normalized_summary,
                    created_by=_optional_text(created_by) or "operator",
                    created_at=now,
                    updated_at=now,
                )
            elif memory.team_id != normalized_team_id:
                raise ValueError(f"memory {effective_memory_id} belongs to another team")
            memory.project_id = normalized_project_id
            memory.codebase_id = _optional_text(codebase_id)
            memory.agent_id = normalized_agent_id
            memory.scope = normalized_scope
            memory.kind = normalized_kind
            memory.title = normalized_title
            memory.summary = normalized_summary
            memory.body = _optional_text(body)
            memory.tags = list(normalized_tags)
            memory.source_refs = list(normalized_source_refs)
            memory.artifact_refs = list(normalized_artifact_refs)
            memory.status = TeamMemoryStatus.ACTIVE
            memory.superseded_by_memory_id = None
            memory.confidence = confidence
            memory.supersedes_memory_id = normalized_supersedes
            memory.updated_at = now
            state.memories[memory.id] = memory
            if normalized_supersedes is not None:
                superseded = _require_present(state.memories, normalized_supersedes, "memory")
                if superseded.team_id != normalized_team_id:
                    raise ValueError("superseded memory belongs to another team")
                superseded.status = TeamMemoryStatus.SUPERSEDED
                superseded.superseded_by_memory_id = memory.id
                superseded.updated_at = now
            created_record = memory
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if created_record is None:  # pragma: no cover - defensive guard
            raise RuntimeError("memory mutation did not produce a record")
        return state, state.memories[created_record.id]

    def archive_memory(
        self,
        *,
        memory_id: str,
        superseded_by_memory_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, TeamMemoryRecord]:
        normalized_memory_id = _required_text(memory_id, "memory_id")
        normalized_superseded_by = _optional_text(superseded_by_memory_id)
        archived_record: TeamMemoryRecord | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal archived_record
            memory = _require_present(state.memories, normalized_memory_id, "memory")
            if normalized_superseded_by is not None:
                replacement = _require_present(state.memories, normalized_superseded_by, "memory")
                if replacement.team_id != memory.team_id:
                    raise ValueError("replacement memory belongs to another team")
            memory.status = (
                TeamMemoryStatus.SUPERSEDED
                if normalized_superseded_by is not None
                else TeamMemoryStatus.ARCHIVED
            )
            memory.superseded_by_memory_id = normalized_superseded_by
            memory.updated_at = utc_now()
            archived_record = memory
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if archived_record is None:  # pragma: no cover - defensive guard
            raise RuntimeError("memory archive did not produce a record")
        return state, state.memories[archived_record.id]

    def create_team(
        self,
        *,
        team_id: str | None,
        name: str,
        manager_agent_id: str | None,
        manager_session_id: str,
        manager_display_name: str | None = None,
        manager_role_id: str | None = None,
        manager_role_name: str | None = None,
        operator_communication_target_id: str | None = None,
        max_cost_usd: object | None = None,
        cost_warning_ratio: object | None = None,
        cost_budget_renewal_mode: object | None = None,
        escalate_to_governance: bool = False,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_team_id = _optional_text(team_id)
        normalized_name = _required_text(name, "name")
        normalized_manager_agent_id = _optional_text(manager_agent_id)
        normalized_manager_session_id = _required_text(manager_session_id, "manager_session_id")
        normalized_manager_display_name = _optional_text(manager_display_name)
        normalized_manager_role_id = _optional_text(manager_role_id)
        normalized_manager_role_name = _optional_text(manager_role_name) or "Manager"
        normalized_operator_target = _optional_text(operator_communication_target_id)

        def _mutate(state: TeamState) -> TeamState:
            effective_team_id = normalized_team_id or _generated_id(
                prefix="team",
                labels=[normalized_name],
                existing_ids=state.teams,
            )
            _require_absent(state.teams, effective_team_id, "team")
            role_base_id = _default_role_id(
                team_id=effective_team_id,
                project_id=None,
                role_key="manager",
            )
            role_id = normalized_manager_role_id or _unique_id_from_base(
                role_base_id,
                existing_ids=state.roles,
            )
            _require_absent(state.roles, role_id, "role")
            effective_manager_agent_id = normalized_manager_agent_id or _generated_id(
                prefix="agent",
                labels=[
                    effective_team_id,
                    normalized_manager_display_name or normalized_manager_role_name,
                ],
                existing_ids=state.agents,
            )
            existing_manager = state.agents.get(effective_manager_agent_id)
            if existing_manager is not None and existing_manager.team_id is not None:
                raise ValueError(
                    f"agent {effective_manager_agent_id} already belongs to team "
                    f"{existing_manager.team_id}"
                )
            now = utc_now()
            role = TeamRole(
                id=role_id,
                team_id=effective_team_id,
                name=normalized_manager_role_name,
                permissions=[RolePermission.CREATE_ROOT_TASK],
                created_at=now,
                updated_at=now,
            )
            state.roles[role.id] = role
            if existing_manager is not None:
                manager = existing_manager
                manager.team_id = effective_team_id
                manager.display_name = normalized_manager_display_name or manager.display_name
                manager.role_id = role.id
                manager.session_id = normalized_manager_session_id
                manager.reports_to_agent_id = None
                manager.subordinate_agent_ids = []
                manager.ephemeral_policy = role.ephemeral_policy.model_copy(deep=True)
            else:
                manager = PersistentAgent(
                    id=effective_manager_agent_id,
                    team_id=effective_team_id,
                    display_name=normalized_manager_display_name,
                    role_id=role.id,
                    session_id=normalized_manager_session_id,
                    reports_to_agent_id=None,
                    ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                )
                state.agents[manager.id] = manager
            team = Team(
                id=effective_team_id,
                name=normalized_name,
                manager_agent_id=manager.id,
                operator_communication_target_id=normalized_operator_target,
                max_cost_usd=max_cost_usd,
                cost_warning_ratio=_cost_warning_ratio(cost_warning_ratio),
                cost_budget_renewal_mode=_cost_budget_renewal_mode(cost_budget_renewal_mode),
                escalate_to_governance=escalate_to_governance,
                created_at=now,
                updated_at=now,
            )
            if team.max_cost_usd is not None:
                _open_team_cost_budget_window(
                    team,
                    now=now,
                    kind=TeamCostBudgetWindowKind.CONFIGURED,
                    actor="operator:team-config",
                    reason="Initial cost budget configured.",
                )
            state.teams[effective_team_id] = team
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def update_team(
        self,
        *,
        team_id: str,
        name: str | None = None,
        manager_agent_id: str | None = None,
        operator_communication_target_id: str | None = None,
        set_operator_communication_target_id: bool = False,
        max_cost_usd: object | None = None,
        set_max_cost_usd: bool = False,
        cost_warning_ratio: object | None = None,
        set_cost_warning_ratio: bool = False,
        cost_budget_renewal_mode: object | None = None,
        set_cost_budget_renewal_mode: bool = False,
        escalate_to_governance: bool | None = None,
        archived: bool | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_manager_agent_id = _optional_text(manager_agent_id)

        def _superior_depth(
            state: TeamState,
            *,
            agent_id: str,
            superior_id: str,
        ) -> int | None:
            depth = 0
            current_id = state.agents.get(agent_id).reports_to_agent_id
            seen: set[str] = set()
            while current_id is not None:
                if current_id in seen:
                    return None
                seen.add(current_id)
                depth += 1
                if current_id == superior_id:
                    return depth
                current = state.agents.get(current_id)
                if current is None:
                    return None
                current_id = current.reports_to_agent_id
            return None

        def _mutate(state: TeamState) -> TeamState:
            team = _require_present(state.teams, team_id, "team")
            mutation_now = utc_now()
            _advance_team_cost_budget_window(team, now=mutation_now)
            if (
                normalized_manager_agent_id is not None
                and normalized_manager_agent_id != team.manager_agent_id
            ):
                old_manager = _require_present(
                    state.agents,
                    team.manager_agent_id,
                    "current manager",
                )
                new_manager = _require_present(
                    state.agents,
                    normalized_manager_agent_id,
                    "manager agent",
                )
                if new_manager.team_id != team.id:
                    raise ValueError(f"manager agent {new_manager.id} is not in team {team.id}")
                if new_manager.status != AgentStatus.ACTIVE:
                    raise ValueError(f"manager agent {new_manager.id} is not active")
                new_under_old_depth = _superior_depth(
                    state,
                    agent_id=new_manager.id,
                    superior_id=old_manager.id,
                )
                if new_under_old_depth is not None and new_under_old_depth > 1:
                    raise ValueError(
                        f"manager agent {new_manager.id} is nested under current "
                        f"manager {old_manager.id}; reassign its current superior first"
                    )
                if (
                    _superior_depth(
                        state,
                        agent_id=old_manager.id,
                        superior_id=new_manager.id,
                    )
                    is not None
                ):
                    raise ValueError(
                        f"manager agent {new_manager.id} would create a reporting cycle"
                    )

                previous_superior_id = new_manager.reports_to_agent_id
                if previous_superior_id is not None and previous_superior_id in state.agents:
                    previous_superior = state.agents[previous_superior_id]
                    previous_superior.subordinate_agent_ids = _remove_value(
                        previous_superior.subordinate_agent_ids,
                        new_manager.id,
                    )

                moved_report_ids = [
                    report_id
                    for report_id in old_manager.subordinate_agent_ids
                    if report_id != new_manager.id
                ]
                for report_id in moved_report_ids:
                    report = state.agents.get(report_id)
                    if report is None or report.team_id != team.id:
                        continue
                    report.reports_to_agent_id = new_manager.id

                old_manager.subordinate_agent_ids = []
                new_manager.reports_to_agent_id = None
                for report_id in moved_report_ids:
                    new_manager.subordinate_agent_ids = _append_unique(
                        new_manager.subordinate_agent_ids,
                        report_id,
                    )
                role = (
                    state.roles.get(new_manager.role_id)
                    if new_manager.role_id is not None
                    else None
                )
                if role is not None and RolePermission.CREATE_ROOT_TASK not in role.permissions:
                    role.permissions.append(RolePermission.CREATE_ROOT_TASK)
                    role.updated_at = utc_now()
                team.manager_agent_id = new_manager.id
            if name is not None:
                team.name = name
            if set_operator_communication_target_id:
                team.operator_communication_target_id = operator_communication_target_id
            if set_cost_budget_renewal_mode:
                previous_renewal_mode = team.cost_budget_renewal_mode
                team.cost_budget_renewal_mode = _cost_budget_renewal_mode(cost_budget_renewal_mode)
                active_window = _active_team_cost_budget_window(team)
                if (
                    previous_renewal_mode != TeamCostBudgetRenewalMode.MONTHLY_UTC
                    and team.cost_budget_renewal_mode == TeamCostBudgetRenewalMode.MONTHLY_UTC
                    and active_window is not None
                ):
                    # A mode change must not retroactively discard spend from the
                    # current manual window.  Its first monthly renewal is the next
                    # UTC month boundary after this edit.
                    active_window.monthly_renewal_not_before = _next_utc_month_start(mutation_now)
            if set_max_cost_usd:
                previous_max_cost_usd = team.max_cost_usd
                team.max_cost_usd = _optional_cost_limit(max_cost_usd)
                if previous_max_cost_usd is None and team.max_cost_usd is not None:
                    _open_team_cost_budget_window(
                        team,
                        now=mutation_now,
                        kind=TeamCostBudgetWindowKind.CONFIGURED,
                        actor="operator:team-config",
                        reason="Cost budget configured.",
                    )
                elif previous_max_cost_usd is not None and team.max_cost_usd is None:
                    _close_active_team_cost_budget_window(team, ended_at=mutation_now)
                elif team.max_cost_usd is not None:
                    active_window = _active_team_cost_budget_window(team)
                    if active_window is not None:
                        active_window.max_cost_usd = team.max_cost_usd
            if set_cost_warning_ratio:
                team.cost_warning_ratio = _cost_warning_ratio(cost_warning_ratio)
            if escalate_to_governance is not None:
                team.escalate_to_governance = bool(escalate_to_governance)
            if archived is not None:
                team.archived = archived
                if archived:
                    for agent in state.agents.values():
                        if agent.team_id == team_id:
                            agent.status = AgentStatus.ARCHIVED
            team.updated_at = mutation_now
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def reset_team_cost_budget(
        self,
        *,
        team_id: str,
        reason: str,
        operator_actor_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_reason = _required_text(reason, "reason")
        normalized_actor = _required_text(operator_actor_id, "operator_actor_id")

        def _mutate(state: TeamState) -> TeamState:
            team = _require_present(state.teams, normalized_team_id, "team")
            if team.max_cost_usd is None:
                raise ValueError("team has no configured max_cost_usd to reset")
            now = utc_now()
            _advance_team_cost_budget_window(team, now=now)
            _open_team_cost_budget_window(
                team,
                now=now,
                kind=TeamCostBudgetWindowKind.MANUAL_RESET,
                actor=normalized_actor,
                reason=normalized_reason,
            )
            team.updated_at = now
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def advance_team_cost_budget_windows(
        self,
        *,
        now: datetime | None = None,
        current_state: TeamState | None = None,
    ) -> TeamState:
        effective_now = now or utc_now()
        state_snapshot = current_state or self._repository.read()
        if not any(
            _team_cost_budget_window_advance_due(team, now=effective_now)
            for team in state_snapshot.teams.values()
        ):
            return state_snapshot

        def _mutate(state: TeamState) -> TeamState:
            for team in state.teams.values():
                if _advance_team_cost_budget_window(team, now=effective_now):
                    team.updated_at = effective_now
            return state

        return self._repository.update(_mutate)

    def create_governance_group(
        self,
        *,
        governance_group_id: str | None,
        name: str,
        manager_agent_id: str,
        member_team_ids: list[str] | None = None,
        goals: list[GovernanceGoal | Mapping[str, Any]] | None = None,
        max_cost_usd: object | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_group_id = _optional_text(governance_group_id)
        normalized_name = _required_text(name, "name")
        normalized_manager_agent_id = _required_text(manager_agent_id, "manager_agent_id")
        normalized_member_team_ids = _unique_text_list(member_team_ids or [], "member_team_ids")
        goal_inputs = list(goals or [])

        def _mutate(state: TeamState) -> TeamState:
            effective_group_id = normalized_group_id or _generated_id(
                prefix="governance",
                labels=[normalized_name],
                existing_ids=state.governance_groups,
            )
            _require_absent(state.governance_groups, effective_group_id, "governance group")
            _require_present(state.agents, normalized_manager_agent_id, "governance manager")
            for team_id in normalized_member_team_ids:
                _require_present(state.teams, team_id, "member team")
            now = utc_now()
            state.governance_groups[effective_group_id] = GovernanceGroup(
                id=effective_group_id,
                name=normalized_name,
                manager_agent_id=normalized_manager_agent_id,
                member_team_ids=normalized_member_team_ids,
                goals=_governance_goals_with_generated_ids(
                    goal_inputs,
                    governance_group_id=effective_group_id,
                ),
                max_cost_usd=max_cost_usd,
                created_at=now,
                updated_at=now,
            )
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def update_governance_group(
        self,
        *,
        governance_group_id: str,
        name: str | None = None,
        manager_agent_id: str | None = None,
        member_team_ids: list[str] | None = None,
        set_member_team_ids: bool = False,
        goals: list[GovernanceGoal | Mapping[str, Any]] | None = None,
        max_cost_usd: object | None = None,
        set_max_cost_usd: bool = False,
        archived: bool | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_group_id = _required_text(governance_group_id, "governance_group_id")
        normalized_goals = (
            _governance_goals_with_generated_ids(
                list(goals),
                governance_group_id=normalized_group_id,
            )
            if goals is not None
            else None
        )

        def _mutate(state: TeamState) -> TeamState:
            group = _require_present(
                state.governance_groups,
                normalized_group_id,
                "governance group",
            )
            if name is not None:
                group.name = name
            if manager_agent_id is not None:
                _require_present(state.agents, manager_agent_id, "governance manager")
                group.manager_agent_id = manager_agent_id
            if archived is not None:
                group.archived = archived
            if set_member_team_ids:
                previous_member_team_ids = set(group.member_team_ids)
                normalized_member_team_ids = _unique_text_list(
                    member_team_ids or [],
                    "member_team_ids",
                )
                for team_id in normalized_member_team_ids:
                    _require_present(state.teams, team_id, "member team")
                group.member_team_ids = normalized_member_team_ids
                added_team_ids = set(normalized_member_team_ids) - previous_member_team_ids
                removed_team_ids = previous_member_team_ids - set(normalized_member_team_ids)
                membership_changed_at = utc_now()
                if removed_team_ids:
                    for item in state.governance_agenda.values():
                        if (
                            item.governance_group_id != group.id
                            or item.state == GovernanceAgendaItemState.HANDLED
                        ):
                            continue
                        item_team_id = _governance_agenda_effective_team_id(state, item)
                        if item_team_id in removed_team_ids:
                            item.state = GovernanceAgendaItemState.HANDLED
                            item.updated_at = membership_changed_at
                _record_governance_membership_change_agenda(
                    state,
                    group=group,
                    previous_member_team_ids=sorted(previous_member_team_ids),
                    added_team_ids=sorted(added_team_ids),
                    removed_team_ids=sorted(removed_team_ids),
                    now=membership_changed_at,
                )
            if normalized_goals is not None:
                group.goals = normalized_goals
            if set_max_cost_usd:
                group.max_cost_usd = _optional_cost_limit(max_cost_usd)
            group.updated_at = utc_now()
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def create_governance_agenda_item(
        self,
        *,
        agenda_item_id: str | None,
        governance_group_id: str,
        item_type: GovernanceAgendaItemType | str,
        title: str,
        summary: str,
        team_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_item_id = _optional_text(agenda_item_id)
        normalized_group_id = _required_text(governance_group_id, "governance_group_id")
        normalized_title = _required_text(title, "title")
        normalized_summary = _required_text(summary, "summary")
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        normalized_item_type = (
            item_type
            if isinstance(item_type, GovernanceAgendaItemType)
            else GovernanceAgendaItemType(_required_text(item_type, "item_type"))
        )
        normalized_priority = (
            priority
            if isinstance(priority, TeamInboxPriority)
            else TeamInboxPriority(_required_text(priority, "priority"))
        )
        normalized_payload = dict(payload or {})

        def _mutate(state: TeamState) -> TeamState:
            group = _require_present(
                state.governance_groups,
                normalized_group_id,
                "governance group",
            )
            effective_team_id = normalized_team_id
            if normalized_project_id is not None:
                project = _require_present(state.projects, normalized_project_id, "project")
                if effective_team_id is not None and project.team_id != effective_team_id:
                    raise ValueError(
                        f"project {normalized_project_id} does not belong to team "
                        f"{effective_team_id}"
                    )
                effective_team_id = effective_team_id or project.team_id
            if normalized_agent_id is not None:
                agent = _require_present(state.agents, normalized_agent_id, "agent")
                if effective_team_id is not None and agent.team_id != effective_team_id:
                    raise ValueError(
                        f"agent {normalized_agent_id} does not belong to team {effective_team_id}"
                    )
                effective_team_id = effective_team_id or agent.team_id
            if effective_team_id is not None:
                _require_present(state.teams, effective_team_id, "team")
                if effective_team_id not in group.member_team_ids:
                    raise ValueError(
                        f"team {effective_team_id} is not in governance group {group.id}"
                    )
            effective_item_id = normalized_item_id or _generated_id(
                prefix="governance-agenda",
                labels=[normalized_group_id, normalized_item_type.value, normalized_title],
                existing_ids=state.governance_agenda,
            )
            _require_absent(state.governance_agenda, effective_item_id, "governance agenda item")
            now = utc_now()
            state.governance_agenda[effective_item_id] = GovernanceAgendaItem(
                id=effective_item_id,
                governance_group_id=normalized_group_id,
                item_type=normalized_item_type,
                title=normalized_title,
                summary=normalized_summary,
                team_id=effective_team_id,
                project_id=normalized_project_id,
                agent_id=normalized_agent_id,
                payload=normalized_payload,
                priority=normalized_priority,
                created_at=now,
                updated_at=now,
            )
            runtime = state.governance_runtimes.get(normalized_group_id)
            if runtime is not None:
                runtime.next_wake_at = None
                runtime.updated_at = now
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def mark_governance_agenda_handled(
        self,
        *,
        agenda_item_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_item_id = _required_text(agenda_item_id, "agenda_item_id")

        def _mutate(state: TeamState) -> TeamState:
            item = _require_present(
                state.governance_agenda,
                normalized_item_id,
                "governance agenda item",
            )
            item.state = GovernanceAgendaItemState.HANDLED
            item.updated_at = utc_now()
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def create_project(
        self,
        *,
        project_id: str | None,
        team_id: str,
        name: str,
        lead_agent_id: str | None,
        lead_session_id: str | None,
        lead_display_name: str | None = None,
        goals: list[ProjectGoal | Mapping[str, Any]] | None = None,
        directory_path: str | None = None,
        git: ProjectGitConfig | None = None,
        release_policy: ProjectReleasePolicy | None = None,
        lead_role_id: str | None = None,
        lead_role_name: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        _validate_resolved_human_release_policy(release_policy)
        normalized_project_id = _optional_text(project_id)
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_name = _required_text(name, "name")
        normalized_lead_agent_id = _optional_text(lead_agent_id)
        normalized_lead_session_id = _optional_text(lead_session_id)
        normalized_lead_display_name = _optional_text(lead_display_name)
        normalized_lead_role_id = _optional_text(lead_role_id)
        normalized_lead_role_name = _optional_text(lead_role_name) or "Project Lead"
        normalized_directory_path = DirectoryPath(
            segments=(
                _optional_text(directory_path)
                or (git.directory_path if git is not None else "root")
            )
        ).as_posix()
        if git is not None and git.directory_path != normalized_directory_path:
            raise ValueError(
                "project git directory_path must match the provider-neutral project directory_path"
            )
        goal_inputs = list(goals or [])
        current_state = self._repository.read()
        effective_project_id = normalized_project_id or _generated_id(
            prefix="project",
            labels=[normalized_team_id, normalized_name],
            existing_ids=current_state.projects,
        )

        def _mutate(state: TeamState) -> TeamState:
            team = _require_present(state.teams, normalized_team_id, "team")
            _require_absent(state.projects, effective_project_id, "project")
            role_base_id = _default_role_id(
                team_id=normalized_team_id,
                project_id=effective_project_id,
                role_key="project_lead",
            )
            role_id = normalized_lead_role_id or _unique_id_from_base(
                role_base_id,
                existing_ids=state.roles,
            )
            _require_absent(state.roles, role_id, "role")
            effective_lead_agent_id = normalized_lead_agent_id or _generated_id(
                prefix="agent",
                labels=[
                    effective_project_id,
                    normalized_lead_display_name or normalized_lead_role_name,
                ],
                existing_ids=state.agents,
            )
            existing_lead = state.agents.get(effective_lead_agent_id)
            if existing_lead is not None:
                if existing_lead.team_id != normalized_team_id:
                    raise ValueError(
                        f"agent {effective_lead_agent_id} belongs to team {existing_lead.team_id}"
                    )
            elif normalized_lead_agent_id and normalized_lead_session_id is None:
                raise ValueError(f"agent {effective_lead_agent_id} does not exist")
            elif normalized_lead_session_id is None:
                raise ValueError(
                    "lead_session_id is required when creating a new project lead agent"
                )
            project_goals = _project_goals_with_generated_ids(
                goal_inputs,
                project_id=effective_project_id,
            )
            now = utc_now()
            role = TeamRole(
                id=role_id,
                team_id=normalized_team_id,
                project_id=effective_project_id,
                name=normalized_lead_role_name,
                permissions=[RolePermission.CREATE_ROOT_TASK],
                created_at=now,
                updated_at=now,
            )
            state.roles[role.id] = role
            if existing_lead is not None:
                lead = existing_lead
            else:
                lead = PersistentAgent(
                    id=effective_lead_agent_id,
                    team_id=normalized_team_id,
                    display_name=normalized_lead_display_name,
                    role_id=role.id,
                    session_id=normalized_lead_session_id,
                    reports_to_agent_id=team.manager_agent_id,
                    ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                )
                state.agents[lead.id] = lead
                manager = state.agents[team.manager_agent_id]
                manager.subordinate_agent_ids = _append_unique(
                    manager.subordinate_agent_ids,
                    lead.id,
                )
            assignment_id = _project_assignment_id(
                effective_project_id,
                lead.id,
                AgentProjectAssignmentKind.PROJECT_LEAD,
                existing_ids=state.project_assignments,
            )
            state.project_assignments[assignment_id] = AgentProjectAssignment(
                id=assignment_id,
                team_id=normalized_team_id,
                project_id=effective_project_id,
                agent_id=lead.id,
                role_id=role.id,
                kind=AgentProjectAssignmentKind.PROJECT_LEAD,
                created_at=now,
                updated_at=now,
            )
            state.projects[effective_project_id] = Project(
                id=effective_project_id,
                team_id=normalized_team_id,
                name=normalized_name,
                lead_agent_id=lead.id,
                directory_path=normalized_directory_path,
                goals=project_goals,
                git=git,
                release_policy=release_policy,
                created_at=now,
                updated_at=now,
            )
            team.project_ids = _append_unique(team.project_ids, effective_project_id)
            team.updated_at = now
            return state

        if git is None:
            return self._repository.update(_mutate, expected_revision=expected_revision)
        return self._update_with_project_repository_binding(
            _mutate,
            project_id=effective_project_id,
            next_managed_repo_id=git.managed_repo_id,
            expected_revision=expected_revision,
        )

    def update_project(
        self,
        *,
        project_id: str,
        name: str | None = None,
        directory_path: str | None = None,
        set_directory_path: bool = False,
        git: ProjectGitConfig | None = None,
        set_git: bool = False,
        release_policy: ProjectReleasePolicy | None = None,
        set_release_policy: bool = False,
        goals: list[ProjectGoal] | None = None,
        status: ProjectStatus | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        if set_release_policy:
            _validate_resolved_human_release_policy(release_policy)
        normalized_directory_path = (
            DirectoryPath(segments=_required_text(directory_path, "directory_path")).as_posix()
            if set_directory_path
            else None
        )
        next_managed_repo_id = git.managed_repo_id if set_git and git is not None else None

        def _mutate(state: TeamState) -> TeamState:
            project = _require_present(state.projects, project_id, "project")
            if name is not None:
                project.name = name
            next_directory_path = project.directory_path
            if normalized_directory_path is not None:
                next_directory_path = normalized_directory_path
            elif set_git and git is not None:
                next_directory_path = git.directory_path
            next_git = git if set_git else project.git
            if next_git is not None and next_git.directory_path != next_directory_path:
                raise ValueError(
                    "project git directory_path must match the provider-neutral "
                    "project directory_path"
                )
            current_managed_repo_id = (
                project.git.managed_repo_id if project.git is not None else None
            )
            next_managed_repo_id_for_state = (
                next_git.managed_repo_id if next_git is not None else None
            )
            if (
                next_directory_path != project.directory_path
                or next_managed_repo_id_for_state != current_managed_repo_id
            ):
                unfrozen_context_ids = _unfrozen_project_worktree_context_ids(
                    state,
                    project=project,
                )
                if unfrozen_context_ids:
                    raise ValueError(
                        f"project {project.id} repository scope cannot change while Git "
                        "work contexts lack frozen cleanup scope: "
                        f"{', '.join(unfrozen_context_ids)}; resolve or reassign the "
                        "tasks and run worktree cleanup enqueue first"
                    )
            project.directory_path = next_directory_path
            if set_git:
                project.git = git
            if set_release_policy:
                project.release_policy = release_policy
            if goals is not None:
                project.goals = goals
            if status is not None:
                project.status = status
            project.updated_at = utc_now()
            return state

        if not set_git:
            return self._repository.update(_mutate, expected_revision=expected_revision)
        return self._update_with_project_repository_binding(
            _mutate,
            project_id=project_id,
            next_managed_repo_id=next_managed_repo_id,
            expected_revision=expected_revision,
        )

    def transfer_project_lead(
        self,
        *,
        project_id: str,
        lead_agent_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        """Transfer a project's lead overlay to an existing active team agent.

        The receiving agent retains their existing role.  Their project assignment
        is promoted to ``PROJECT_LEAD`` while any previous lead remains a project
        member, allowing a later, separately validated team removal or archival.
        """

        normalized_project_id = _required_text(project_id, "project_id")
        normalized_lead_agent_id = _required_text(lead_agent_id, "lead_agent_id")

        def _mutate(state: TeamState) -> TeamState:
            project = _require_present(state.projects, normalized_project_id, "project")
            next_lead = _require_present(
                state.agents,
                normalized_lead_agent_id,
                "project lead agent",
            )
            if next_lead.team_id != project.team_id:
                raise ValueError(
                    f"agent {next_lead.id} does not belong to project team {project.team_id}"
                )
            if next_lead.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {next_lead.id} is archived")
            if next_lead.role_id is None:
                raise ValueError(f"agent {next_lead.id} has no Team role")
            role = _require_present(state.roles, next_lead.role_id, "project lead role")
            _validate_role_assignment(
                role,
                team_id=project.team_id,
                project_ids=sorted(set(agent_project_ids(state, next_lead.id)).union({project.id})),
            )

            now = utc_now()
            next_lead_assignments = sorted(
                (
                    assignment
                    for assignment in state.project_assignments.values()
                    if assignment.project_id == project.id and assignment.agent_id == next_lead.id
                ),
                key=lambda assignment: assignment.id,
            )
            retained_assignment = next_lead_assignments[0] if next_lead_assignments else None
            if retained_assignment is None:
                assignment_id = _project_assignment_id(
                    project.id,
                    next_lead.id,
                    AgentProjectAssignmentKind.PROJECT_LEAD,
                    existing_ids=state.project_assignments,
                )
                retained_assignment = AgentProjectAssignment(
                    id=assignment_id,
                    team_id=project.team_id,
                    project_id=project.id,
                    agent_id=next_lead.id,
                    role_id=next_lead.role_id,
                    kind=AgentProjectAssignmentKind.PROJECT_LEAD,
                    created_at=now,
                    updated_at=now,
                )
                state.project_assignments[assignment_id] = retained_assignment
            else:
                for duplicate in next_lead_assignments[1:]:
                    state.project_assignments.pop(duplicate.id, None)
                retained_assignment.team_id = project.team_id
                retained_assignment.role_id = next_lead.role_id
                retained_assignment.kind = AgentProjectAssignmentKind.PROJECT_LEAD
                retained_assignment.updated_at = now

            for assignment in state.project_assignments.values():
                if (
                    assignment.id != retained_assignment.id
                    and assignment.project_id == project.id
                    and assignment.kind == AgentProjectAssignmentKind.PROJECT_LEAD
                ):
                    assignment.kind = AgentProjectAssignmentKind.PROJECT_MEMBER
                    assignment.updated_at = now

            project.lead_agent_id = next_lead.id
            project.updated_at = now
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def create_git_work_context_with_id(
        self,
        *,
        context_id: str | None,
        agent_id: str,
        task_id: str,
        agent_branch: str,
        base_branch: str | None = None,
        base_commit: str | None = None,
        commit_sha: str | None = None,
        dispatch_id: str | None = None,
        worktree_path: str | None = None,
        expected_preparation_generation: str | None = None,
        preparation_attempt_id: str | None = None,
        dependency_git_sources: Sequence[Mapping[str, object] | DependencyGitSource] | None = None,
        dependency_composition_recovery_id: str | None = None,
        manual_dependency_git_sources: Sequence[Mapping[str, object] | DependencyGitSource]
        | None = None,
        dependency_composition_manifest: Mapping[str, object]
        | DependencyCompositionManifest
        | None = None,
        resource_provenance_validator: Callable[[TeamState], None] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, str]:
        normalized_context_id = _optional_text(context_id)
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_agent_branch = validate_branch_name(_required_text(agent_branch, "agent_branch"))
        normalized_base_branch = _optional_text(base_branch)
        if normalized_base_branch is not None:
            normalized_base_branch = validate_branch_name(normalized_base_branch)
        normalized_base_commit = _optional_text(base_commit)
        normalized_commit_sha = _optional_text(commit_sha)
        normalized_dispatch_id = _optional_text(dispatch_id)
        normalized_worktree_path = _optional_text(worktree_path)
        normalized_expected_preparation_generation = _optional_text(expected_preparation_generation)
        normalized_preparation_attempt_id = _optional_text(preparation_attempt_id)
        normalized_dependency_git_sources = [
            source
            if isinstance(source, DependencyGitSource)
            else DependencyGitSource.model_validate(source)
            for source in dependency_git_sources or []
        ]
        normalized_dependency_composition_recovery_id = _optional_text(
            dependency_composition_recovery_id
        )
        normalized_manual_dependency_git_sources = [
            source
            if isinstance(source, DependencyGitSource)
            else DependencyGitSource.model_validate(source)
            for source in manual_dependency_git_sources or []
        ]
        normalized_dependency_composition_manifest = (
            dependency_composition_manifest
            if isinstance(dependency_composition_manifest, DependencyCompositionManifest)
            else DependencyCompositionManifest.model_validate(dependency_composition_manifest)
            if dependency_composition_manifest is not None
            else None
        )
        created_context_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal created_context_id
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            task = _require_present(state.tasks, normalized_task_id, "task")
            if task.state not in _NON_TERMINAL_TASK_STATES:
                raise ValueError(
                    f"task {task.id} is {task.state.value} and cannot have an active "
                    "Git work context"
                )
            if task.owner_agent_id != agent.id:
                raise ValueError(f"agent {agent.id} cannot own Git work context for task {task.id}")
            if agent.team_id is None:
                raise ValueError(f"agent {agent.id} is not assigned to a team")
            project = _require_present(state.projects, task.project_id, "project")
            if project.team_id != agent.team_id:
                raise ValueError(
                    f"task {task.id} project {project.id} does not belong to "
                    f"agent team {agent.team_id}"
                )
            if resource_provenance_validator is not None:
                resource_provenance_validator(state)
            effective_base_branch = normalized_base_branch
            if task.git_base_branch is None:
                task.git_base_branch = _resolve_new_task_git_base_branch(
                    state,
                    project_id=task.project_id,
                    explicit_git_base_branch=effective_base_branch,
                    parent_task_id=task.parent_task_id,
                    case_id=task.case_id,
                    git_default_branch_resolver=(self._resolve_managed_git_default_branch),
                )
            if effective_base_branch is None:
                effective_base_branch = task.git_base_branch
            if task.git_base_branch != effective_base_branch:
                raise ValueError(
                    f"Git work context base {effective_base_branch!r} does not match "
                    f"task {task.id} snapshot {task.git_base_branch!r}"
                )
            current_preparation_generation = task_git_work_context_generation(
                state,
                task.id,
            )
            if (
                normalized_expected_preparation_generation is not None
                and normalized_expected_preparation_generation != current_preparation_generation
            ):
                raise ValueError(
                    f"task {task.id} preparation generation changed during Git worktree preparation"
                )
            preparation_attempt = (
                state.git_preparation_attempts.get(normalized_preparation_attempt_id)
                if normalized_preparation_attempt_id is not None
                else None
            )
            if normalized_preparation_attempt_id is not None:
                if preparation_attempt is None:
                    raise ValueError(
                        f"Git preparation attempt {normalized_preparation_attempt_id} is missing"
                    )
                if preparation_attempt.state not in {
                    TeamGitPreparationAttemptState.PREPARED,
                    TeamGitPreparationAttemptState.COMMITTED,
                }:
                    raise ValueError(
                        f"Git preparation attempt {preparation_attempt.id} is "
                        f"{preparation_attempt.state.value}, not prepared"
                    )
                if (
                    preparation_attempt.agent_id != agent.id
                    or preparation_attempt.task_id != task.id
                    or preparation_attempt.branch != normalized_agent_branch
                    or preparation_attempt.worktree_path != normalized_worktree_path
                    or preparation_attempt.preparation_generation != current_preparation_generation
                ):
                    raise ValueError(
                        f"Git preparation attempt {preparation_attempt.id} does not match "
                        "the work context provenance"
                    )
            if task.git_base_commit_sha is not None:
                if normalized_base_commit is None:
                    raise ValueError(
                        f"Git work context for pinned task {task.id} requires "
                        f"base commit {task.git_base_commit_sha}"
                    )
                if normalized_base_commit.casefold() != task.git_base_commit_sha:
                    raise ValueError(
                        f"Git work context base commit {normalized_base_commit} does "
                        f"not match task {task.id} pin {task.git_base_commit_sha}"
                    )
            effective_context_id = normalized_context_id or _generated_id(
                prefix="gitctx",
                labels=[normalized_agent_id, normalized_task_id, normalized_agent_branch],
                existing_ids=state.git_work_contexts,
            )
            existing_context = state.git_work_contexts.get(effective_context_id)
            if existing_context is not None:
                expected_context = GitWorkContext(
                    id=effective_context_id,
                    agent_id=agent.id,
                    task_id=task.id,
                    dispatch_id=normalized_dispatch_id,
                    worktree_path=normalized_worktree_path,
                    dependency_git_sources=normalized_dependency_git_sources,
                    dependency_composition_recovery_id=(
                        normalized_dependency_composition_recovery_id
                    ),
                    manual_dependency_git_sources=(normalized_manual_dependency_git_sources),
                    dependency_composition_manifest=(normalized_dependency_composition_manifest),
                    preparation_generation=current_preparation_generation,
                    base_branch=effective_base_branch,
                    base_commit=normalized_base_commit,
                    agent_branch=normalized_agent_branch,
                    commit_sha=normalized_commit_sha,
                    created_at=existing_context.created_at,
                    updated_at=existing_context.updated_at,
                )
                if existing_context != expected_context:
                    raise ValueError(
                        f"Git work context {effective_context_id} already exists with "
                        "different resource provenance"
                    )
                created_context_id = effective_context_id
                if preparation_attempt is not None:
                    preparation_attempt.state = TeamGitPreparationAttemptState.COMMITTED
                    preparation_attempt.git_work_context_id = effective_context_id
                    preparation_attempt.committed_at = preparation_attempt.committed_at or utc_now()
                    preparation_attempt.updated_at = utc_now()
                return state
            created_context_id = effective_context_id
            now = utc_now()
            state.git_work_contexts[effective_context_id] = GitWorkContext(
                id=effective_context_id,
                agent_id=agent.id,
                task_id=task.id,
                dispatch_id=normalized_dispatch_id,
                worktree_path=normalized_worktree_path,
                dependency_git_sources=normalized_dependency_git_sources,
                dependency_composition_recovery_id=(normalized_dependency_composition_recovery_id),
                manual_dependency_git_sources=(normalized_manual_dependency_git_sources),
                dependency_composition_manifest=(normalized_dependency_composition_manifest),
                preparation_generation=current_preparation_generation,
                base_branch=effective_base_branch,
                base_commit=normalized_base_commit,
                agent_branch=normalized_agent_branch,
                commit_sha=normalized_commit_sha,
                created_at=now,
                updated_at=now,
            )
            if preparation_attempt is not None:
                preparation_attempt.state = TeamGitPreparationAttemptState.COMMITTED
                preparation_attempt.git_work_context_id = effective_context_id
                preparation_attempt.committed_at = now
                preparation_attempt.updated_at = now
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if created_context_id is None:
            raise RuntimeError("created Git work context could not be resolved")
        return state, created_context_id

    def create_role(
        self,
        *,
        role_id: str | None,
        team_id: str,
        name: str,
        project_id: str | None = None,
        description: str | None = None,
        permissions: list[RolePermission | str] | None = None,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_role_id = _optional_text(role_id)
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_name = _required_text(name, "name")
        normalized_description = _optional_text(description) or ""
        normalized_project_id = _optional_text(project_id)

        def _mutate(state: TeamState) -> TeamState:
            _require_present(state.teams, normalized_team_id, "team")
            if normalized_project_id is not None:
                project = _require_present(state.projects, normalized_project_id, "project")
                if project.team_id != normalized_team_id:
                    raise ValueError(
                        f"project {normalized_project_id} does not belong to team {normalized_team_id}"
                    )
            effective_role_id = normalized_role_id or _generated_id(
                prefix="role",
                labels=[normalized_project_id or normalized_team_id, normalized_name],
                existing_ids=state.roles,
            )
            _require_absent(state.roles, effective_role_id, "role")
            now = utc_now()
            role = TeamRole(
                id=effective_role_id,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                name=normalized_name,
                description=normalized_description,
                permissions=_normalize_role_permissions(permissions),
                ephemeral_policy=ephemeral_policy or EphemeralAgentPolicy(),
                created_at=now,
                updated_at=now,
            )
            state.roles[role.id] = role
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def update_role(
        self,
        *,
        role_id: str,
        name: str | None = None,
        project_id: str | None = None,
        set_project_id: bool = False,
        description: str | None = None,
        set_description: bool = False,
        permissions: list[RolePermission | str] | None = None,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        archived: bool | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            role = _require_present(state.roles, role_id, "role")
            if name is not None:
                role.name = name
            if set_project_id:
                normalized_project_id = _optional_text(project_id)
                if normalized_project_id is not None:
                    project = _require_present(state.projects, normalized_project_id, "project")
                    if project.team_id != role.team_id:
                        raise ValueError(
                            f"project {normalized_project_id} does not belong to team {role.team_id}"
                        )
                role.project_id = normalized_project_id
            if set_description:
                role.description = description or ""
            if permissions is not None:
                role.permissions = _normalize_role_permissions(permissions)
            if ephemeral_policy is not None:
                role.ephemeral_policy = ephemeral_policy
            if archived is not None:
                role.archived = archived
            role.updated_at = utc_now()
            for agent in state.agents.values():
                if agent.role_id == role.id:
                    _validate_role_assignment(
                        role,
                        team_id=agent.team_id,
                        project_ids=agent_project_ids(state, agent.id),
                    )
            for profile in state.review_profiles.values():
                if role.id in profile.applicable_role_ids:
                    _validate_role_filter_assignment(role, profile=profile)
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def register_agent(
        self,
        *,
        agent_id: str | None,
        session_id: str,
        team_id: str | None = None,
        project_id: str | None = None,
        role_id: str | None = None,
        reports_to_agent_id: str | None = None,
        display_name: str | None = None,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _optional_text(agent_id)
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_role_id = _optional_text(role_id)
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_reports_to_agent_id = _optional_text(reports_to_agent_id)
        normalized_display_name = _optional_text(display_name)

        def _mutate(state: TeamState) -> TeamState:
            generated_agent_labels = (
                [
                    normalized_project_id or normalized_team_id,
                    normalized_display_name or normalized_role_id or normalized_session_id,
                ]
                if normalized_team_id is not None
                else [normalized_display_name or normalized_session_id]
            )
            effective_agent_id = normalized_agent_id or _generated_id(
                prefix="agent",
                labels=generated_agent_labels,
                existing_ids=state.agents,
            )
            _require_absent(state.agents, effective_agent_id, "agent")
            role: TeamRole | None = None
            if normalized_team_id is None:
                if normalized_project_id is not None:
                    raise ValueError("project_id requires team_id")
                if normalized_role_id is not None:
                    raise ValueError("role_id requires team_id")
                if normalized_reports_to_agent_id is not None:
                    raise ValueError("reports_to_agent_id requires team_id")
            else:
                _require_present(state.teams, normalized_team_id, "team")
                if normalized_project_id is not None:
                    project = _require_present(state.projects, normalized_project_id, "project")
                    if project.team_id != normalized_team_id:
                        raise ValueError(
                            f"project {normalized_project_id} does not belong to team "
                            f"{normalized_team_id}"
                        )
                if normalized_role_id is None:
                    raise ValueError("role_id is required when registering an agent to a team")
                role = _require_present(state.roles, normalized_role_id, "role")
                _validate_role_assignment(
                    role,
                    team_id=normalized_team_id,
                    project_ids=[normalized_project_id]
                    if normalized_project_id is not None
                    else [],
                )
                if normalized_reports_to_agent_id is not None:
                    superior = _require_present(
                        state.agents,
                        normalized_reports_to_agent_id,
                        "superior agent",
                    )
                    if superior.team_id != normalized_team_id:
                        raise ValueError(
                            f"superior agent {normalized_reports_to_agent_id} is not in team "
                            f"{normalized_team_id}"
                        )
            agent = PersistentAgent(
                id=effective_agent_id,
                team_id=normalized_team_id,
                display_name=normalized_display_name,
                role_id=normalized_role_id,
                session_id=normalized_session_id,
                reports_to_agent_id=normalized_reports_to_agent_id,
                ephemeral_policy=(
                    ephemeral_policy.model_copy(deep=True)
                    if ephemeral_policy is not None
                    else (
                        role.ephemeral_policy.model_copy(deep=True)
                        if role is not None
                        else EphemeralAgentPolicy()
                    )
                ),
            )
            state.agents[agent.id] = agent
            if normalized_project_id is not None and normalized_team_id is not None:
                assignment_id = _project_assignment_id(
                    normalized_project_id,
                    agent.id,
                    AgentProjectAssignmentKind.PROJECT_MEMBER,
                    existing_ids=state.project_assignments,
                )
                state.project_assignments[assignment_id] = AgentProjectAssignment(
                    id=assignment_id,
                    team_id=normalized_team_id,
                    project_id=normalized_project_id,
                    agent_id=agent.id,
                    role_id=normalized_role_id,
                    kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
                )
            if normalized_reports_to_agent_id and normalized_reports_to_agent_id in state.agents:
                superior = state.agents[normalized_reports_to_agent_id]
                superior.subordinate_agent_ids = _append_unique(
                    superior.subordinate_agent_ids,
                    agent.id,
                )
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def update_agent(
        self,
        *,
        agent_id: str,
        team_id: str | None = None,
        set_team_id: bool = False,
        project_id: str | None = None,
        set_project_id: bool = False,
        role_id: str | None = None,
        set_role_id: bool = False,
        session_id: str | None = None,
        reports_to_agent_id: str | None = None,
        set_reports_to_agent_id: bool = False,
        display_name: str | None = None,
        set_display_name: bool = False,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        status: AgentStatus | None = None,
        voice_profile: AgentVoiceProfile | None = None,
        set_voice_profile: bool = False,
        context_clearing_policy: AgentContextClearingPolicy | str | None = None,
        set_context_clearing_policy: bool = False,
        context_clearing_idle_grace_seconds: int | None = None,
        set_context_clearing_idle_grace_seconds: bool = False,
        auto_context_clearing_enabled: bool | None = None,
        set_auto_context_clearing_enabled: bool = False,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_role_id = _optional_text(role_id)
        normalized_reports_to_agent_id = _optional_text(reports_to_agent_id)
        normalized_context_clearing_policy = _resolved_context_clearing_policy(
            context_clearing_policy=context_clearing_policy,
            set_context_clearing_policy=set_context_clearing_policy,
            auto_context_clearing_enabled=auto_context_clearing_enabled,
            set_auto_context_clearing_enabled=set_auto_context_clearing_enabled,
        )
        normalized_context_clearing_idle_grace_seconds = (
            _context_clearing_idle_grace_seconds_from_value(context_clearing_idle_grace_seconds)
            if set_context_clearing_idle_grace_seconds
            else None
        )
        idle_rotation_result: dict[str, Any] = {"events": []}

        def _mutate(state: TeamState) -> TeamState:
            now = utc_now()
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            detached_from_team = False
            if set_team_id:
                if normalized_team_id is None:
                    blockers = _agent_team_removal_blockers(state, agent)
                    if blockers:
                        raise ValueError(
                            f"agent {agent.id} cannot be removed from team: " + "; ".join(blockers)
                        )
                    old_superior_id = agent.reports_to_agent_id
                    if old_superior_id and old_superior_id in state.agents:
                        old_superior = state.agents[old_superior_id]
                        old_superior.subordinate_agent_ids = _remove_value(
                            old_superior.subordinate_agent_ids,
                            agent.id,
                        )
                    for assignment_id in [
                        assignment.id
                        for assignment in state.project_assignments.values()
                        if assignment.agent_id == agent.id
                    ]:
                        state.project_assignments.pop(assignment_id, None)
                    agent.team_id = None
                    agent.role_id = None
                    agent.reports_to_agent_id = None
                    agent.subordinate_agent_ids = []
                    detached_from_team = True
                else:
                    _require_present(state.teams, normalized_team_id, "team")
                    agent.team_id = normalized_team_id
            if set_project_id and not detached_from_team:
                if agent.team_id is None:
                    raise ValueError("project_id cannot be set for a teamless agent")
                for assignment_id in [
                    assignment.id
                    for assignment in state.project_assignments.values()
                    if assignment.agent_id == agent.id
                    and assignment.kind == AgentProjectAssignmentKind.PROJECT_MEMBER
                ]:
                    state.project_assignments.pop(assignment_id, None)
                if normalized_project_id is not None:
                    project = _require_present(state.projects, normalized_project_id, "project")
                    if project.team_id != agent.team_id:
                        raise ValueError(
                            f"project {normalized_project_id} does not belong to team {agent.team_id}"
                        )
                    assignment_id = _project_assignment_id(
                        normalized_project_id,
                        agent.id,
                        AgentProjectAssignmentKind.PROJECT_MEMBER,
                        existing_ids=state.project_assignments,
                    )
                    state.project_assignments[assignment_id] = AgentProjectAssignment(
                        id=assignment_id,
                        team_id=agent.team_id,
                        project_id=normalized_project_id,
                        agent_id=agent.id,
                        role_id=agent.role_id,
                        kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
                    )
            if set_role_id and not detached_from_team:
                if normalized_role_id is not None:
                    if agent.team_id is None:
                        raise ValueError("role_id cannot be set for a teamless agent")
                    role = _require_present(state.roles, normalized_role_id, "role")
                    _validate_role_assignment(
                        role,
                        team_id=agent.team_id,
                        project_ids=agent_project_ids(state, agent.id),
                    )
                agent.role_id = normalized_role_id
                if role is not None and role.project_id is not None:
                    for assignment in state.project_assignments.values():
                        if (
                            assignment.agent_id == agent.id
                            and assignment.project_id == role.project_id
                        ):
                            assignment.role_id = role.id
                            assignment.updated_at = now
            elif role_id is not None and not detached_from_team:
                role = _require_present(state.roles, normalized_role_id or role_id, "role")
                _validate_role_assignment(
                    role,
                    team_id=agent.team_id,
                    project_ids=agent_project_ids(state, agent.id),
                )
                agent.role_id = role.id
                if role.project_id is not None:
                    for assignment in state.project_assignments.values():
                        if (
                            assignment.agent_id == agent.id
                            and assignment.project_id == role.project_id
                        ):
                            assignment.role_id = role.id
                            assignment.updated_at = now
            if session_id is not None:
                session_changed = session_id != agent.session_id
                agent.session_id = session_id
                if session_changed:
                    runtime = state.agent_runtimes.setdefault(
                        agent.id,
                        AgentRuntime(agent_id=agent.id),
                    )
                    runtime.last_context_rotated_at = now
                    runtime.work_lineage_thread_ids = {}
                    runtime.updated_at = now
            if set_display_name:
                agent.display_name = display_name
            if ephemeral_policy is not None:
                agent.ephemeral_policy = ephemeral_policy
            if status is not None:
                agent.status = status
            if set_voice_profile:
                agent.voice_profile = (
                    voice_profile
                    if voice_profile is not None and voice_profile.configured
                    else None
                )
            if (
                normalized_context_clearing_policy is not None
                or normalized_context_clearing_idle_grace_seconds is not None
            ):
                runtime = state.agent_runtimes.setdefault(
                    agent.id,
                    AgentRuntime(agent_id=agent.id),
                )
                if normalized_context_clearing_idle_grace_seconds is not None:
                    _set_context_clearing_idle_grace_seconds(
                        state,
                        agent=agent,
                        runtime=runtime,
                        grace_seconds=normalized_context_clearing_idle_grace_seconds,
                        now=now,
                        result=idle_rotation_result,
                    )
            if normalized_context_clearing_policy is not None:
                _set_context_clearing_policy(
                    state,
                    agent=agent,
                    runtime=runtime,
                    policy=normalized_context_clearing_policy,
                    now=now,
                    result=idle_rotation_result,
                )
            if set_reports_to_agent_id and not detached_from_team:
                old_superior_id = agent.reports_to_agent_id
                if normalized_reports_to_agent_id is not None:
                    if agent.team_id is None:
                        raise ValueError("reports_to_agent_id cannot be set for a teamless agent")
                    superior = _require_present(
                        state.agents, normalized_reports_to_agent_id, "superior agent"
                    )
                    if superior.team_id != agent.team_id:
                        raise ValueError(
                            f"superior agent {normalized_reports_to_agent_id} is not in team {agent.team_id}"
                        )
                if old_superior_id and old_superior_id in state.agents:
                    old_superior = state.agents[old_superior_id]
                    old_superior.subordinate_agent_ids = _remove_value(
                        old_superior.subordinate_agent_ids, agent.id
                    )
                agent.reports_to_agent_id = normalized_reports_to_agent_id
                if (
                    normalized_reports_to_agent_id
                    and normalized_reports_to_agent_id in state.agents
                ):
                    superior = state.agents[normalized_reports_to_agent_id]
                    superior.subordinate_agent_ids = _append_unique(
                        superior.subordinate_agent_ids, agent.id
                    )
            if agent.team_id is None:
                if agent.role_id is not None:
                    raise ValueError("teamless agent cannot have a team-scoped role")
                if agent.reports_to_agent_id is not None:
                    raise ValueError("teamless agent cannot report to another agent")
                if agent.subordinate_agent_ids:
                    raise ValueError("teamless agent cannot have subordinate agents")
            else:
                if agent.role_id is None:
                    raise ValueError("role_id is required when assigning an agent to a team")
                role = _require_present(state.roles, agent.role_id, "role")
                _validate_role_assignment(
                    role,
                    team_id=agent.team_id,
                    project_ids=agent_project_ids(state, agent.id),
                )
            return state

        return self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(
                idle_rotation_result.get("events") or []
            ),
            expected_revision=expected_revision,
        )

    def set_agent_avatar(
        self,
        *,
        agent_id: str,
        avatar: AgentAvatar,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")

        def _mutate(state: TeamState) -> TeamState:
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            agent.avatar = avatar
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def clear_agent_avatar(
        self,
        *,
        agent_id: str,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_agent_id = _required_text(agent_id, "agent_id")

        def _mutate(state: TeamState) -> TeamState:
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            agent.avatar = None
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def create_external_source_evidence_snapshot(
        self,
        *,
        snapshot_id: str | None,
        team_id: str,
        project_id: str | None,
        provider_type: str,
        source_type: str,
        external_identifier: str,
        external_version: str,
        fetched_at: datetime,
        expires_at: datetime | None,
        freshness_status: ExternalSourceEvidenceFreshnessStatus | str,
        access_classification: str,
        title: str | None = None,
        body: Mapping[str, Any] | ExternalSourceEvidenceBody | None = None,
        acceptance_criteria: Sequence[
            Mapping[str, Any] | ExternalSourceEvidenceAcceptanceCriterion
        ] = (),
        discussion: Sequence[Mapping[str, Any] | ExternalSourceEvidenceDiscussionEntry] = (),
        links: Sequence[Mapping[str, Any] | ExternalSourceEvidenceLink] = (),
        metadata: Mapping[str, Any] | None = None,
        content_digest: str | None = None,
        case_ids: Sequence[str] = (),
        task_ids: Sequence[str] = (),
        created_by_actor: str,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, ExternalSourceEvidenceSnapshot]:
        normalized_snapshot_id = _optional_text(snapshot_id)
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_project_id = _optional_text(project_id)
        normalized_provider_type = _required_text(provider_type, "provider_type")
        normalized_source_type = _required_text(source_type, "source_type")
        normalized_external_identifier = _required_text(
            external_identifier,
            "external_identifier",
        )
        normalized_external_version = _required_text(external_version, "external_version")
        normalized_fetched_at = _evidence_utc_datetime(fetched_at, "fetched_at")
        normalized_expires_at = (
            _evidence_utc_datetime(expires_at, "expires_at") if expires_at is not None else None
        )
        normalized_freshness_status = (
            freshness_status
            if isinstance(freshness_status, ExternalSourceEvidenceFreshnessStatus)
            else ExternalSourceEvidenceFreshnessStatus(str(freshness_status))
        )
        normalized_access_classification = _required_text(
            access_classification,
            "access_classification",
        )
        normalized_title = _optional_text(title)
        normalized_body = (
            body
            if isinstance(body, ExternalSourceEvidenceBody)
            else ExternalSourceEvidenceBody.model_validate(body)
            if body is not None
            else None
        )
        normalized_acceptance_criteria = [
            item
            if isinstance(item, ExternalSourceEvidenceAcceptanceCriterion)
            else ExternalSourceEvidenceAcceptanceCriterion.model_validate(item)
            for item in acceptance_criteria
        ]
        normalized_discussion = [
            item
            if isinstance(item, ExternalSourceEvidenceDiscussionEntry)
            else ExternalSourceEvidenceDiscussionEntry.model_validate(item)
            for item in discussion
        ]
        normalized_links = [
            item
            if isinstance(item, ExternalSourceEvidenceLink)
            else ExternalSourceEvidenceLink.model_validate(item)
            for item in links
        ]
        normalized_metadata = _mapping(metadata or {}, "metadata")
        normalized_case_ids = _unique_text_list(case_ids, "case_ids")
        normalized_task_ids = _unique_text_list(task_ids, "task_ids")
        normalized_created_by_actor = _required_text(created_by_actor, "created_by_actor")
        computed_digest = external_source_evidence_content_digest(
            title=normalized_title,
            body=normalized_body,
            acceptance_criteria=normalized_acceptance_criteria,
            discussion=normalized_discussion,
            links=normalized_links,
            metadata=normalized_metadata,
        )
        supplied_digest = _optional_text(content_digest)
        if supplied_digest is not None and supplied_digest.casefold() != computed_digest:
            raise ValueError(
                "content_digest does not match the normalized evidence snapshot content"
            )
        created_snapshot_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal created_snapshot_id
            _require_present(state.teams, normalized_team_id, "team")
            if normalized_project_id is not None:
                project = _require_present(state.projects, normalized_project_id, "project")
                if project.team_id != normalized_team_id:
                    raise ValueError(
                        f"project {project.id} does not belong to team {normalized_team_id}"
                    )
            identity_matches = sorted(
                (
                    snapshot
                    for snapshot in state.external_source_evidence_snapshots.values()
                    if snapshot.team_id == normalized_team_id
                    and snapshot.project_id == normalized_project_id
                    and snapshot.provider_type == normalized_provider_type
                    and snapshot.source_type == normalized_source_type
                    and snapshot.external_identifier == normalized_external_identifier
                    and snapshot.external_version == normalized_external_version
                ),
                key=lambda snapshot: (snapshot.created_at, snapshot.id),
            )
            if identity_matches:
                existing = next(
                    (
                        snapshot
                        for snapshot in identity_matches
                        if snapshot.id == normalized_snapshot_id
                    ),
                    identity_matches[0],
                )
                if any(snapshot.content_digest != computed_digest for snapshot in identity_matches):
                    raise ValueError(
                        "external source evidence version already exists with "
                        "different normalized content"
                    )
                if normalized_snapshot_id is not None and all(
                    snapshot.id != normalized_snapshot_id for snapshot in identity_matches
                ):
                    raise ValueError(
                        "external source evidence identity already exists as "
                        f"{existing.id}, not requested id {normalized_snapshot_id}"
                    )
                if any(
                    snapshot.access_classification != normalized_access_classification
                    for snapshot in identity_matches
                ):
                    raise ValueError(
                        "external source evidence version already exists with a "
                        "different access classification"
                    )
                if any(
                    snapshot.fetched_at != normalized_fetched_at
                    or snapshot.expires_at != normalized_expires_at
                    or snapshot.freshness_status != normalized_freshness_status
                    for snapshot in identity_matches
                ):
                    raise ValueError(
                        "external source evidence version already exists with "
                        "different capture metadata"
                    )
                now = utc_now()
                _attach_external_source_evidence_snapshot(
                    state,
                    snapshot=existing,
                    case_ids=normalized_case_ids,
                    task_ids=normalized_task_ids,
                    now=now,
                )
                created_snapshot_id = existing.id
                return state
            effective_snapshot_id = normalized_snapshot_id or _generated_id(
                prefix="source-evidence",
                labels=[
                    normalized_provider_type,
                    normalized_source_type,
                    normalized_external_identifier,
                    normalized_external_version,
                ],
                existing_ids=state.external_source_evidence_snapshots,
            )
            _require_absent(
                state.external_source_evidence_snapshots,
                effective_snapshot_id,
                "external source evidence snapshot",
            )
            now = utc_now()
            snapshot = ExternalSourceEvidenceSnapshot(
                id=effective_snapshot_id,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                provider_type=normalized_provider_type,
                source_type=normalized_source_type,
                external_identifier=normalized_external_identifier,
                external_version=normalized_external_version,
                fetched_at=normalized_fetched_at,
                expires_at=normalized_expires_at,
                freshness_status=normalized_freshness_status,
                access_classification=normalized_access_classification,
                content_digest=computed_digest,
                title=normalized_title,
                body=normalized_body,
                acceptance_criteria=normalized_acceptance_criteria,
                discussion=normalized_discussion,
                links=normalized_links,
                metadata=dict(normalized_metadata),
                created_by_actor=normalized_created_by_actor,
                created_at=now,
            )
            state.external_source_evidence_snapshots[snapshot.id] = snapshot
            _attach_external_source_evidence_snapshot(
                state,
                snapshot=snapshot,
                case_ids=normalized_case_ids,
                task_ids=normalized_task_ids,
                now=now,
            )
            created_snapshot_id = snapshot.id
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if created_snapshot_id is None:
            raise RuntimeError("external source evidence snapshot was not created")
        return state, state.external_source_evidence_snapshots[created_snapshot_id]

    def attach_external_source_evidence_snapshot(
        self,
        *,
        snapshot_id: str,
        case_ids: Sequence[str] = (),
        task_ids: Sequence[str] = (),
        expected_revision: int | None = None,
    ) -> tuple[TeamState, ExternalSourceEvidenceSnapshot]:
        normalized_snapshot_id = _required_text(snapshot_id, "snapshot_id")
        normalized_case_ids = _unique_text_list(case_ids, "case_ids")
        normalized_task_ids = _unique_text_list(task_ids, "task_ids")
        if not normalized_case_ids and not normalized_task_ids:
            raise ValueError("case_ids or task_ids is required")

        def _mutate(state: TeamState) -> TeamState:
            snapshot = _require_present(
                state.external_source_evidence_snapshots,
                normalized_snapshot_id,
                "external source evidence snapshot",
            )
            _attach_external_source_evidence_snapshot(
                state,
                snapshot=snapshot,
                case_ids=normalized_case_ids,
                task_ids=normalized_task_ids,
                now=utc_now(),
            )
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        return state, state.external_source_evidence_snapshots[normalized_snapshot_id]

    def create_case(
        self,
        *,
        case_id: str | None,
        team_id: str,
        title: str,
        description: str = "",
        project_id: str | None = None,
        status: TeamCaseStatus | str = TeamCaseStatus.OPEN,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        owner_agent_id: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        tags: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        task_ids: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        state, _case_id = self.create_case_with_id(
            case_id=case_id,
            team_id=team_id,
            title=title,
            description=description,
            project_id=project_id,
            status=status,
            priority=priority,
            owner_agent_id=owner_agent_id,
            source=source,
            source_ref=source_ref,
            tags=tags,
            metadata=metadata,
            task_ids=task_ids,
            expected_revision=expected_revision,
        )
        return state

    def create_case_with_id(
        self,
        *,
        case_id: str | None,
        team_id: str,
        title: str,
        description: str = "",
        project_id: str | None = None,
        status: TeamCaseStatus | str = TeamCaseStatus.OPEN,
        priority: TeamInboxPriority | str = TeamInboxPriority.NORMAL,
        owner_agent_id: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        tags: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        task_ids: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, str]:
        normalized_case_id = _optional_text(case_id)
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_project_id = _optional_text(project_id)
        normalized_title = _required_text(title, "title")
        normalized_description = _optional_text(description) or ""
        normalized_status = (
            status if isinstance(status, TeamCaseStatus) else TeamCaseStatus(str(status))
        )
        normalized_priority = (
            priority
            if isinstance(priority, TeamInboxPriority)
            else TeamInboxPriority(str(priority))
        )
        normalized_owner_agent_id = _optional_text(owner_agent_id)
        normalized_source = _optional_text(source)
        normalized_source_ref = _optional_text(source_ref)
        normalized_tags = _unique_text_list(tags or [], "tags")
        normalized_metadata = _mapping(metadata or {}, "metadata")
        normalized_task_ids = _unique_text_list(task_ids or [], "task_ids")
        _validate_generic_case_source(normalized_source)
        _validate_case_status_transition(
            source=normalized_source,
            current_status=None,
            next_status=normalized_status,
        )
        created_case_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal created_case_id
            _validate_case_scope(
                state,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                owner_agent_id=normalized_owner_agent_id,
            )
            effective_case_id = normalized_case_id or _generated_id(
                prefix="case",
                labels=[normalized_project_id or normalized_team_id, normalized_title],
                existing_ids=state.cases,
            )
            _require_absent(state.cases, effective_case_id, "case")
            now = utc_now()
            case = TeamCase(
                id=effective_case_id,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                title=normalized_title,
                description=normalized_description,
                status=normalized_status,
                priority=normalized_priority,
                owner_agent_id=normalized_owner_agent_id,
                source=normalized_source,
                source_ref=normalized_source_ref,
                tags=normalized_tags,
                metadata=dict(normalized_metadata),
                task_ids=[],
                created_at=now,
                updated_at=now,
                closed_at=now if normalized_status == TeamCaseStatus.CLOSED else None,
            )
            state.cases[case.id] = case
            for task_id in normalized_task_ids:
                _link_task_to_case(state, task_id=task_id, case_id=case.id, now=now)
            created_case_id = case.id
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if created_case_id is None:
            raise RuntimeError("case was not created")
        return state, created_case_id

    def update_case(
        self,
        *,
        case_id: str,
        title: str | None = None,
        description: str | None = None,
        set_description: bool = False,
        project_id: str | None = None,
        set_project_id: bool = False,
        status: TeamCaseStatus | str | None = None,
        priority: TeamInboxPriority | str | None = None,
        owner_agent_id: str | None = None,
        set_owner_agent_id: bool = False,
        source: str | None = None,
        set_source: bool = False,
        source_ref: str | None = None,
        set_source_ref: bool = False,
        tags: list[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        task_ids: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_case_id = _required_text(case_id, "case_id")
        normalized_title = _optional_text(title)
        description_present = set_description or description is not None
        normalized_description = (
            (_optional_text(description) or "") if description_present else None
        )
        normalized_project_id = _optional_text(project_id)
        normalized_status = (
            status
            if status is None or isinstance(status, TeamCaseStatus)
            else TeamCaseStatus(str(status))
        )
        normalized_priority = (
            priority
            if priority is None or isinstance(priority, TeamInboxPriority)
            else TeamInboxPriority(str(priority))
        )
        normalized_owner_agent_id = _optional_text(owner_agent_id)
        normalized_source = _optional_text(source)
        normalized_source_ref = _optional_text(source_ref)
        normalized_tags = None if tags is None else _unique_text_list(tags, "tags")
        normalized_metadata = None if metadata is None else _mapping(metadata, "metadata")
        normalized_task_ids = None if task_ids is None else _unique_text_list(task_ids, "task_ids")

        events: list[dict[str, Any]] = []

        def _mutate(state: TeamState) -> TeamState:
            case = _require_present(state.cases, normalized_case_id, "case")
            previous_status = case.status
            next_project_id = case.project_id
            next_owner_agent_id = case.owner_agent_id
            next_source = case.source
            if set_project_id or project_id is not None:
                next_project_id = normalized_project_id
            if set_owner_agent_id or owner_agent_id is not None:
                next_owner_agent_id = normalized_owner_agent_id
            if set_source or source is not None:
                next_source = normalized_source
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
                source_ref_present=set_source_ref or source_ref is not None,
                next_source_ref=normalized_source_ref,
                metadata_present=normalized_metadata is not None,
                next_metadata=normalized_metadata,
                next_task_ids=normalized_task_ids,
            )
            now = utc_now()
            if normalized_title is not None:
                case.title = normalized_title
            if description_present:
                case.description = normalized_description
            if set_project_id or project_id is not None:
                case.project_id = next_project_id
            if normalized_status is not None:
                previous_status = case.status
                _validate_case_status_transition(
                    source=case.source,
                    current_status=previous_status,
                    next_status=normalized_status,
                )
                if next_source != case.source:
                    _validate_case_status_transition(
                        source=next_source,
                        current_status=previous_status,
                        next_status=normalized_status,
                    )
                case.status = normalized_status
                if (
                    case.status == TeamCaseStatus.CLOSED
                    and previous_status != TeamCaseStatus.CLOSED
                ):
                    case.closed_at = now
                elif case.status != TeamCaseStatus.CLOSED:
                    case.closed_at = None
            if normalized_priority is not None:
                case.priority = normalized_priority
            if set_owner_agent_id or owner_agent_id is not None:
                case.owner_agent_id = next_owner_agent_id
            if set_source or source is not None:
                case.source = next_source
            if set_source_ref or source_ref is not None:
                case.source_ref = normalized_source_ref
            if normalized_tags is not None:
                case.tags = normalized_tags
            if normalized_metadata is not None:
                case.metadata = dict(normalized_metadata)
            if normalized_task_ids is not None:
                for task_id in list(case.task_ids):
                    if task_id not in normalized_task_ids:
                        _unlink_task_from_case(state, task_id=task_id, case_id=case.id, now=now)
                for task_id in normalized_task_ids:
                    _link_task_to_case(state, task_id=task_id, case_id=case.id, now=now)
            else:
                for task_id in list(case.task_ids):
                    task = state.tasks.get(task_id)
                    if task is not None and not _case_can_link_task(state, case=case, task=task):
                        _unlink_task_from_case(state, task_id=task_id, case_id=case.id, now=now)
            case.updated_at = now
            append_case_closed_event(events, case, previous_status=previous_status)
            return state

        return self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(events),
            expected_revision=expected_revision,
        )

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
                deliverable_policy_edit = deliverable_policy_request and (
                    proposed_integration_boundary != task.integration_boundary
                    or proposed_deliverable_contract != task.deliverable_contract
                )
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
            state_model.tasks[effective_task_id] = AgentTask(
                id=effective_task_id,
                owner_agent_id=normalized_owner_agent_id,
                project_id=normalized_project_id,
                case_id=normalized_case_id,
                priority=priority,
                state=state,
                state_entered_at=now,
                title=normalized_title,
                description=normalized_description,
                created_by_agent_id=created_by_agent_id,
                created_by_actor=created_by_actor,
                parent_task_id=normalized_parent_task_id,
                originating_task_id=normalized_originating_task_id,
                git_base_branch=_resolve_new_task_git_base_branch(
                    state_model,
                    project_id=normalized_project_id,
                    explicit_git_base_branch=normalized_git_base_branch,
                    parent_task_id=normalized_parent_task_id,
                    case_id=normalized_case_id,
                    git_default_branch_resolver=(self._resolve_managed_git_default_branch),
                ),
                git_base_commit_sha=normalized_git_base_commit_sha,
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
            if normalized_case_id is not None:
                _link_task_to_case(
                    state_model,
                    task_id=effective_task_id,
                    case_id=normalized_case_id,
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

    def create_operator_directive(
        self,
        *,
        team_id: str,
        message: str,
        recipient_agent_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
        title: str | None = None,
        source: str | None = None,
        source_message_id: str | None = None,
        created_by: str | None = None,
        requesting_session_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, AgentInboxItem]:
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_message = _required_text(message, "message")
        normalized_recipient_id = _optional_text(recipient_agent_id)
        normalized_project_id = _optional_text(project_id)
        normalized_task_id = _optional_text(task_id)
        normalized_title = _optional_text(title)
        normalized_source = _optional_text(source)
        normalized_source_message_id = _optional_text(source_message_id)
        normalized_created_by = _optional_text(created_by)
        normalized_requesting_session_id = _optional_text(requesting_session_id)
        created_inbox_id: str | None = None

        def _mutate(state_model: TeamState) -> TeamState:
            nonlocal created_inbox_id
            now = utc_now()
            team = _require_present(state_model.teams, normalized_team_id, "team")
            if team.archived:
                raise ValueError(f"team {team.id} is archived")
            recipient_id = normalized_recipient_id or team.manager_agent_id
            recipient = _require_present(state_model.agents, recipient_id, "recipient agent")
            if recipient.team_id != team.id:
                raise ValueError(f"recipient agent {recipient.id} is not in team {team.id}")
            if recipient.status != AgentStatus.ACTIVE:
                raise ValueError(f"recipient agent {recipient.id} is not active")
            if normalized_requesting_session_id is not None:
                requester = _agent_for_session(
                    state_model,
                    session_id=normalized_requesting_session_id,
                )
                requester_is_team_manager = requester.id == team.manager_agent_id
                requester_is_governance_manager = _agent_governs_team(
                    state_model,
                    agent_id=requester.id,
                    team_id=team.id,
                )
                if not requester_is_team_manager and not requester_is_governance_manager:
                    raise ValueError(
                        "team operator directives from agent sessions require the "
                        "team manager session or governance manager session"
                    )
                if recipient.id != team.manager_agent_id:
                    raise ValueError(
                        "team manager and governance manager sessions can only enqueue "
                        "operator directives for the target team manager inbox"
                    )

            effective_project_id = normalized_project_id
            if normalized_task_id is not None:
                task = _require_present(state_model.tasks, normalized_task_id, "task")
                project = _require_present(state_model.projects, task.project_id, "project")
                if project.team_id != team.id:
                    raise ValueError(f"task {task.id} is not in team {team.id}")
                if effective_project_id is not None and effective_project_id != task.project_id:
                    raise ValueError(
                        f"task {task.id} belongs to project {task.project_id}, "
                        f"not project {effective_project_id}"
                    )
                effective_project_id = task.project_id
            if effective_project_id is not None:
                project = _require_present(
                    state_model.projects,
                    effective_project_id,
                    "project",
                )
                if project.team_id != team.id:
                    raise ValueError(f"project {project.id} is not in team {team.id}")

            payload: dict[str, Any] = {
                "title": normalized_title or "Operator directive",
                "message": normalized_message,
                "team_id": team.id,
            }
            if effective_project_id is not None:
                payload["project_id"] = effective_project_id
            if normalized_source is not None:
                payload["source"] = normalized_source
            if normalized_source_message_id is not None:
                payload["source_message_id"] = normalized_source_message_id
            if normalized_created_by is not None:
                payload["created_by"] = normalized_created_by
            if normalized_requesting_session_id is not None:
                payload["requesting_session_id"] = normalized_requesting_session_id

            inbox_id = f"inbox-{uuid4().hex}"
            state_model.inbox[inbox_id] = AgentInboxItem(
                id=inbox_id,
                recipient_agent_id=recipient.id,
                sender_agent_id=None,
                task_id=normalized_task_id,
                item_type=TeamInboxItemType.OPERATOR_DIRECTIVE,
                response_requested=True,
                priority=TeamInboxPriority.NORMAL,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            created_inbox_id = inbox_id
            _mark_agent_work_due(state_model, agent_id=recipient.id, now=now)
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if created_inbox_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("operator directive was not created")
        return state, state.inbox[created_inbox_id]

    def record_deliverable(
        self,
        *,
        deliverable_id: str | None,
        task_id: str,
        producer_agent_id: str,
        summary: str,
        artifact_refs: list[str] | None = None,
        outcomes: list[dict[str, Any]] | None = None,
        source_deliverable_ids: list[str] | None = None,
        completion: Mapping[str, Any] | None = None,
        state: TeamRecordState = TeamRecordState.PENDING,
        supersedes_deliverable_id: str | None = None,
        enforce_task_assessment: bool = False,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_deliverable_id = _optional_text(deliverable_id)
        normalized_task_id = _required_text(task_id, "task_id")
        normalized_producer_agent_id = _required_text(producer_agent_id, "producer_agent_id")
        normalized_summary = _required_text(summary, "summary")
        normalized_supersedes_deliverable_id = _optional_text(supersedes_deliverable_id)
        normalized_outcomes = [DeliverableOutcome.model_validate(item) for item in outcomes or []]
        normalized_source_deliverable_ids = _unique_text_list(
            source_deliverable_ids or [],
            "source_deliverable_ids",
        )
        normalized_completion = (
            TaskCompletion.model_validate(completion) if completion is not None else None
        )

        def _mutate(state_model: TeamState) -> TeamState:
            task = _require_present(state_model.tasks, normalized_task_id, "task")
            if enforce_task_assessment or task.integration_boundary:
                try:
                    require_deliverable_submission(
                        state_model,
                        task_id=task.id,
                        source_deliverable_ids=normalized_source_deliverable_ids,
                    )
                except TaskAssessmentBoundaryError as exc:
                    raise ValueError(exc.message) from None
            if (
                task.integration_boundary
                and task.deliverable_contract is not None
                and task.deliverable_contract.require_git_provenance
            ):
                raise ValueError(
                    f"integration boundary task {task.id} requires authoritative "
                    "Git provenance; the Admin deliverable endpoint cannot bind "
                    "server-verified git_refs, so submit with "
                    "team_submit_deliverable_for_review"
                )
            snapshotted_outcomes = snapshot_deliverable_outcomes(
                state_model,
                project_id=task.project_id,
                outcomes=normalized_outcomes,
            )
            superseded_deliverable = _superseded_deliverable_for_rework(
                state_model,
                task=task,
                producer_agent_id=normalized_producer_agent_id,
                supersedes_deliverable_id=normalized_supersedes_deliverable_id,
            )
            if (
                superseded_deliverable is not None
                and superseded_deliverable.review_cohort_id is not None
                and superseded_deliverable.review_cohort_sealed_at is not None
            ):
                raise ValueError(
                    f"replacement for sealed-cohort deliverable "
                    f"{superseded_deliverable.id} must use "
                    "team_submit_deliverable_for_review to atomically create a "
                    "complete fresh review cohort"
                )
            _validate_deliverable_outcomes_against_task_contract(
                task,
                snapshotted_outcomes,
                completion=normalized_completion,
            )
            effective_deliverable_id = normalized_deliverable_id or _generated_id(
                prefix="deliverable",
                labels=[normalized_task_id, normalized_summary],
                existing_ids=state_model.deliverables,
            )
            _require_absent(state_model.deliverables, effective_deliverable_id, "deliverable")
            deliverable = Deliverable(
                id=effective_deliverable_id,
                task_id=normalized_task_id,
                producer_agent_id=normalized_producer_agent_id,
                summary=normalized_summary,
                artifact_refs=artifact_refs or [],
                outcomes=snapshotted_outcomes,
                source_deliverable_ids=normalized_source_deliverable_ids,
                completion=normalized_completion,
                state=state,
                supersedes_deliverable_id=normalized_supersedes_deliverable_id,
                carried_findings=(
                    [
                        finding.model_copy(deep=True)
                        for finding in superseded_deliverable.carried_findings
                        if finding.state == "open"
                    ]
                    if superseded_deliverable is not None
                    else []
                ),
            )
            state_model.deliverables[deliverable.id] = deliverable
            if superseded_deliverable is not None:
                superseded_deliverable.superseded_by_deliverable_id = deliverable.id
                superseded_deliverable.supersession_reason = "Rework deliverable submitted."
                superseded_deliverable.superseded_at = deliverable.created_at
                superseded_deliverable.superseded_by_actor = f"agent:{normalized_producer_agent_id}"
            task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
            task.updated_at = utc_now()
            return state_model

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def create_outcome_kind_definition(
        self,
        *,
        definition_id: str | None,
        team_id: str,
        project_id: str | None,
        kind: str,
        name: str,
        description: str = "",
        reference_requirements: str = "",
        evidence_requirements: str = "",
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_id = _optional_text(definition_id)
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_project_id = _optional_text(project_id)
        normalized_kind = _required_text(kind, "kind")
        normalized_name = _required_text(name, "name")

        def _mutate(state: TeamState) -> TeamState:
            _require_present(state.teams, normalized_team_id, "team")
            if normalized_project_id is not None:
                project = _require_present(
                    state.projects,
                    normalized_project_id,
                    "project",
                )
                if project.team_id != normalized_team_id:
                    raise ValueError(
                        f"project {project.id} does not belong to team {normalized_team_id}"
                    )
            for existing in state.outcome_kind_definitions.values():
                if (
                    existing.team_id == normalized_team_id
                    and existing.project_id == normalized_project_id
                    and existing.kind == normalized_kind
                ):
                    raise ValueError(
                        f"outcome kind {normalized_kind} already exists in "
                        f"{normalized_project_id or normalized_team_id}"
                    )
            effective_id = normalized_id or _generated_id(
                prefix="outcome-kind",
                labels=[
                    normalized_project_id or normalized_team_id,
                    normalized_kind,
                ],
                existing_ids=state.outcome_kind_definitions,
            )
            _require_absent(
                state.outcome_kind_definitions,
                effective_id,
                "outcome kind definition",
            )
            now = utc_now()
            state.outcome_kind_definitions[effective_id] = OutcomeKindDefinition(
                id=effective_id,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                kind=normalized_kind,
                name=normalized_name,
                description=description,
                reference_requirements=reference_requirements,
                evidence_requirements=evidence_requirements,
                created_at=now,
                updated_at=now,
            )
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def update_outcome_kind_definition(
        self,
        *,
        definition_id: str,
        name: str | None = None,
        description: str | None = None,
        reference_requirements: str | None = None,
        evidence_requirements: str | None = None,
        archived: bool | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_id = _required_text(definition_id, "definition_id")

        def _mutate(state: TeamState) -> TeamState:
            definition = _require_present(
                state.outcome_kind_definitions,
                normalized_id,
                "outcome kind definition",
            )
            if archived is True:
                references = active_outcome_kind_references(
                    state,
                    definition=definition,
                )
                if references:
                    raise ValueError(
                        f"outcome kind {definition.kind} is used by " + ", ".join(references)
                    )
            if name is not None:
                definition.name = _required_text(name, "name")
            if description is not None:
                definition.description = description
            if reference_requirements is not None:
                definition.reference_requirements = reference_requirements
            if evidence_requirements is not None:
                definition.evidence_requirements = evidence_requirements
            if archived is not None:
                definition.archived = archived
            definition.updated_at = utc_now()
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def create_review_profile(
        self,
        *,
        profile_id: str | None,
        team_id: str,
        project_id: str | None = None,
        name: str,
        description: str = "",
        applicable_role_ids: list[str] | None = None,
        criteria: list[ReviewProfileCriterion] | None = None,
        required_outcome_kinds: list[str] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_profile_id = _optional_text(profile_id)
        normalized_team_id = _required_text(team_id, "team_id")
        normalized_name = _required_text(name, "name")
        normalized_project_id = _optional_text(project_id)

        def _mutate(state: TeamState) -> TeamState:
            _require_present(state.teams, normalized_team_id, "team")
            if normalized_project_id is not None:
                project = _require_present(state.projects, normalized_project_id, "project")
                if project.team_id != normalized_team_id:
                    raise ValueError(
                        f"project {normalized_project_id} does not belong to team {normalized_team_id}"
                    )
            effective_profile_id = normalized_profile_id or _generated_id(
                prefix="profile",
                labels=[normalized_project_id or normalized_team_id, normalized_name],
                existing_ids=state.review_profiles,
            )
            _require_absent(state.review_profiles, effective_profile_id, "review profile")
            now = utc_now()
            profile = ReviewProfile(
                id=effective_profile_id,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                name=normalized_name,
                description=description,
                applicable_role_ids=applicable_role_ids or [],
                criteria=criteria or [],
                required_outcome_kinds=required_outcome_kinds or [],
                created_at=now,
                updated_at=now,
            )
            for role_id in profile.applicable_role_ids:
                role = _require_present(state.roles, role_id, "role")
                _validate_role_filter_assignment(role, profile=profile)
            if not profile.archived:
                outcome_kind_snapshots(
                    state,
                    team_id=profile.team_id,
                    project_id=profile.project_id,
                    kinds=profile.required_outcome_kinds,
                )
            state.review_profiles[profile.id] = profile
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def update_review_profile(
        self,
        *,
        profile_id: str,
        name: str | None = None,
        description: str | None = None,
        applicable_role_ids: list[str] | None = None,
        criteria: list[ReviewProfileCriterion] | None = None,
        required_outcome_kinds: list[str] | None = None,
        archived: bool | None = None,
        project_id: str | None = None,
        set_project_id: bool = False,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            profile = _require_present(state.review_profiles, profile_id, "review profile")
            if name is not None:
                profile.name = name
            if description is not None:
                profile.description = description
            if applicable_role_ids is not None:
                profile.applicable_role_ids = applicable_role_ids
            if criteria is not None:
                profile.criteria = [
                    ReviewProfileCriterion.model_validate(criterion) for criterion in criteria
                ]
            if required_outcome_kinds is not None:
                profile.required_outcome_kinds = _unique_text_list(
                    required_outcome_kinds,
                    "required_outcome_kinds",
                )
            if archived is not None:
                profile.archived = archived
            if set_project_id:
                normalized_project_id = _optional_text(project_id)
                if normalized_project_id is not None:
                    project = _require_present(state.projects, normalized_project_id, "project")
                    if project.team_id != profile.team_id:
                        raise ValueError(
                            f"project {normalized_project_id} does not belong to team {profile.team_id}"
                        )
                profile.project_id = normalized_project_id
            for role_id in profile.applicable_role_ids:
                role = _require_present(state.roles, role_id, "role")
                _validate_role_filter_assignment(role, profile=profile)
            if not profile.archived:
                outcome_kind_snapshots(
                    state,
                    team_id=profile.team_id,
                    project_id=profile.project_id,
                    kinds=profile.required_outcome_kinds,
                )
            profile.updated_at = utc_now()
            return state

        return self._repository.update(_mutate, expected_revision=expected_revision)

    def materialize_template(
        self,
        template_id: str,
        *,
        inputs: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_template_id = _required_text(template_id, "template_id")
        template = get_template(normalized_template_id)
        if template is None:
            raise ValueError(f"template {normalized_template_id} does not exist")

        normalized_inputs = _mapping(inputs, "inputs")
        team_input = _required_mapping(normalized_inputs, "team")
        project_input = _required_mapping(normalized_inputs, "project")
        team_name = _required_text(team_input.get("name"), "team.name")
        operator_communication_target_id = _optional_text(
            team_input.get("operator_communication_target_id")
        )
        escalate_to_governance = _optional_bool(
            team_input.get("escalate_to_governance"),
            "team.escalate_to_governance",
        )
        max_cost_usd = team_input.get("max_cost_usd") if "max_cost_usd" in team_input else None
        cost_warning_ratio = _cost_warning_ratio(team_input.get("cost_warning_ratio"))
        cost_budget_renewal_mode = _cost_budget_renewal_mode(
            team_input.get("cost_budget_renewal_mode")
        )
        project_name = _required_text(project_input.get("name"), "project.name")
        git = _project_git_config(project_input.get("git"))

        role_inputs = _materialize_role_inputs(
            template, normalized_inputs, team_input, project_input
        )
        current_state = self._repository.read()
        team_id = _optional_text(team_input.get("id")) or _generated_id(
            prefix="team",
            labels=[team_name],
            existing_ids=current_state.teams,
        )
        project_id = _optional_text(project_input.get("id")) or _generated_id(
            prefix="project",
            labels=[team_id, project_name],
            existing_ids=current_state.projects,
        )
        goal_ids = {
            goal.id: _unique_id_from_base(
                _materialized_template_record_id(project_id, goal.id),
                existing_ids=set(),
            )
            for goal in template.project_goals
        }
        used_review_profile_ids = set(current_state.review_profiles)
        review_profile_ids = {
            profile.id: _unique_id_from_base(
                _materialized_template_record_id(project_id, profile.id),
                existing_ids=used_review_profile_ids,
            )
            for profile in template.review_profiles
        }
        used_review_profile_ids.update(review_profile_ids.values())
        used_outcome_kind_ids = set(current_state.outcome_kind_definitions)
        outcome_kind_ids = {
            definition.kind: _unique_id_from_base(
                _materialized_template_record_id(
                    project_id if definition.project_scoped else team_id,
                    f"outcome-kind-{definition.kind}",
                ),
                existing_ids=used_outcome_kind_ids,
            )
            for definition in template.outcome_kinds
        }
        used_outcome_kind_ids.update(outcome_kind_ids.values())
        used_task_ids = set(current_state.tasks)
        task_ids = {
            task.id: _unique_id_from_base(
                _materialized_template_record_id(project_id, task.id),
                existing_ids=used_task_ids,
            )
            for task in template.tasks
        }
        used_task_ids.update(task_ids.values())
        used_role_ids = set(current_state.roles)
        role_ids: dict[str, str] = {}
        for role in template.roles:
            role_id = _unique_id_from_base(
                _default_role_id(
                    team_id=team_id,
                    project_id=None if role.key == template.root_role_key else project_id,
                    role_key=role.key,
                ),
                existing_ids=used_role_ids,
            )
            used_role_ids.add(role_id)
            role_ids[role.key] = role_id
        used_agent_ids = set(current_state.agents)
        for role in template.roles:
            role_input = role_inputs[role.key]
            agent_id = _optional_text(role_input.get("agent_id", role_input.get("id")))
            if agent_id is None:
                agent_id = _generated_id(
                    prefix="agent",
                    labels=[
                        team_id if role.key == template.root_role_key else project_id,
                        role.label,
                    ],
                    existing_ids=used_agent_ids,
                )
            role_input["agent_id"] = agent_id
            used_agent_ids.add(agent_id)
        concrete_ids = [
            team_id,
            project_id,
            *role_ids.values(),
            *[role_input["agent_id"] for role_input in role_inputs.values()],
            *goal_ids.values(),
            *outcome_kind_ids.values(),
            *review_profile_ids.values(),
            *task_ids.values(),
        ]
        _ensure_unique(concrete_ids, "materialization ids")
        _ensure_unique(
            [role_input["session_id"] for role_input in role_inputs.values()],
            "materialization session ids",
        )

        result: dict[str, Any] = {
            "success": False,
            "template_id": template.id,
            "template_name": template.name,
            "team_id": team_id,
            "project_id": project_id,
            "role_ids": role_ids,
            "agent_ids": {key: value["agent_id"] for key, value in role_inputs.items()},
            "goal_ids": [goal_ids[goal.id] for goal in template.project_goals],
            "outcome_kind_definition_ids": [
                outcome_kind_ids[definition.kind] for definition in template.outcome_kinds
            ],
            "review_profile_ids": [
                review_profile_ids[profile.id] for profile in template.review_profiles
            ],
            "task_ids": [task_ids[task.id] for task in template.tasks],
            "errors": [],
        }

        role_agent_ids = {role.key: role_inputs[role.key]["agent_id"] for role in template.roles}
        root_role_key = template.root_role_key
        root_role = next(role for role in template.roles if role.key == root_role_key)
        if root_role.reports_to_role_key is not None:
            raise ValueError(f"template {template.id} root role must not report upward")

        def _mutate(state: TeamState) -> TeamState:
            _require_absent(state.teams, team_id, "team")
            _require_absent(state.projects, project_id, "project")
            for role_key, role_id in role_ids.items():
                _require_absent(state.roles, role_id, "role")
            for role_key, role_input in role_inputs.items():
                _require_absent(state.agents, role_input["agent_id"], "agent")
            for profile in template.review_profiles:
                _require_absent(
                    state.review_profiles,
                    review_profile_ids[profile.id],
                    "review profile",
                )
            for definition in template.outcome_kinds:
                _require_absent(
                    state.outcome_kind_definitions,
                    outcome_kind_ids[definition.kind],
                    "outcome kind definition",
                )
            for task in template.tasks:
                _require_absent(state.tasks, task_ids[task.id], "task")

            now = utc_now()
            for role in template.roles:
                role_project_id = None if role.key == root_role_key else project_id
                state.roles[role_ids[role.key]] = TeamRole(
                    id=role_ids[role.key],
                    team_id=team_id,
                    project_id=role_project_id,
                    name=role.label,
                    description=role.description,
                    permissions=_normalize_role_permissions(role.permissions),
                    ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                    created_at=now,
                    updated_at=now,
                )
            manager_role_input = role_inputs[root_role_key]
            manager = PersistentAgent(
                id=manager_role_input["agent_id"],
                team_id=team_id,
                role_id=role_ids[root_role_key],
                session_id=manager_role_input["session_id"],
                reports_to_agent_id=None,
                ephemeral_policy=root_role.ephemeral_policy.model_copy(deep=True),
            )
            state.agents[manager.id] = manager
            team = Team(
                id=team_id,
                name=team_name,
                manager_agent_id=manager.id,
                operator_communication_target_id=operator_communication_target_id,
                escalate_to_governance=escalate_to_governance or False,
                max_cost_usd=max_cost_usd,
                cost_warning_ratio=cost_warning_ratio,
                cost_budget_renewal_mode=cost_budget_renewal_mode,
                created_at=now,
                updated_at=now,
            )
            if team.max_cost_usd is not None:
                _open_team_cost_budget_window(
                    team,
                    now=now,
                    kind=TeamCostBudgetWindowKind.CONFIGURED,
                    actor="operator:team-template",
                    reason="Initial cost budget configured from Team template.",
                )
            state.teams[team_id] = team

            project_goals = [
                goal.model_copy(update={"id": goal_ids[goal.id]}, deep=True)
                for goal in template.project_goals
            ]
            state.projects[project_id] = Project(
                id=project_id,
                team_id=team_id,
                name=project_name,
                lead_agent_id=role_agent_ids["project_lead"],
                goals=project_goals,
                git=git,
                created_at=now,
                updated_at=now,
            )
            state.teams[team_id].project_ids = _append_unique(
                state.teams[team_id].project_ids,
                project_id,
            )

            role_objects: dict[str, PersistentAgent] = {
                root_role_key: manager,
            }
            ordered_roles = _ordered_template_roles(template)
            for role in ordered_roles:
                if role.key == root_role_key:
                    continue
                superior_role_key = role.reports_to_role_key
                if superior_role_key is None:
                    raise ValueError(
                        f"template {template.id} role {role.key} is missing reports_to_role_key"
                    )
                superior = role_objects.get(superior_role_key)
                if superior is None:
                    raise ValueError(
                        f"template {template.id} role {role.key} reports to missing role "
                        f"{superior_role_key}"
                    )
                role_agent = PersistentAgent(
                    id=role_agent_ids[role.key],
                    team_id=team_id,
                    role_id=role_ids[role.key],
                    session_id=role_inputs[role.key]["session_id"],
                    reports_to_agent_id=superior.id,
                    ephemeral_policy=role.ephemeral_policy.model_copy(deep=True),
                )
                state.agents[role_agent.id] = role_agent
                assignment_kind = (
                    AgentProjectAssignmentKind.PROJECT_LEAD
                    if role.key == "project_lead"
                    else AgentProjectAssignmentKind.PROJECT_MEMBER
                )
                assignment_id = _project_assignment_id(
                    project_id,
                    role_agent.id,
                    assignment_kind,
                    existing_ids=state.project_assignments,
                )
                state.project_assignments[assignment_id] = AgentProjectAssignment(
                    id=assignment_id,
                    team_id=team_id,
                    project_id=project_id,
                    agent_id=role_agent.id,
                    role_id=role_ids[role.key],
                    kind=assignment_kind,
                    created_at=now,
                    updated_at=now,
                )
                superior.subordinate_agent_ids = _append_unique(
                    superior.subordinate_agent_ids,
                    role_agent.id,
                )
                role_objects[role.key] = role_agent
            state.projects[project_id].lead_agent_id = role_agent_ids["project_lead"]
            state.projects[project_id].updated_at = now
            state.teams[team_id].updated_at = now

            for definition_template in template.outcome_kinds:
                definition = OutcomeKindDefinition(
                    id=outcome_kind_ids[definition_template.kind],
                    team_id=team_id,
                    project_id=(project_id if definition_template.project_scoped else None),
                    kind=definition_template.kind,
                    name=definition_template.name,
                    description=definition_template.description,
                    reference_requirements=(definition_template.reference_requirements),
                    evidence_requirements=(definition_template.evidence_requirements),
                    created_at=now,
                    updated_at=now,
                )
                state.outcome_kind_definitions[definition.id] = definition

            for profile_template in template.review_profiles:
                profile = ReviewProfile(
                    id=review_profile_ids[profile_template.id],
                    team_id=team_id,
                    project_id=project_id if profile_template.project_scoped else None,
                    name=profile_template.name,
                    description=profile_template.description,
                    applicable_role_ids=[
                        role_ids[role_key] for role_key in profile_template.applicable_role_keys
                    ],
                    criteria=[
                        criterion.model_copy(deep=True) for criterion in profile_template.criteria
                    ],
                    required_outcome_kinds=list(profile_template.required_outcome_kinds),
                    created_at=now,
                    updated_at=now,
                )
                state.review_profiles[profile.id] = profile

            template_git_base_branch = (
                _resolve_new_task_git_base_branch(
                    state,
                    project_id=project_id,
                    git_default_branch_resolver=(self._resolve_managed_git_default_branch),
                )
                if template.tasks
                else None
            )
            materialized_tasks: list[AgentTask] = []
            for task_template in template.tasks:
                owner_agent_id = role_agent_ids[task_template.owner_role_key]
                created_by_agent_id = role_agent_ids[task_template.created_by_role_key]
                dependency_task_ids = [
                    task_ids[dependency_id] for dependency_id in task_template.dependency_task_ids
                ]
                state.tasks[task_ids[task_template.id]] = AgentTask(
                    id=task_ids[task_template.id],
                    owner_agent_id=owner_agent_id,
                    project_id=project_id,
                    git_base_branch=template_git_base_branch,
                    priority=task_template.priority,
                    state=task_template.state,
                    title=task_template.title,
                    description=task_template.description,
                    created_by_agent_id=created_by_agent_id,
                    parent_task_id=(
                        task_ids[task_template.parent_task_id]
                        if task_template.parent_task_id is not None
                        else None
                    ),
                    dependency_task_ids=dependency_task_ids,
                    created_at=now,
                    updated_at=now,
                )
                materialized_tasks.append(state.tasks[task_ids[task_template.id]])
            ensure_task_work_lineages(state, materialized_tasks)

            result["success"] = True
            result["created_agent_ids"] = dict(result["agent_ids"])
            return state

        if git is None:
            state = self._repository.update(_mutate, expected_revision=expected_revision)
        else:
            state = self._update_with_project_repository_binding(
                _mutate,
                project_id=project_id,
                next_managed_repo_id=git.managed_repo_id,
                expected_revision=expected_revision,
            )
        result["revision"] = state.revision
        result["created_team_id"] = team_id
        result["created_project_id"] = project_id
        return state, result

    def record_review_request(
        self,
        *,
        review_request_id: str | None,
        deliverable_id: str,
        reviewer_agent_id: str,
        state: TeamRecordState = TeamRecordState.PENDING,
        findings: list[str] | None = None,
        review_profile_id: str | None = None,
        instructions: str | None = None,
        requested_by_actor: str = "operator",
        decision_at: datetime | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_review_request_id = _optional_text(review_request_id)
        normalized_deliverable_id = _required_text(deliverable_id, "deliverable_id")
        normalized_reviewer_agent_id = _required_text(reviewer_agent_id, "reviewer_agent_id")
        normalized_requested_by_actor = _required_text(
            requested_by_actor,
            "requested_by_actor",
        )
        blocker_result: dict[str, Any] = {"events": []}

        def _mutate(state_model: TeamState) -> TeamState:
            deliverable = _require_present(
                state_model.deliverables,
                normalized_deliverable_id,
                "deliverable",
            )
            reviewer = _require_present(
                state_model.agents,
                normalized_reviewer_agent_id,
                "reviewer",
            )
            task = _require_present(state_model.tasks, deliverable.task_id, "task")
            project = _require_present(state_model.projects, task.project_id, "project")
            if reviewer.team_id != project.team_id:
                raise ValueError(
                    f"reviewer {normalized_reviewer_agent_id} is not in the same team as deliverable "
                    f"{normalized_deliverable_id}"
                )
            profile, profile_error = _review_profile_for_request(
                state_model,
                profile_id=review_profile_id,
                expected_team_id=reviewer.team_id,
                project_id=task.project_id,
                reviewer=reviewer,
                deliverable=deliverable,
            )
            if profile_error is not None:
                raise ValueError(profile_error)
            if profile is not None and profile.required_outcome_kinds:
                delivered_outcome_kinds = {outcome.kind for outcome in deliverable.outcomes}
                missing_outcome_kinds = sorted(
                    set(profile.required_outcome_kinds) - delivered_outcome_kinds
                )
                if missing_outcome_kinds:
                    raise ValueError(
                        f"review profile {profile.id} requires deliverable outcome kinds: "
                        + ", ".join(missing_outcome_kinds)
                    )
            effective_review_profile_id = review_profile_id
            if profile is not None and effective_review_profile_id is None:
                effective_review_profile_id = profile.id
            effective_review_request_id = normalized_review_request_id or _generated_id(
                prefix="review",
                labels=[
                    normalized_deliverable_id,
                    effective_review_profile_id or normalized_reviewer_agent_id,
                ],
                existing_ids=state_model.review_requests,
            )
            _require_absent(
                state_model.review_requests,
                effective_review_request_id,
                "review request",
            )
            apply_formal_review_acceptance_policy(
                state_model,
                deliverable=deliverable,
                result=blocker_result,
            )
            now = utc_now()
            review = ReviewRequest(
                id=effective_review_request_id,
                deliverable_id=normalized_deliverable_id,
                reviewer_agent_id=normalized_reviewer_agent_id,
                requested_by_actor=normalized_requested_by_actor,
                state=state,
                findings=findings or [],
                review_profile_id=effective_review_profile_id,
                required_outcome_kind_definitions=snapshot_review_profile_outcomes(
                    state_model,
                    profile=profile,
                    project_id=task.project_id,
                ),
                instructions=instructions,
                reviewed_commit_sha=(
                    deliverable.git_refs.reviewed_commit_sha
                    or deliverable.git_refs.submitted_commit_sha
                    or deliverable.git_refs.commit_sha
                ),
                repository_snapshot=deliverable_review_repository_snapshot(
                    state_model,
                    deliverable=deliverable,
                    review_id=effective_review_request_id,
                ),
                created_at=now,
                decision_at=decision_at,
            )
            state_model.review_requests[review.id] = review
            deliverable.review_request_ids = _append_unique(
                deliverable.review_request_ids,
                review.id,
            )
            if state == TeamRecordState.BLOCKED:
                open_or_get_blocker(
                    state_model,
                    source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
                    source_id=review.id,
                    summary=f"Review {review.id} is blocked",
                    details="; ".join(findings or []) or "Review was created in blocked state.",
                    reporter_agent_id=reviewer.id,
                    team_id=project.team_id,
                    project_id=project.id,
                    task_id=task.id,
                    result=blocker_result,
                    now=now,
                )
            if state in {
                TeamRecordState.PENDING,
                TeamRecordState.IN_PROGRESS,
                TeamRecordState.BLOCKED,
            }:
                reopened = _reopen_accepted_deliverable_for_pending_review(
                    deliverable=deliverable,
                    task=task,
                    now=now,
                )
                if reopened:
                    _recalculate_project_status(state_model, task.project_id)
            return state_model

        return self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(blocker_result.get("events") or []),
            expected_revision=expected_revision,
        )

    def record_review_decision(
        self,
        *,
        review_request_id: str,
        state: TeamRecordState,
        findings: list[str] | None = None,
        criteria: list[ReviewCriterionResult] | None = None,
        finding_resolutions: list[Mapping[str, Any]] | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        normalized_id = _required_text(review_request_id, "review_request_id")
        if not _is_review_decision_state(state):
            raise ValueError(
                f"review decision state {state.value} must be accepted, rejected, or blocked"
            )
        findings_list = list(findings or [])
        criterion_results = (
            [ReviewCriterionResult.model_validate(item) for item in criteria]
            if criteria is not None
            else None
        )
        normalized_finding_resolutions = [dict(item) for item in (finding_resolutions or [])]
        blocker_result: dict[str, Any] = {"events": [], "created_inbox_item_ids": []}
        if self._human_review_eligibility_resolver is not None:
            blocker_result[HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY] = (
                self._human_review_eligibility_resolver
            )

        def _mutate(state_model: TeamState) -> TeamState:
            review = _require_present(state_model.review_requests, normalized_id, "review request")
            if review.retired_at is not None:
                action = "withdrawn" if review.state == TeamRecordState.PENDING else "retired"
                raise ValueError(f"review {review.id} was {action} and cannot receive a decision")
            effective_criteria = (
                criterion_results if criterion_results is not None else list(review.criteria)
            )
            policy_error = _review_decision_policy_error(
                state_model,
                review=review,
                decision_state=state,
                criterion_results=effective_criteria,
                finding_resolutions=normalized_finding_resolutions,
                findings=findings_list,
            )
            if policy_error is not None:
                raise ValueError(policy_error)
            review.criteria = effective_criteria
            _apply_finding_resolutions(
                state_model,
                review=review,
                decision_state=state,
                finding_resolutions=normalized_finding_resolutions,
            )
            _record_open_review_findings(
                state_model,
                review=review,
                decision_state=state,
                findings=findings_list,
                criterion_results=effective_criteria,
            )
            projects_to_recalc: set[str] = set()
            _cascade_review_decision(
                state_model,
                review=review,
                decision_state=state,
                findings=findings_list,
                projects_to_recalc=projects_to_recalc,
                result=blocker_result,
            )
            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, project_id)
            _maybe_mark_idle_context_rotation_due(
                state_model,
                agent_id=review.reviewer_agent_id,
                now=utc_now(),
                result=blocker_result,
            )
            return state_model

        updated_state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(blocker_result.get("events") or []),
            expected_revision=expected_revision,
        )
        blocker_result.pop(HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY, None)
        return updated_state

    def withdraw_review_request(
        self,
        *,
        review_request_id: str,
        reason: str,
        operator_actor_id: str = "operator",
        expected_revision: int | None = None,
    ) -> TeamState:
        """Withdraw one current pending non-release review without a decision."""

        return self._close_review_request_as_operator(
            review_request_id=review_request_id,
            reason=reason,
            operator_actor_id=operator_actor_id,
            expected_revision=expected_revision,
            action="withdraw",
        )

    def retire_review_request(
        self,
        *,
        review_request_id: str,
        reason: str,
        operator_actor_id: str = "operator",
        expected_revision: int | None = None,
    ) -> TeamState:
        """Retire one current terminal non-release review without changing its decision."""

        return self._close_review_request_as_operator(
            review_request_id=review_request_id,
            reason=reason,
            operator_actor_id=operator_actor_id,
            expected_revision=expected_revision,
            action="retire",
        )

    def _close_review_request_as_operator(
        self,
        *,
        review_request_id: str,
        reason: str,
        operator_actor_id: str,
        expected_revision: int | None,
        action: str,
    ) -> TeamState:
        normalized_id = _required_text(review_request_id, "review_request_id")
        normalized_reason = _required_text(reason, "reason")
        normalized_actor = _required_text(operator_actor_id, "operator_actor_id")
        closure_result: dict[str, Any] = {"events": []}

        def _mutate(state_model: TeamState) -> TeamState:
            review = _require_present(
                state_model.review_requests,
                normalized_id,
                "review request",
            )
            if review.retired_at is not None:
                if (
                    review.retirement_reason == normalized_reason
                    and review.retired_by == normalized_actor
                ):
                    return state_model
                raise ValueError(f"review {review.id} is already retired")
            if action == "withdraw":
                closed_review = withdraw_pending_review(
                    state_model,
                    review_request_id=normalized_id,
                    reason=normalized_reason,
                    retired_by=normalized_actor,
                    actor=None,
                    result=closure_result,
                )
            else:
                closed_review = retire_terminal_review(
                    state_model,
                    review_request_id=normalized_id,
                    reason=normalized_reason,
                    retired_by=normalized_actor,
                    actor=None,
                    result=closure_result,
                )
            deliverable = _require_present(
                state_model.deliverables,
                closed_review.deliverable_id,
                "deliverable",
            )
            task = _require_present(state_model.tasks, deliverable.task_id, "task")
            _recalculate_project_status(state_model, task.project_id)
            return state_model

        return self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(closure_result.get("events") or []),
            expected_revision=expected_revision,
        )

    def record_human_review_decision_with_result(
        self,
        *,
        human_review_request_id: str,
        reviewer_user_id: str,
        reviewer_display_name: str,
        decision: str,
        request_id: str,
        expected_request_revision: int,
        checklist_results: Mapping[str, Any] | None = None,
        confirmed_finding_ids: Sequence[str] = (),
        summary: str = "",
        evidence_refs: Sequence[str] = (),
        findings: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_human_request_id = _required_text(
            human_review_request_id,
            "human_review_request_id",
        )
        normalized_reviewer_user_id = _required_text(
            reviewer_user_id,
            "reviewer_user_id",
        )
        normalized_decision = _required_text(decision, "decision")
        normalized_checklist = dict(checklist_results or {})
        normalized_confirmed_ids = list(confirmed_finding_ids)
        normalized_evidence_refs = list(evidence_refs)
        normalized_findings = [dict(item) for item in findings]

        def _apply(state: TeamState, result: dict[str, Any]) -> None:
            record_human_review_decision(
                state,
                human_review_request_id=normalized_human_request_id,
                reviewer_user_id=normalized_reviewer_user_id,
                reviewer_display_name=reviewer_display_name,
                decision=normalized_decision,
                request_id=request_id,
                expected_revision=expected_request_revision,
                checklist_results=normalized_checklist,
                confirmed_finding_ids=normalized_confirmed_ids,
                summary=summary,
                evidence_refs=normalized_evidence_refs,
                findings=normalized_findings,
                result=result,
            )

        return self._run_idempotent_human_command(
            operation="team.human_review.decision",
            caller_user_id=normalized_reviewer_user_id,
            scope_kind="human_review_request",
            scope_id=normalized_human_request_id,
            request_id=request_id,
            normalized_payload={
                "decision": normalized_decision,
                "expected_request_revision": expected_request_revision,
                "checklist_results": normalized_checklist,
                "confirmed_finding_ids": normalized_confirmed_ids,
                "summary": str(summary or "").strip(),
                "evidence_refs": normalized_evidence_refs,
                "findings": normalized_findings,
            },
            apply_command=_apply,
        )

    def reassign_human_review_with_result(
        self,
        *,
        human_review_request_id: str,
        user_ids: Sequence[str],
        assigned_by_user_id: str,
        request_id: str,
        expected_request_revision: int,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_human_request_id = _required_text(
            human_review_request_id,
            "human_review_request_id",
        )
        normalized_actor = _required_text(
            assigned_by_user_id,
            "assigned_by_user_id",
        )
        normalized_user_ids = sorted({_required_text(user_id, "user_ids") for user_id in user_ids})

        def _apply(state: TeamState, result: dict[str, Any]) -> None:
            human_request = _require_present(
                state.human_review_requests,
                normalized_human_request_id,
                "human review request",
            )
            if human_request.revision != expected_request_revision:
                raise ValueError(
                    "stale human review revision: "
                    f"expected {expected_request_revision}, "
                    f"current {human_request.revision}"
                )
            reassigned = reassign_human_review(
                state,
                human_review_request_id=normalized_human_request_id,
                user_ids=normalized_user_ids,
                assigned_by_user_id=normalized_actor,
                result=result,
            )
            result["human_review_request_id"] = reassigned.id
            result["assignment_generation"] = reassigned.assignment_generation

        return self._run_idempotent_human_command(
            operation="team.human_review.reassign",
            caller_user_id=normalized_actor,
            scope_kind="human_review_request",
            scope_id=normalized_human_request_id,
            request_id=request_id,
            normalized_payload={
                "user_ids": normalized_user_ids,
                "expected_request_revision": expected_request_revision,
            },
            apply_command=_apply,
        )

    def resume_human_review_with_result(
        self,
        *,
        human_review_request_id: str,
        resumed_by_user_id: str,
        request_id: str,
        expected_request_revision: int,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_human_request_id = _required_text(
            human_review_request_id,
            "human_review_request_id",
        )
        normalized_actor = _required_text(
            resumed_by_user_id,
            "resumed_by_user_id",
        )

        def _apply(state: TeamState, result: dict[str, Any]) -> None:
            human_request = _require_present(
                state.human_review_requests,
                normalized_human_request_id,
                "human review request",
            )
            if human_request.revision != expected_request_revision:
                raise ValueError(
                    "stale human review revision: "
                    f"expected {expected_request_revision}, "
                    f"current {human_request.revision}"
                )
            resumed = resume_human_review(
                state,
                human_review_request_id=normalized_human_request_id,
                result=result,
            )
            result["human_review_request_id"] = resumed.id

        return self._run_idempotent_human_command(
            operation="team.human_review.resume",
            caller_user_id=normalized_actor,
            scope_kind="human_review_request",
            scope_id=normalized_human_request_id,
            request_id=request_id,
            normalized_payload={
                "expected_request_revision": expected_request_revision,
            },
            apply_command=_apply,
        )

    def record_human_review_preparation_result_with_result(
        self,
        *,
        human_review_request_id: str,
        recorded_by_user_id: str,
        request_id: str,
        expected_request_revision: int | None,
        candidate_commit_sha: str,
        candidate_tree_hash: str,
        preparation_generation: int,
        target: Mapping[str, Any] | None,
        error: str | None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_human_request_id = _required_text(
            human_review_request_id,
            "human_review_request_id",
        )
        normalized_actor = _required_text(
            recorded_by_user_id,
            "recorded_by_user_id",
        )
        normalized_candidate_commit_sha = _required_text(
            candidate_commit_sha,
            "candidate_commit_sha",
        )
        normalized_candidate_tree_hash = _required_text(
            candidate_tree_hash,
            "candidate_tree_hash",
        )
        normalized_preparation_generation = int(preparation_generation)
        if normalized_preparation_generation < 1:
            raise ValueError("preparation_generation must be greater than zero")
        normalized_target = dict(target) if target is not None else None
        normalized_error = _optional_text(error)

        def _apply(state: TeamState, result: dict[str, Any]) -> None:
            human_request = _require_present(
                state.human_review_requests,
                normalized_human_request_id,
                "human review request",
            )
            if (
                expected_request_revision is not None
                and human_request.revision != expected_request_revision
            ):
                raise ValueError(
                    "stale human review revision: "
                    f"expected {expected_request_revision}, "
                    f"current {human_request.revision}"
                )
            prepared = record_human_review_preparation_result(
                state,
                human_review_request_id=normalized_human_request_id,
                candidate_commit_sha=normalized_candidate_commit_sha,
                candidate_tree_hash=normalized_candidate_tree_hash,
                preparation_generation=normalized_preparation_generation,
                target=normalized_target,
                error=normalized_error,
                result=result,
            )
            result["human_review_request_id"] = prepared.id

        return self._run_idempotent_human_command(
            operation="team.human_review.preparation_result",
            caller_user_id=normalized_actor,
            scope_kind="human_review_request",
            scope_id=normalized_human_request_id,
            request_id=request_id,
            normalized_payload={
                "expected_request_revision": expected_request_revision,
                "candidate_commit_sha": normalized_candidate_commit_sha,
                "candidate_tree_hash": normalized_candidate_tree_hash,
                "preparation_generation": normalized_preparation_generation,
                "target": normalized_target,
                "error": normalized_error,
            },
            apply_command=_apply,
        )

    def record_human_finding_resolution_proposal_with_result(
        self,
        *,
        finding_id: str,
        proposed_resolution: str,
        evidence_refs: Sequence[str],
        proposed_by_deliverable_ids: Sequence[str],
        proposed_by_user_id: str,
        request_id: str,
        expected_state_revision: int,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_finding_id = _required_text(finding_id, "finding_id")
        normalized_resolution = _required_text(
            proposed_resolution,
            "proposed_resolution",
        )
        normalized_evidence_refs = sorted(_unique_text_list(evidence_refs, "evidence_refs"))
        normalized_deliverable_ids = sorted(
            _unique_text_list(
                proposed_by_deliverable_ids,
                "proposed_by_deliverable_ids",
            )
        )
        normalized_actor = _required_text(
            proposed_by_user_id,
            "proposed_by_user_id",
        )

        def _apply(state: TeamState, result: dict[str, Any]) -> None:
            finding = record_human_finding_resolution_proposal(
                state,
                finding_id=normalized_finding_id,
                proposed_resolution=normalized_resolution,
                evidence_refs=normalized_evidence_refs,
                proposed_by_deliverable_ids=normalized_deliverable_ids,
            )
            result["human_review_finding_id"] = finding.id
            result["correction_task_id"] = finding.correction_task_id

        return self._run_idempotent_human_command(
            operation="team.human_finding.propose_resolution",
            caller_user_id=normalized_actor,
            scope_kind="human_review_finding",
            scope_id=normalized_finding_id,
            request_id=request_id,
            normalized_payload={
                "proposed_resolution": normalized_resolution,
                "evidence_refs": normalized_evidence_refs,
                "proposed_by_deliverable_ids": normalized_deliverable_ids,
                "expected_state_revision": expected_state_revision,
            },
            apply_command=_apply,
            expected_state_revision=expected_state_revision,
        )

    def route_human_finding_correction_with_result(
        self,
        *,
        finding_id: str,
        owner_agent_id: str,
        routed_by_user_id: str,
        request_id: str,
        expected_state_revision: int,
        target_deliverable_ids: Sequence[str] | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_finding_id = _required_text(finding_id, "finding_id")
        normalized_owner_id = _required_text(owner_agent_id, "owner_agent_id")
        normalized_actor = _required_text(
            routed_by_user_id,
            "routed_by_user_id",
        )
        normalized_target_ids = (
            _unique_text_list(
                target_deliverable_ids,
                "target_deliverable_ids",
            )
            if target_deliverable_ids is not None
            else None
        )

        def _apply(state: TeamState, result: dict[str, Any]) -> None:
            correction_task = route_human_finding_correction(
                state,
                finding_id=normalized_finding_id,
                owner_agent_id=normalized_owner_id,
                result=result,
                target_deliverable_ids=normalized_target_ids,
            )
            result["human_review_finding_id"] = normalized_finding_id
            result["correction_task_id"] = correction_task.id

        return self._run_idempotent_human_command(
            operation="team.human_finding.route_correction",
            caller_user_id=normalized_actor,
            scope_kind="human_review_finding",
            scope_id=normalized_finding_id,
            request_id=request_id,
            normalized_payload={
                "owner_agent_id": normalized_owner_id,
                "target_deliverable_ids": normalized_target_ids,
                "expected_state_revision": expected_state_revision,
            },
            apply_command=_apply,
            expected_state_revision=expected_state_revision,
        )

    def close_post_release_feedback_case_with_result(
        self,
        *,
        case_id: str,
        reason: str,
        evidence_refs: Sequence[str],
        closed_by_user_id: str,
        request_id: str,
        expected_state_revision: int,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_case_id = _required_text(case_id, "case_id")
        normalized_reason = _required_text(reason, "reason")
        normalized_evidence_refs = sorted(_unique_text_list(evidence_refs, "evidence_refs"))
        normalized_actor = _required_text(
            closed_by_user_id,
            "closed_by_user_id",
        )

        def _apply(state: TeamState, result: dict[str, Any]) -> None:
            case = close_post_release_feedback_case(
                state,
                case_id=normalized_case_id,
                reason=normalized_reason,
                evidence_refs=normalized_evidence_refs,
                actor_kind="operator",
                actor_id=normalized_actor,
                result=result,
            )
            result["case_id"] = case.id
            result["case"] = case.model_dump(mode="json")

        return self._run_idempotent_human_command(
            operation="team.release.close_feedback_case",
            caller_user_id=normalized_actor,
            scope_kind="case",
            scope_id=normalized_case_id,
            request_id=request_id,
            normalized_payload={
                "reason": normalized_reason,
                "evidence_refs": normalized_evidence_refs,
                "expected_state_revision": expected_state_revision,
            },
            apply_command=_apply,
            expected_state_revision=expected_state_revision,
        )

    def report_post_release_feedback_with_result(
        self,
        *,
        release_package_id: str,
        reporter_user_id: str,
        reporter_display_name: str,
        report: str,
        evidence_refs: Sequence[str],
        request_id: str,
        expected_state_revision: int,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_release_id = _required_text(
            release_package_id,
            "release_package_id",
        )
        normalized_reporter = _required_text(
            reporter_user_id,
            "reporter_user_id",
        )
        normalized_report = _required_text(report, "report")
        normalized_evidence_refs = sorted(_unique_text_list(evidence_refs, "evidence_refs"))
        if not normalized_evidence_refs:
            raise ValueError("evidence_refs must contain at least one reference")

        def _apply(state: TeamState, result: dict[str, Any]) -> None:
            source_ref = f"{normalized_release_id}:{str(request_id).strip()}"
            existing = next(
                (
                    case
                    for case in state.cases.values()
                    if case.source == "post_release_feedback" and case.source_ref == source_ref
                ),
                None,
            )
            if existing is not None and (
                existing.description != normalized_report
                or existing.metadata.get("reporter_user_id") != normalized_reporter
                or list(existing.metadata.get("evidence_refs") or []) != normalized_evidence_refs
            ):
                raise ValueError("post-release report request_id was reused with different inputs")
            case = report_post_release_feedback(
                state,
                release_package_id=normalized_release_id,
                reporter_user_id=normalized_reporter,
                reporter_display_name=reporter_display_name,
                report=normalized_report,
                evidence_refs=normalized_evidence_refs,
                request_id=request_id,
                result=result,
            )
            result["case_id"] = case.id

        return self._run_idempotent_human_command(
            operation="team.release.report_issue",
            caller_user_id=normalized_reporter,
            scope_kind="release_package",
            scope_id=normalized_release_id,
            request_id=request_id,
            normalized_payload={
                "report": normalized_report,
                "evidence_refs": normalized_evidence_refs,
                "expected_state_revision": expected_state_revision,
            },
            apply_command=_apply,
            expected_state_revision=expected_state_revision,
        )

    def create_release_package(
        self,
        *,
        project_id: str,
        deliverable_ids: list[str],
        release_id: str | None = None,
        supersedes_release_package_id: str | None = None,
        post_release_feedback_case_id: str | None = None,
        release_branch: str | None = None,
        canonical_branch: str | None = None,
        summary: str | None = None,
        verification_waivers: object | None = None,
        operator_actor_id: str = "operator",
        expected_revision: int | None = None,
    ) -> TeamState:
        state, _result = self.create_release_package_with_result(
            project_id=project_id,
            deliverable_ids=deliverable_ids,
            release_id=release_id,
            supersedes_release_package_id=supersedes_release_package_id,
            post_release_feedback_case_id=post_release_feedback_case_id,
            release_branch=release_branch,
            canonical_branch=canonical_branch,
            summary=summary,
            verification_waivers=verification_waivers,
            operator_actor_id=operator_actor_id,
            expected_revision=expected_revision,
        )
        return state

    def create_release_package_with_result(
        self,
        *,
        project_id: str,
        deliverable_ids: list[str],
        release_id: str | None = None,
        supersedes_release_package_id: str | None = None,
        post_release_feedback_case_id: str | None = None,
        release_branch: str | None = None,
        canonical_branch: str | None = None,
        summary: str | None = None,
        verification_waivers: object | None = None,
        operator_actor_id: str = "operator",
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_project_id = _required_text(project_id, "project_id")
        request = {
            "id": _optional_text(release_id),
            "supersedes_release_package_id": _optional_text(supersedes_release_package_id),
            "post_release_feedback_case_id": _optional_text(post_release_feedback_case_id),
            "project_id": normalized_project_id,
            "deliverable_ids": list(deliverable_ids),
            "release_branch": _optional_text(release_branch),
            "canonical_branch": _optional_text(canonical_branch),
            "summary": _optional_text(summary),
            "release_requested_by_actor": (
                "operator:" + _required_text(operator_actor_id, "operator_actor_id")
            ),
        }
        if verification_waivers is not None:
            # Presence is retained only so the response can warn legacy callers.
            # Contents are deliberately ignored without validation.
            request["verification_waivers"] = True
        result: dict[str, Any] = {
            "success": False,
            "created": False,
            "duplicate": False,
            "existing_release_package_id": None,
            "release_package_ids": [],
            "errors": [],
            "events": [],
            "git_promotion_event_ids": [],
            "created_deliverable_ids": [],
            "next_action_hints": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            project = _require_present(state_model.projects, normalized_project_id, "project")
            projects_to_recalc: set[str] = set()
            created_id = _build_release_package(
                state_model,
                project=project,
                request=request,
                result=result,
                projects_to_recalc=projects_to_recalc,
                actor_agent_id=project.lead_agent_id,
                team_id=project.team_id,
            )
            if created_id is not None:
                result["release_package_ids"].append(created_id)
            if result["errors"]:
                if result.get("error_code"):
                    raise ReleasePackageValidationError(
                        code=str(result["error_code"]),
                        message="; ".join(result["errors"]),
                    )
                raise ValueError("; ".join(result["errors"]))
            for recalc_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, recalc_id)
            result["success"] = True
            return state_model

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
            expected_revision=expected_revision,
        )
        result["revision"] = state.revision
        return state, result

    def retry_release_gate_with_result(
        self,
        *,
        release_package_id: str,
        gate_kind: str,
        gate_id: str,
        reason: str,
        operator_actor_id: str,
        allow_rejected_review_exception: bool = False,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_release_id = _required_text(
            release_package_id,
            "release_package_id",
        )
        normalized_actor_id = _required_text(operator_actor_id, "operator_actor_id")
        result: dict[str, Any] = {
            "success": False,
            "release_package_id": normalized_release_id,
            "events": [],
            "created_inbox_item_ids": [],
            "created_review_request_ids": [],
            "release_verification_run_ids": [],
            "operator_actor_id": normalized_actor_id,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            release = _require_present(
                state_model.release_packages,
                normalized_release_id,
                "release package",
            )
            retry_release_gate(
                state_model,
                release=release,
                gate_kind=gate_kind,
                gate_id=gate_id,
                reason=reason,
                requested_by=normalized_actor_id,
                result=result,
                allow_rejected_review_exception=allow_rejected_review_exception,
            )
            _recalculate_project_status(state_model, release.project_id)
            result["success"] = True
            return state_model

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
            expected_revision=expected_revision,
        )
        result["revision"] = state.revision
        result["release_package"] = release_package_payload_with_derived(
            state,
            state.release_packages[normalized_release_id],
        )
        return state, result
