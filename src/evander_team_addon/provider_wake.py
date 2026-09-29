"""Provider wake."""

from __future__ import annotations

import os
from collections.abc import (
    Mapping,
)
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
)

from evander.addons import (
    addon_storage_path,
)
from evander.admin.web.user_store import (
    AdminWebUserStore,
)
from evander.effects.providers.base import (
    EffectProviderBase,
)
from evander.events.models import (
    WorkflowNodeEnvelope,
)
from evander.runtime_generation import (
    require_current_runtime_generation,
)

from .costs import (
    TeamCostService,
)
from .mcp import (
    TEAM_RECORD_CONTEXT_HANDOFF_TOOL,
)
from .models import (
    team_session_health_payload,
)
from .provider_artifacts import (
    _prepare_review_artifacts_for_lease,
)
from .provider_constants import (
    _TEAM_WAKE_DEFINITION_PAYLOAD_FIELDS,
    TEAM_WAKE_OUTPUT_MAX_LEASE_SUMMARIES,
    TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS,
    TEAM_WAKE_STATE_CONFLICT_RETRY_DELAY_SECONDS,
)
from .provider_events import (
    _materialize,
    _publish_team_events,
)
from .provider_failures import (
    _new_preparation_blocker_ids,
    _record_dependency_git_preparation_blocker,
    _record_project_git_preparation_blockers,
    _record_review_artifact_preparation_blockers,
    _record_review_repository_preparation_blockers,
    _record_wake_failure,
    _review_artifact_preparation_errors,
)
from .provider_git_errors import (
    DependencyGitPreparationError,
    ProjectGitPreparationError,
    ReviewRepositoryPreparationError,
)
from .provider_git_metadata import (
    _project_repo_prepare_record,
)
from .provider_git_repositories import (
    _prepare_project_repos,
)
from .provider_guidance import (
    _seed_team_agent_guidance,
)
from .provider_projection import (
    _mapping_items,
    _string_items,
)
from .provider_prompt_queue import (
    _coerce_mapping,
    _discard_failed_team_prompt_queue_record,
    _enqueue_team_agent_prompt,
    _result_reports_failure,
    _resume_failure_message,
    _resume_failure_reports_missing_thread,
)
from .provider_prompts import (
    _build_prompt,
)
from .provider_runtime import (
    _cost_guard_blocked_teams,
    _ordinary_team_tools_for_agent,
    _prompt_session_health_agent_ids,
    _register_team_dispatch_model_usage_context,
    _session_records_by_id,
    _session_repository_from_context,
    _set_session_team_mcp_tools,
    _team_session_has_active_prompt,
)
from .provider_schemas import (
    TeamWakeDueAgentsEffectDefinition,
)
from .provider_values import (
    _configured_definition_fields,
    _definition_payload,
    _optional_bool,
    _optional_int,
    _optional_text,
    _payload_field_configured,
    _required_text,
    _runtime_dir,
)
from .provider_wake_chat import (
    _activate_delivered_team_prompt,
    _dispatch_queued_team_chat_messages,
)
from .provider_wake_payloads import (
    _team_wake_has_chat_claim_candidate,
    _team_wake_invocation_key,
    _team_wake_lease_summary,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.admin_service import (
    TeamAdminService,
)
from .services.candidate_correction_reconciliation_service import (
    CandidateCorrectionReconciliationService,
)
from .services.chat_service import (
    TeamChatService,
)
from .services.domain.wake import (
    _append_agent_wake_requested_event,
)
from .services.human_correction_reconciliation_service import (
    HumanCorrectionReconciliationService,
)
from .services.human_review_assignment_service import (
    reconcile_current_human_review_assignments,
)
from .services.integration_manifest_reconciliation_service import (
    IntegrationManifestReconciliationService,
)
from .services.runtime_service import (
    TEAM_LEASE_PREPARED_COMMIT_MAX_ATTEMPTS,
    TeamRuntimeService,
)
from .services.wake_prompt_context import (
    wake_uses_fresh_context,
    work_lineage_resume_thread_id,
)
from .services.wake_singleflight import (
    TEAM_WAKE_SINGLE_FLIGHT,
)
from .services.worktree_cleanup_service import (
    EffectTaskWorktreeCleanupBackend,
    TeamWorktreeCleanupService,
)
from .sqlite_store import (
    ADDON_ID,
    STATE_DB_FILE,
)


class TeamWakeDueAgentsEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamWakeDueAgentsEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        runtime_dir = _runtime_dir(self.runtime_context)
        if os.environ.get("INVOCATION_ID"):
            require_current_runtime_generation(runtime_dir)
        state_db_path = addon_storage_path(runtime_dir, ADDON_ID, STATE_DB_FILE)
        repo = TeamStateRepository(runtime_dir)
        effective_resolved_config = dict(resolved_config) if resolved_config is not None else None
        invocation_key = _team_wake_invocation_key(
            self.effect_definition,
            resolved_config=effective_resolved_config,
            state_revision=repo.read_revision(),
        )
        return TEAM_WAKE_SINGLE_FLIGHT.run(
            state_db_path=state_db_path,
            invocation_key=invocation_key,
            execute=lambda: self._execute_once(
                resolved_config=effective_resolved_config,
                repository=repo,
                invocation_key=invocation_key,
            ),
        )

    def _execute_once(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        repository: TeamStateRepository | None = None,
        invocation_key: str | None = None,
    ) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            *_TEAM_WAKE_DEFINITION_PAYLOAD_FIELDS,
        )
        definition_fields = _configured_definition_fields(self.effect_definition)
        resolved_fields = set(resolved_config or {})
        if resolved_config:
            payload.update(dict(resolved_config))
        context = self.runtime_context
        resumer = getattr(context, "agent_resumer", None)
        if resumer is None or not callable(getattr(resumer, "resume_agent", None)):
            raise RuntimeError("effect:team:wake-due-agents requires agent_resumer")

        repo = repository or TeamStateRepository(_runtime_dir(context))
        run_global_maintenance = _optional_bool(
            payload.get("run_global_maintenance"),
            default=True,
        )
        candidate_correction_reconciliation: dict[str, Any] = {}
        candidate_correction_reconciliation_error: str | None = None
        if run_global_maintenance:
            try:
                candidate_correction_reconciliation = CandidateCorrectionReconciliationService(
                    repo
                ).reconcile()
                if candidate_correction_reconciliation.get("events"):
                    candidate_correction_reconciliation["published_event_ids"] = (
                        _publish_team_events(
                            context,
                            candidate_correction_reconciliation["events"],
                        )
                    )
            except Exception as exc:
                # The durable reconciliation blocker and the next Team cycle
                # preserve recovery without suppressing unrelated wakes.
                candidate_correction_reconciliation_error = str(exc) or exc.__class__.__name__
        integration_manifest_reconciliation: dict[str, Any] = {}
        integration_manifest_reconciliation_error: str | None = None
        if run_global_maintenance:
            try:
                integration_manifest_reconciliation = IntegrationManifestReconciliationService(
                    repo
                ).reconcile()
                if integration_manifest_reconciliation.get("events"):
                    integration_manifest_reconciliation["published_event_ids"] = (
                        _publish_team_events(
                            context,
                            integration_manifest_reconciliation["events"],
                        )
                    )
            except Exception as exc:
                integration_manifest_reconciliation_error = str(exc) or exc.__class__.__name__
        human_correction_reconciliation: dict[str, Any] = {}
        human_correction_reconciliation_error: str | None = None
        if run_global_maintenance:
            try:
                human_correction_reconciliation = HumanCorrectionReconciliationService(
                    repo
                ).reconcile()
                if human_correction_reconciliation.get("events"):
                    human_correction_reconciliation["published_event_ids"] = _publish_team_events(
                        context,
                        human_correction_reconciliation["events"],
                    )
            except Exception as exc:
                # Correction reconciliation is durable and retried on the next Team
                # cycle. A single malformed case must not suppress unrelated wakes.
                human_correction_reconciliation_error = str(exc) or exc.__class__.__name__
        human_review_assignment_reconciliation: dict[str, Any] = {}
        human_review_assignment_reconciliation_error: str | None = None
        if run_global_maintenance:
            try:
                human_review_assignment_reconciliation = reconcile_current_human_review_assignments(
                    repository=repo,
                    user_store=AdminWebUserStore(_runtime_dir(context) / "admin-web.db"),
                )
            except Exception as exc:
                # Directory/RBAC drift must be visible, but it must not suppress
                # unrelated agent wakes or worktree cleanup.
                human_review_assignment_reconciliation_error = str(exc) or exc.__class__.__name__
        worktree_cleanup: dict[str, Any] = {}
        worktree_cleanup_error: str | None = None
        if run_global_maintenance:
            try:
                worktree_cleanup = (
                    TeamWorktreeCleanupService(
                        repo,
                        EffectTaskWorktreeCleanupBackend(context, repository=repo),
                    )
                    .reconcile(
                        limit=_optional_int(
                            payload.get("worktree_cleanup_limit"),
                            default=10,
                        )
                        or 0,
                        grace_seconds=_optional_int(
                            payload.get("worktree_cleanup_grace_seconds"),
                            default=300,
                        )
                        or 0,
                        retry_seconds=_optional_int(
                            payload.get("worktree_cleanup_retry_seconds"),
                            default=300,
                        )
                        or 0,
                        recovery_seconds=_optional_int(
                            payload.get("worktree_cleanup_recovery_seconds"),
                            default=900,
                        )
                        or 1,
                        dry_run=(
                            _optional_bool(
                                payload.get("worktree_cleanup_dry_run"),
                                default=True,
                            )
                            if _payload_field_configured(
                                "worktree_cleanup_dry_run",
                                definition_fields=definition_fields,
                                resolved_fields=resolved_fields,
                            )
                            else None
                        ),
                    )
                    .model_dump()
                )
            except Exception as exc:  # cleanup must not suppress unrelated Team wakes
                worktree_cleanup_error = str(exc) or exc.__class__.__name__
        current_state = repo.read()
        current_state = TeamAdminService(repo).advance_team_cost_budget_windows(
            current_state=current_state,
        )
        cost_balance_reconciliation: dict[str, Any] = {}
        cost_balance_reconciliation_error: str | None = None
        if run_global_maintenance:
            try:
                cost_balance_reconciliation = TeamCostService(
                    runtime_dir=_runtime_dir(context),
                    state=current_state,
                    session_repository=_session_repository_from_context(context),
                ).reconcile_current_cost_balances()
            except Exception as exc:
                # A dirty balance is retried from its bounded current window. It must
                # not suppress unrelated wakes in this maintenance cycle.
                cost_balance_reconciliation_error = str(exc) or exc.__class__.__name__
        session_records = _session_records_by_id(_session_repository_from_context(context))
        cost_guard_blocked_teams = _cost_guard_blocked_teams(
            context,
            state=current_state,
            team_id=_optional_text(payload.get("team_id")),
            project_id=_optional_text(payload.get("project_id")),
            agent_id=_optional_text(payload.get("agent_id")),
        )
        blocked_team_ids = {
            _required_text(record["team_id"], "team_id") for record in cost_guard_blocked_teams
        }
        resume_mode = _optional_text(payload.get("resume_mode")) or "same_context"
        lease_limit = _optional_int(payload.get("limit"), default=10)
        normalized_limit = max(0, int(lease_limit or 0))
        transient_conflicts: list[dict[str, Any]] = []
        retry_event_ids: list[str] = []

        def defer_planning_conflict(exc: TeamStateConflictError, *, phase: str):
            retry_agent_id = _optional_text(payload.get("agent_id"))
            if retry_agent_id is None:
                raise exc
            retry_state = repo.read()
            retry_result: dict[str, Any] = {"events": []}
            _append_agent_wake_requested_event(
                retry_state,
                agent_id=retry_agent_id,
                result=retry_result,
                reason="state_conflict_retry",
                now=datetime.now(UTC),
                delay_seconds=TEAM_WAKE_STATE_CONFLICT_RETRY_DELAY_SECONDS,
                idempotency_suffix=(
                    f"{phase.replace('_', '-')}-state-conflict:"
                    f"{invocation_key or f'{exc.expected_revision}:{exc.current_revision}'}"
                ),
            )
            scheduled_ids = _publish_team_events(context, retry_result["events"])
            if not scheduled_ids:
                raise RuntimeError(
                    "dispatch planning exhausted state-conflict retries and no "
                    "successor wake could be scheduled"
                ) from exc
            retry_event_ids.extend(scheduled_ids)
            retry_agent = retry_state.agents.get(retry_agent_id)
            transient_conflicts.append(
                {
                    "agent_id": retry_agent_id,
                    "session_id": retry_agent.session_id if retry_agent is not None else None,
                    "dispatch_id": None,
                    "phase": phase,
                    "attempt_count": TEAM_LEASE_PREPARED_COMMIT_MAX_ATTEMPTS,
                    "conflict_count": TEAM_LEASE_PREPARED_COMMIT_MAX_ATTEMPTS,
                    "expected_revision": exc.expected_revision,
                    "current_revision": exc.current_revision,
                    "resolved": False,
                    "retry_scheduled": True,
                    "retry_event_ids": scheduled_ids,
                }
            )
            return retry_state

        chat_planning_conflict = False
        if normalized_limit == 0 or (
            not _team_wake_has_chat_claim_candidate(
                current_state,
                team_id=_optional_text(payload.get("team_id")),
                project_id=_optional_text(payload.get("project_id")),
                agent_id=_optional_text(payload.get("agent_id")),
                blocked_team_ids=blocked_team_ids,
                now=datetime.now(UTC),
            )
        ):
            state = current_state
            queued_chat_claims = []
        else:
            try:
                state, queued_chat_claims = TeamChatService(repo).claim_pending_chat_message_dispatches(
                    team_id=_optional_text(payload.get("team_id")),
                    project_id=_optional_text(payload.get("project_id")),
                    agent_id=_optional_text(payload.get("agent_id")),
                    limit=normalized_limit,
                    blocked_team_ids=blocked_team_ids,
                    session_is_busy=lambda session_id: _team_session_has_active_prompt(
                        context,
                        session_id=session_id,
                    ),
                    defer_activation=True,
                )
            except TeamStateConflictError as exc:
                state = defer_planning_conflict(exc, phase="chat_claim_planning")
                queued_chat_claims = []
                chat_planning_conflict = True
        chat_dispatches: list[dict[str, Any]] = []
        errors: list[str] = []
        state, chat_dispatches, chat_errors = _dispatch_queued_team_chat_messages(
            context,
            repo=repo,
            state=state,
            claims=queued_chat_claims,
            resumer=resumer,
            session_records=session_records,
            configured_resume_mode=resume_mode,
        )
        errors.extend(chat_errors)
        agent_lease_seconds = _optional_int(payload.get("lease_seconds"), default=900) or 900
        if chat_planning_conflict or normalized_limit == 0 or len(queued_chat_claims) >= normalized_limit:
            leases = []
        else:
            regular_limit = (
                max(0, normalized_limit - len(queued_chat_claims)) if normalized_limit else 0
            )
            try:
                state, leases = TeamRuntimeService(repo).lease_due_agents(
                    team_id=_optional_text(payload.get("team_id")),
                    project_id=_optional_text(payload.get("project_id")),
                    agent_id=_optional_text(payload.get("agent_id")),
                    limit=regular_limit,
                    lease_seconds=agent_lease_seconds,
                    wake_interval_seconds=_optional_int(
                        payload.get("wake_interval_seconds"),
                        default=900,
                    ),
                    max_no_progress_count=_optional_int(
                        payload.get("max_no_progress_count"),
                        default=3,
                    ),
                    low_priority_wake_after_cycles=_optional_int(
                        payload.get("low_priority_wake_after_cycles"),
                        default=4,
                    ),
                    respect_wake_interval=_optional_bool(
                        payload.get("respect_wake_interval"),
                        default=True,
                    ),
                    blocked_team_ids=blocked_team_ids,
                    session_is_busy=lambda session_id: _team_session_has_active_prompt(
                        context,
                        session_id=session_id,
                    ),
                    defer_activation=True,
                )
            except TeamStateConflictError as exc:
                state = defer_planning_conflict(exc, phase="lease_planning")
                leases = []
        dispatches: list[dict[str, Any]] = []
        prepared_project_repos: list[dict[str, Any]] = []
        failed_project_repo_preparations: list[dict[str, Any]] = []
        git_work_contexts: list[dict[str, Any]] = []
        review_artifact_imports: list[dict[str, Any]] = []
        review_artifact_import_errors: list[dict[str, Any]] = []
        preparation_blocker_ids: list[str] = []
        for lease_index, lease in enumerate(leases):
            agent = lease["agent"]
            prompt_submission_attempted = False
            try:
                lease = {
                    **lease,
                    "session_health": team_session_health_payload(
                        state,
                        session_records=session_records,
                        agent_ids=_prompt_session_health_agent_ids(state, agent),
                    ),
                }
                context_handoff_preparation = lease.get("reason") == "context_handoff"
                guidance_result = _seed_team_agent_guidance(context, agent=agent)
                try:
                    dispatch_scope = (
                        lease.get("dispatch") if isinstance(lease.get("dispatch"), Mapping) else {}
                    )
                    state, project_repos = _prepare_project_repos(
                        context,
                        repo=repo,
                        state=state,
                        agent=agent,
                        dispatch_id=lease["dispatch_id"],
                        tasks=lease.get("tasks"),
                        project_ids=_string_items(dispatch_scope.get("project_ids")),
                        review_request_ids=_string_items(dispatch_scope.get("review_request_ids")),
                    )
                except TeamStateConflictError:
                    raise
                except ReviewRepositoryPreparationError as exc:
                    state = TeamRuntimeService(repo).fail_agent_dispatch(
                        agent_id=agent["id"],
                        dispatch_id=lease["dispatch_id"],
                        error=f"review repository preparation failed before inference: {exc}",
                    )
                    state, created_blocker_ids = _record_review_repository_preparation_blockers(
                        repo,
                        review_request_ids=exc.review_request_ids,
                        error=str(exc),
                    )
                    preparation_blocker_ids.extend(created_blocker_ids)
                    errors.append(
                        f"{agent['id']}: review repository preparation failed; "
                        "the affected review attempts were blocked before inference"
                    )
                    continue
                except Exception as exc:
                    failed_project_repo_preparations.append(
                        _project_repo_prepare_record(
                            agent=agent,
                            dispatch_id=lease["dispatch_id"],
                            error=str(exc),
                        )
                    )
                    raise
                if project_repos:
                    lease = {**lease, "project_repos": project_repos}
                    if len(project_repos) == 1:
                        lease["project_repo"] = project_repos[0]
                for project_repo in project_repos:
                    conflict_retry_count = int(project_repo.get("state_conflict_retry_count") or 0)
                    if conflict_retry_count:
                        transient_conflicts.append(
                            {
                                "agent_id": agent["id"],
                                "session_id": agent["session_id"],
                                "dispatch_id": lease["dispatch_id"],
                                "phase": "git_work_context_commit",
                                "attempt_count": int(
                                    project_repo.get("state_commit_attempt_count") or 0
                                ),
                                "conflict_count": conflict_retry_count,
                                "resolved": True,
                                "retry_scheduled": False,
                            }
                        )
                    git_work_contexts.extend(_mapping_items(project_repo.get("git_work_contexts")))
                    prepared_project_repos.append(
                        _project_repo_prepare_record(
                            agent=agent,
                            dispatch_id=lease["dispatch_id"],
                            project_repo=project_repo,
                        )
                    )
                tool_grant_result = _set_session_team_mcp_tools(
                    context,
                    session_id=agent["session_id"],
                    tool_names=(
                        [TEAM_RECORD_CONTEXT_HANDOFF_TOOL]
                        if context_handoff_preparation
                        else _ordinary_team_tools_for_agent(agent)
                    ),
                    dispatch_id=lease["dispatch_id"],
                )
                if tool_grant_result["error"] is not None:
                    dispatch = state.dispatches[lease["dispatch_id"]]
                    receipt = dispatch.mcp_capability_receipt
                    if receipt is not None:
                        state, finalization = TeamRuntimeService(
                            repo
                        ).record_agent_mcp_capability_preparation_failure(
                            session_id=agent["session_id"],
                            dispatch_id=lease["dispatch_id"],
                            agent_id=agent["id"],
                            diagnostic=(
                                "MCP policy grant confirmation failed before resume: "
                                f"{tool_grant_result['error']}"
                            ),
                        )
                        errors.append(f"{agent['id']}: {tool_grant_result['error']}")
                        dispatches.append(
                            {
                                "agent_id": agent["id"],
                                "session_id": agent["session_id"],
                                "dispatch_id": lease["dispatch_id"],
                                "agent_guidance": guidance_result,
                                "team_mcp_tools": tool_grant_result,
                                "mcp_capability_receipt": finalization.get(
                                    "mcp_capability_receipt"
                                ),
                                "error": tool_grant_result["error"],
                            }
                        )
                        continue
                    raise RuntimeError(str(tool_grant_result["error"]))
                dispatch = state.dispatches[lease["dispatch_id"]]
                if dispatch.mcp_capability_receipt is not None:
                    state = TeamRuntimeService(repo).confirm_agent_dispatch_mcp_capability_grant(
                        agent_id=agent["id"],
                        dispatch_id=lease["dispatch_id"],
                        granted_tool_names=tool_grant_result["requested_tools"],
                    )
                    lease = {
                        **lease,
                        "dispatch": state.dispatches[lease["dispatch_id"]].model_dump(mode="json"),
                    }
                    leases[lease_index] = lease
                review_artifacts = _prepare_review_artifacts_for_lease(
                    context,
                    lease=lease,
                )
                if (
                    review_artifacts["review_artifact_imports"]
                    or review_artifacts["review_artifact_import_errors"]
                ):
                    lease = {**lease, **review_artifacts}
                    review_artifact_imports.extend(review_artifacts["review_artifact_imports"])
                    review_artifact_import_errors.extend(
                        review_artifacts["review_artifact_import_errors"]
                    )
                    leases[lease_index] = lease
                if review_artifacts["review_artifact_import_errors"]:
                    grouped_errors = _review_artifact_preparation_errors(
                        review_artifacts["review_artifact_import_errors"]
                    )
                    state = TeamRuntimeService(repo).fail_agent_dispatch(
                        agent_id=agent["id"],
                        dispatch_id=lease["dispatch_id"],
                        error=(
                            "review artifact preparation failed before inference: "
                            + "; ".join(
                                f"{review_id}: {message}"
                                for review_id, message in grouped_errors.items()
                            )
                        ),
                    )
                    state, created_blocker_ids = _record_review_artifact_preparation_blockers(
                        repo,
                        grouped_errors=grouped_errors,
                    )
                    preparation_blocker_ids.extend(created_blocker_ids)
                    errors.append(
                        f"{agent['id']}: review artifact preparation failed; "
                        "the affected review attempts were blocked before inference"
                    )
                    continue
                effective_resume_mode = (
                    resume_mode
                    if context_handoff_preparation
                    else "new_context"
                    if wake_uses_fresh_context(
                        lease,
                        effective_resume_mode=resume_mode,
                    )
                    else resume_mode
                )
                prompt = _build_prompt(
                    lease,
                    effective_resume_mode=effective_resume_mode,
                )
                _register_team_dispatch_model_usage_context(
                    context,
                    agent=agent,
                    dispatch_id=lease["dispatch_id"],
                    effect_id=_optional_text(getattr(self.effect_definition, "id", None)),
                )
                resume_thread_id = work_lineage_resume_thread_id(lease)
                try:
                    prompt_submission_attempted = True
                    result = _enqueue_team_agent_prompt(
                        resumer,
                        target=agent["session_id"],
                        prompt=prompt,
                        resume_mode=effective_resume_mode,
                        resume_thread_id=resume_thread_id,
                        dispatch_id=lease["dispatch_id"],
                    )
                except Exception as exc:
                    result_payload = {"success": False, "error": str(exc)}
                    if not _resume_failure_reports_missing_thread(
                        result_payload,
                        resume_thread_id=resume_thread_id,
                    ):
                        raise
                else:
                    result_payload = _coerce_mapping(result)
                if _resume_failure_reports_missing_thread(
                    result_payload,
                    resume_thread_id=resume_thread_id,
                ):
                    state, invalidated = TeamRuntimeService(
                        repo
                    ).invalidate_agent_work_lineage_thread_binding(
                        agent_id=agent["id"],
                        dispatch_id=lease["dispatch_id"],
                        resume_thread_id=resume_thread_id or "",
                    )
                    retry_allowed = _discard_failed_team_prompt_queue_record(
                        repo,
                        result=result_payload,
                        dispatch_id=lease["dispatch_id"],
                    )
                    if invalidated and retry_allowed:
                        lease = {
                            **lease,
                            "runtime": state.agent_runtimes[agent["id"]].model_dump(mode="json"),
                        }
                        leases[lease_index] = lease
                        effective_resume_mode = "new_context"
                        prompt = _build_prompt(
                            lease,
                            effective_resume_mode=effective_resume_mode,
                        )
                        result = _enqueue_team_agent_prompt(
                            resumer,
                            target=agent["session_id"],
                            prompt=prompt,
                            resume_mode=effective_resume_mode,
                            resume_thread_id=None,
                            dispatch_id=lease["dispatch_id"],
                        )
                        result_payload = _coerce_mapping(result)
                if _result_reports_failure(result_payload):
                    error = _resume_failure_message(result_payload)
                    state = _record_wake_failure(
                        repo,
                        lease=lease,
                        error=error,
                        delivery_not_started=not prompt_submission_attempted,
                    )
                    errors.append(f"{agent['id']}: {error}")
                    continue
                if bool(result_payload.get("queued")):
                    state = TeamRuntimeService(repo).mark_agent_dispatch_queued(
                        agent_id=agent["id"],
                        dispatch_id=lease["dispatch_id"],
                    )
                    dispatches.append(
                        {
                            "agent_id": agent["id"],
                            "session_id": agent["session_id"],
                            "dispatch_id": lease["dispatch_id"],
                            "agent_guidance": guidance_result,
                            "team_mcp_tools": tool_grant_result,
                            "result": result_payload,
                            "queued": True,
                        }
                    )
                    continue
                state, activation = _activate_delivered_team_prompt(
                    repo=repo,
                    dispatch_id=lease["dispatch_id"],
                    result_payload=result_payload,
                )
                lease = {
                    **lease,
                    "runtime": state.agent_runtimes[agent["id"]].model_dump(mode="json"),
                    "dispatch": state.dispatches[lease["dispatch_id"]].model_dump(mode="json"),
                }
                leases[lease_index] = lease
                delivered_action_feedback_dispatch_ids = list(
                    activation["delivered_action_feedback_dispatch_ids"]
                )
                dispatch_payload = {
                    "agent_id": agent["id"],
                    "session_id": agent["session_id"],
                    "dispatch_id": lease["dispatch_id"],
                    "agent_guidance": guidance_result,
                    "team_mcp_tools": tool_grant_result,
                    "result": result_payload,
                }
                if delivered_action_feedback_dispatch_ids:
                    dispatch_payload["delivered_action_feedback_dispatch_ids"] = (
                        delivered_action_feedback_dispatch_ids
                    )
                if project_repos:
                    dispatch_payload["project_repos"] = project_repos
                    if len(project_repos) == 1:
                        dispatch_payload["project_repo"] = project_repos[0]
                if lease.get("review_artifact_imports"):
                    dispatch_payload["review_artifact_imports"] = lease["review_artifact_imports"]
                if lease.get("review_artifact_import_errors"):
                    dispatch_payload["review_artifact_import_errors"] = lease[
                        "review_artifact_import_errors"
                    ]
                if activation["context_rotation"] is not None:
                    dispatch_payload["context_rotation"] = activation["context_rotation"]
                if activation["context_handoff_preparation"] is not None:
                    dispatch_payload["context_handoff_preparation"] = activation[
                        "context_handoff_preparation"
                    ]
                dispatches.append(dispatch_payload)
            except TeamStateConflictError as exc:
                conflict_record: dict[str, Any] = {
                    "agent_id": agent["id"],
                    "session_id": agent["session_id"],
                    "dispatch_id": lease["dispatch_id"],
                    "phase": "git_work_context_commit",
                    "attempt_count": TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS,
                    "conflict_count": TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS,
                    "expected_revision": exc.expected_revision,
                    "current_revision": exc.current_revision,
                    "resolved": False,
                    "retry_scheduled": False,
                    "retry_event_ids": [],
                }
                try:
                    state = TeamRuntimeService(repo).release_agent_wake_lease_for_retry(
                        agent_id=agent["id"],
                        dispatch_id=lease["dispatch_id"],
                        error=(
                            "repository/worktree preparation state conflict: "
                            f"expected revision {exc.expected_revision}, current "
                            f"revision {exc.current_revision}"
                        ),
                    )
                    retry_result: dict[str, Any] = {"events": []}
                    _append_agent_wake_requested_event(
                        state,
                        agent_id=agent["id"],
                        result=retry_result,
                        reason="state_conflict_retry",
                        now=datetime.now(UTC),
                        delay_seconds=TEAM_WAKE_STATE_CONFLICT_RETRY_DELAY_SECONDS,
                        idempotency_suffix=f"state-conflict:{lease['dispatch_id']}",
                    )
                    scheduled_ids = _publish_team_events(
                        context,
                        retry_result["events"],
                    )
                    if not scheduled_ids:
                        raise RuntimeError("transient wake retry event was not scheduled")
                    retry_event_ids.extend(scheduled_ids)
                    conflict_record["retry_scheduled"] = True
                    conflict_record["retry_event_ids"] = scheduled_ids
                except Exception as retry_exc:
                    errors.append(
                        f"{agent['id']}: failed to schedule transient wake retry: {retry_exc}"
                    )
                transient_conflicts.append(conflict_record)
            except Exception as exc:  # pragma: no cover - exercised by provider tests with fakes
                error = str(exc)
                try:
                    state = _record_wake_failure(
                        repo,
                        lease=lease,
                        error=error,
                        delivery_not_started=not prompt_submission_attempted,
                    )
                    if isinstance(exc, DependencyGitPreparationError):
                        state = _record_dependency_git_preparation_blocker(
                            repo,
                            lease=lease,
                            task_id=exc.task_id,
                            error=error,
                            conflict=exc.conflict,
                            cause=exc.cause,
                            evidence=exc.evidence,
                        )
                        preparation_blocker_ids.extend(
                            _new_preparation_blocker_ids(
                                state,
                                task_ids=[exc.task_id],
                            )
                        )
                    elif isinstance(exc, ProjectGitPreparationError):
                        state, created_blocker_ids = _record_project_git_preparation_blockers(
                            repo,
                            lease=lease,
                            project_id=exc.project_id,
                            error=error,
                            cause=exc.cause,
                            evidence=exc.evidence,
                        )
                        preparation_blocker_ids.extend(created_blocker_ids)
                except Exception as release_exc:  # pragma: no cover - defensive diagnostics
                    error = f"{error}; failed to release team runtime lease: {release_exc}"
                errors.append(f"{agent['id']}: {error}")
        final_state = repo.read()
        lease_summaries = [
            _team_wake_lease_summary(lease, state=final_state)
            for lease in leases[:TEAM_WAKE_OUTPUT_MAX_LEASE_SUMMARIES]
        ]
        return {
            "success": not errors,
            "coalesced": False,
            "revision": final_state.revision,
            "leased_count": len(leases),
            "lease_summary_version": 1,
            "lease_summaries": lease_summaries,
            "leases": lease_summaries,
            "lease_summaries_truncated": len(lease_summaries) < len(leases),
            "chat_dispatches": chat_dispatches,
            "dispatches": dispatches,
            "prepared_project_repos": prepared_project_repos,
            "failed_project_repo_preparations": failed_project_repo_preparations,
            "git_work_contexts": git_work_contexts,
            "review_artifact_imports": review_artifact_imports,
            "review_artifact_import_errors": review_artifact_import_errors,
            "preparation_blocker_ids": sorted(set(preparation_blocker_ids)),
            "transient_conflicts": transient_conflicts,
            "retry_event_ids": retry_event_ids,
            "retry_scheduled_count": len(retry_event_ids),
            "cost_guard_blocked_team_ids": sorted(blocked_team_ids),
            "cost_guard_blocked_teams": cost_guard_blocked_teams,
            "cost_balance_reconciliation": cost_balance_reconciliation,
            "cost_balance_reconciliation_error": cost_balance_reconciliation_error,
            "worktree_cleanup": worktree_cleanup,
            "worktree_cleanup_error": worktree_cleanup_error,
            "human_review_assignment_reconciliation": (human_review_assignment_reconciliation),
            "human_review_assignment_reconciliation_error": (
                human_review_assignment_reconciliation_error
            ),
            "human_correction_reconciliation": human_correction_reconciliation,
            "human_correction_reconciliation_error": (human_correction_reconciliation_error),
            "candidate_correction_reconciliation": (candidate_correction_reconciliation),
            "candidate_correction_reconciliation_error": (
                candidate_correction_reconciliation_error
            ),
            "integration_manifest_reconciliation": (integration_manifest_reconciliation),
            "integration_manifest_reconciliation_error": (
                integration_manifest_reconciliation_error
            ),
            "errors": errors,
        }
