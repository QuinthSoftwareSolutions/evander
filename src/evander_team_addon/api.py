from __future__ import annotations

import asyncio
import hashlib
import json
import os
import posixpath
import re
import shlex
import tempfile
import zipfile
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import to_jsonable_python
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse as StarletteJSONResponse,
    StreamingResponse,
)
from starlette.routing import Route

from evander.addons import AddonContext
from evander.admin.services.managed_git_common import (
    ManagedGitConflictError,
    ManagedGitNotFoundError,
)
from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
    TEAM_CHATS_MUTATE_PERMISSION,
    TEAM_CHATS_READ_ALL_PERMISSION,
    TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
    normalize_admin_web_group_id,
    scope_matches,
)
from evander.admin.web.shared import request_has_privileged_operations
from evander.admin.web.user_store import normalize_admin_web_login
from evander.events.internal import (
    InternalEventSource,
)
from evander.orchestration.internal_events import (
    build_internal_event_bus_for_runtime,
    build_scheduled_internal_event_store_for_runtime,
)
from evander.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from evander.email.http_utils import ProviderRequestError
from evander.models import utc_now
from evander.model_usage.runtime import MODEL_USAGE_STORE_FILENAME, model_usage_store
from evander.messaging.targets import load_communication_targets_config
from evander.voice_sessions import (
    OpenAIRealtimeClientSecretService,
    VoiceQueryDelegator,
    normalize_voice_session_status,
    sanitize_voice_metadata,
    voice_query_payload,
)

from .avatars import (
    AVATAR_MIME_TYPE,
    MAX_AVATAR_UPLOAD_BYTES,
    AvatarStorage,
    build_avatar_prompt,
    normalize_prompt_note,
    normalize_style_preset,
    openai_avatar_generator_from_config,
)
from .chat_attachments import (
    TeamChatAttachmentPolicyService,
    TeamChatAttachmentStorage,
    normalize_directory_path,
    validate_attachment_count,
    validate_attachment_policy_content,
)
from .models import (
    AgentStatus,
    AgentVoiceProfile,
    EphemeralAgentPolicy,
    ExternalSourceEvidenceFreshnessStatus,
    GovernanceAgendaItemType,
    ProjectGitConfig,
    ProjectReleasePolicy,
    ProjectGoal,
    ProjectStatus,
    ReviewProfileCriterion,
    RolePermission,
    TeamCaseStatus,
    TeamInboxItemType,
    TeamRecordState,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamOutboxStatus,
    TeamTaskState,
    TeamState,
    _agent_payload_with_role,
    agent_activity_summaries_payload,
    agent_project_assignment_payloads,
    agent_project_ids,
    current_failed_action_records,
    external_source_evidence_snapshot_payload,
    filter_work_lineage_payloads,
    filter_work_lineage_references,
    project_assignments_for_agent,
    effective_role_permissions,
    runtime_operations_payload,
    team_state_payload,
)
from .human_review_projection import (
    HUMAN_REVIEW_COLLECTION_KEYS,
    apply_compact_human_review_projection,
    human_review_request_detail_payload,
    release_package_api_payload,
)
from .costs import TeamCostService
from .templates import get_template, template_catalog
from .timeline import derive_team_timeline
from .repository import TeamStateConflictError, TeamStateRepository
from .outbox_publication import (
    publish_team_wake_events_with_outbox_recovery,
    request_team_outbox_publication as _request_team_outbox_publication,
    request_team_outbox_publication_for_records,
)
from .operator_chat_steering import (
    queue_operator_chat_steering,
    reconcile_operator_chat_steering_deliveries,
)
from .sqlite_store import TeamIdempotencyConflictError
from .services.domain.chat import CHAT_MESSAGE_QUEUED_EVENT_TYPE
from .services.domain.acceptance_verification import ACCEPTANCE_VERIFICATION_RETIRED_WARNING
from .services.domain.case_task import TaskGitBaseValidationError
from .services.domain.case_events import TEAM_CASE_CLOSED_EVENT_TYPE
from .services.domain.context_rotation import (
    _append_idle_context_rotation_wake_requested_event,
)
from .services.domain.release_packages import ReleasePackageValidationError
from .services.domain.git_common import GIT_PROMOTION_EVENT_TYPE
from .services.domain.task_assessment import task_payload_with_assessment
from .services.domain.wake import (
    AGENT_WAKE_REQUESTED_EVENT_TYPE,
)
from .services.admin_service import TeamAdminService
from .services.chat_service import (
    TeamChatService,
    project_scoped_team_chat_authorization,
    reply_only_team_chat_authorization,
)
from .services.human_review_assignment_service import (
    human_review_eligibility_resolver,
    reconcile_current_human_review_assignments,
)
from .services.memory_service import list_memory_records, memory_record_payload
from .services.runtime_service import TeamRuntimeService
from .services.worktree_cleanup_service import (
    EffectTaskWorktreeCleanupBackend,
    TeamWorktreeCleanupService,
)
from .services.review_dispatch_steering import steer_closed_review_dispatch
from .providers import _session_records_by_id, build_voice_reconciliation_context


class TeamJSONResponse(StarletteJSONResponse):
    """JSON response for Team API payloads containing derived model values."""

    def render(self, content: Any) -> bytes:
        return super().render(to_jsonable_python(content))


# Keep every existing Team API route on the serialization-safe response class.
JSONResponse = TeamJSONResponse


DEFAULT_CHAT_MESSAGE_LIMIT = 50
MAX_CHAT_MESSAGE_LIMIT = 100
DEFAULT_TIMELINE_LIMIT = 50
MAX_TIMELINE_LIMIT = 200
DEFAULT_STATE_HISTORY_LIMIT = 100
MAX_STATE_HISTORY_LIMIT = 500
DEFAULT_INBOX_HISTORY_LIMIT = 50
MAX_INBOX_HISTORY_LIMIT = 200
TEAM_VIEW_IDS = (
    "workspace",
    "operations",
    "delivery",
    "projects",
    "task-board",
    "cases",
    "people",
    "chat",
    "mail",
    "activity",
    "governance",
    "planning",
    "timeline",
    "memory",
    "org",
)
TEAM_STATE_COLLECTION_KEYS = (
    "teams",
    "governance_groups",
    "governance_agenda",
    "governance_runtimes",
    "projects",
    "roles",
    "agents",
    "project_assignments",
    "agent_runtimes",
    "stand_downs",
    "cases",
    "tasks",
    "external_source_evidence_snapshots",
    "deliverables",
    "review_requests",
    "outcome_kind_definitions",
    "review_profiles",
    "inbox",
    "blockers",
    "blocker_events",
    "dependency_composition_recoveries",
    "action_records",
    "memories",
    "release_packages",
    *HUMAN_REVIEW_COLLECTION_KEYS,
    "dispatches",
    "git_preparation_attempts",
    "worktree_cleanup_candidates",
    "ephemeral_assignments",
    "git_promotions",
    "git_work_contexts",
    "conversations",
    "conversation_messages",
)
TEAM_VIEW_COLLECTIONS: dict[str, set[str]] = {
    "operations": {
        "teams",
        "projects",
        "roles",
        "agents",
        "agent_runtimes",
        "stand_downs",
        "tasks",
        "external_source_evidence_snapshots",
        "inbox",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "action_records",
        "deliverables",
        "release_packages",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_work_contexts",
        "ephemeral_assignments",
    },
    "delivery": {
        "teams",
        "projects",
        "roles",
        "agents",
        "tasks",
        "external_source_evidence_snapshots",
        "deliverables",
        "review_requests",
        "outcome_kind_definitions",
        "review_profiles",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "release_packages",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_promotions",
        "git_work_contexts",
    },
    "projects": {
        "teams",
        "projects",
        "roles",
        "agents",
        "tasks",
        "external_source_evidence_snapshots",
        "deliverables",
        "review_requests",
        "outcome_kind_definitions",
        "review_profiles",
        "release_packages",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "cases",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_promotions",
        "git_work_contexts",
    },
    "task-board": {
        "teams",
        "projects",
        "agents",
        "agent_runtimes",
        "tasks",
        "cases",
        "external_source_evidence_snapshots",
        "inbox",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_work_contexts",
    },
    "cases": {
        "teams",
        "projects",
        "agents",
        "cases",
        "tasks",
        "external_source_evidence_snapshots",
    },
    "people": {
        "teams",
        "projects",
        "roles",
        "agents",
        "agent_runtimes",
        "stand_downs",
        "tasks",
        "external_source_evidence_snapshots",
        "inbox",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_work_contexts",
    },
    "governance": {
        "teams",
        "governance_groups",
        "governance_agenda",
        "governance_runtimes",
        "projects",
        "roles",
        "agents",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
    },
    "planning": {
        "teams",
        "projects",
        "agents",
        "agent_runtimes",
        "tasks",
        "external_source_evidence_snapshots",
        "deliverables",
        "review_requests",
        "review_profiles",
        "inbox",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "dispatches",
        "git_preparation_attempts",
        "worktree_cleanup_candidates",
        "git_work_contexts",
    },
    "timeline": {
        "teams",
        "projects",
        "roles",
        "agents",
        "cases",
        "tasks",
        "deliverables",
        "review_requests",
        "outcome_kind_definitions",
        "review_profiles",
        "release_packages",
        "blockers",
        "blocker_events",
        "dependency_composition_recoveries",
        "inbox",
        "action_records",
        "ephemeral_assignments",
        "git_promotions",
    },
    "org": {
        "teams",
        "governance_groups",
        "governance_agenda",
        "governance_runtimes",
        "projects",
        "roles",
        "agents",
        "agent_runtimes",
        "stand_downs",
        "tasks",
        "inbox",
    },
    "memory": {"teams", "projects", "agents", "memories"},
}
TEAM_VIEW_COLLECTIONS["workspace"] = {
    "teams",
    "governance_groups",
    "governance_agenda",
    "projects",
    "agents",
    "cases",
    "tasks",
    "external_source_evidence_snapshots",
    "deliverables",
    "review_requests",
    "inbox",
    "release_packages",
    "blockers",
    "blocker_events",
    "dependency_composition_recoveries",
    "dispatches",
    "git_preparation_attempts",
    "worktree_cleanup_candidates",
    "git_work_contexts",
    "git_promotions",
}
TEAM_VIEW_COLLECTIONS["chat"] = {"agents"}
TEAM_VIEW_COLLECTIONS["mail"] = {"teams", "projects", "roles", "agents"}
TEAM_VIEW_COLLECTIONS["activity"] = {"teams", "roles", "agents"}
TEAM_RESOURCE_TOKEN_COLLECTIONS = (
    "teams",
    "governance_groups",
    "governance_agenda",
    "projects",
    "roles",
    "agents",
    "cases",
    "tasks",
    "external_source_evidence_snapshots",
    "deliverables",
    "review_requests",
    "outcome_kind_definitions",
    "review_profiles",
    "inbox",
    "action_records",
    "memories",
    "release_packages",
    "blockers",
    "dependency_composition_recoveries",
    "stand_downs",
)
TEAM_STATE_HISTORY_COLLECTIONS = (
    "inbox",
    "action_records",
    "release_packages",
    "blockers",
    "blocker_events",
    "dependency_composition_recoveries",
    "ephemeral_assignments",
    "git_promotions",
    "conversation_messages",
)


def _team_costs_payload(
    context: AddonContext,
    repository: TeamStateRepository,
    *,
    backfill_transcripts: bool,
) -> dict[str, Any]:
    state = repository.read()
    state = TeamAdminService(repository).advance_team_cost_budget_windows(
        current_state=state,
    )
    service = TeamCostService(
        runtime_dir=context.runtime_dir,
        state=state,
        session_repository=_session_repository_from_context(context),
    )
    return service.costs_payload(backfill_transcripts=backfill_transcripts)


def admin_api_routes(context: AddonContext) -> Sequence[Route]:
    repository = TeamStateRepository(context.runtime_dir)
    admin_service = TeamAdminService(
        repository,
        managed_git_admin=getattr(context.service, "managed_git_admin", None),
        human_review_eligibility_resolver=human_review_eligibility_resolver(
            getattr(context.auth, "user_store", None)
        ),
    )
    chat_service = TeamChatService(repository)
    runtime_service = TeamRuntimeService(
        repository,
        event_bus_factory=lambda: build_internal_event_bus_for_runtime(
            context.runtime_dir, project_dir=context.project_dir, base_config=context.config
        ),
    )
    avatar_storage = AvatarStorage(context.runtime_dir)
    chat_attachment_storage = TeamChatAttachmentStorage(context.runtime_dir)
    chat_attachment_policy_service = TeamChatAttachmentPolicyService(repository)

    def _state_payload(request: Request, state: TeamState) -> dict[str, Any]:
        payload = _team_state_payload_for_request(
            context.auth,
            request,
            state,
            runtime_dir=context.runtime_dir,
            session_records=_session_records_by_id(_session_repository_from_context(context)),
            agent_execution_records=_agent_execution_records_by_session(context.service),
        )
        _add_release_outbox_metadata(payload, repository)
        return payload

    def _state_response(
        request: Request,
        state: TeamState,
        *,
        status_code: int = 200,
        rebased_from_revision: int | None = None,
        extra: Mapping[str, Any] | None = None,
        publish_role_queue: bool = False,
    ) -> JSONResponse:
        if publish_role_queue:
            publication = request_team_outbox_publication_for_records(
                context,
                outbox_record_ids=[
                    record.id
                    for record in repository.list_outbox_records(
                        status="pending",
                        intent_type="team.role_queue.wake_requested",
                    )
                ],
            )
            if publication:
                extra = {**dict(extra or {}), "role_queue_outbox_publication": publication}
        if _request_wants_view_scoped_response(request):
            response_payload = _mutation_view_response_payload(
                state,
                rebased_from_revision=rebased_from_revision,
            )
            if extra:
                response_payload.update(extra)
            return JSONResponse(response_payload, status_code=status_code)
        response_payload = _state_payload(request, state)
        if rebased_from_revision is not None:
            response_payload["rebased_from_revision"] = rebased_from_revision
        if extra:
            response_payload.update(extra)
        return JSONResponse(response_payload, status_code=status_code)

    def _run_ui_rebasable_mutation(
        payload: Mapping[str, Any],
        mutate: Any,
        *,
        target_collection: str | None = None,
        target_id: str | None = None,
        create_collection: str | None = None,
        create_id: str | None = None,
    ) -> tuple[TeamState, int | None]:
        expected_revision = _expected_revision(payload)
        try:
            return mutate(expected_revision), None
        except TeamStateConflictError:
            current = repository.read()
            if not _can_rebase_ui_mutation(
                payload,
                current,
                target_collection=target_collection,
                target_id=target_id,
                create_collection=create_collection,
                create_id=create_id,
            ):
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=current.revision,
                )
            return mutate(current.revision), expected_revision

    def _task_project_owner_authorization_error(
        request: Request,
        *,
        state: TeamState,
        task_id: str,
        action: str,
    ) -> JSONResponse | None:
        task = state.tasks.get(task_id)
        if task is None:
            return JSONResponse({"error": "task not found"}, status_code=404)
        project = state.projects.get(task.project_id)
        if project is None:
            return JSONResponse({"error": "task project not found"}, status_code=404)
        return _directory_owner_authorization_error(
            request,
            directory_path=project.directory_path,
            action=action,
        )

    def _directory_owner_authorization_error(
        request: Request,
        *,
        directory_path: str,
        action: str,
    ) -> JSONResponse | None:
        if _auth_can(
            context.auth,
            request,
            ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return None
        return JSONResponse(
            {"error": (f"{action} requires directory owner permission for the affected project.")},
            status_code=403,
        )

    async def get_state(request: Request) -> JSONResponse:
        return JSONResponse(_state_payload(request, repository.read()))

    async def get_bootstrap(request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            _bootstrap_payload_for_request(
                context.auth,
                request,
                state,
                communication_targets=_registered_communication_target_options(context),
            )
        )

    async def get_inbox_history(request: Request) -> JSONResponse:
        return JSONResponse(_inbox_history_payload_for_request(request, repository.read()))

    async def get_dispatch_events(request: Request) -> JSONResponse:
        state = repository.read()
        team_id = _query_text(request, "team_id")
        agent_id = _query_text(request, "agent_id")
        dispatch_id = _query_text(request, "dispatch_id")
        tool_name = _query_text(request, "tool_name")
        status = _query_text(request, "status")
        search_text = _query_text(request, "q")
        limit = _query_limited_int(
            request,
            "limit",
            default=100,
            max_value=200,
        )
        offset = _query_non_negative_int(request, "offset", default=0)
        rows = repository.list_dispatch_activity(
            dispatch_id=dispatch_id,
            agent_id=agent_id,
            tool_name=tool_name,
            newest_first=True,
        )
        if team_id:
            team_agent_ids = {
                agent.id for agent in state.agents.values() if agent.team_id == team_id
            }
            rows = [row for row in rows if row.agent_id in team_agent_ids]
        if status:
            rows = [row for row in rows if row.status.value == status]
        if search_text:
            needle = search_text.casefold()
            rows = [
                row
                for row in rows
                if needle
                in " ".join(
                    value
                    for value in (
                        row.id,
                        row.dispatch_id or "",
                        row.agent_id or "",
                        row.tool_name,
                        row.error_code or "",
                        row.error_message or "",
                        row.correction_context_summary or "",
                    )
                ).casefold()
            ]
        total_count = len(rows)
        page = rows[offset : offset + limit]

        def _event_payload(row: Any) -> dict[str, Any]:
            agent = state.agents.get(row.agent_id or "")
            if row.tool_name == "team_dispatch_started":
                kind = "dispatch_started"
                summary = "Dispatch started"
            elif row.tool_name == "team_dispatch_finished":
                kind = "dispatch_finished"
                summary = (
                    "Dispatch finished with progress"
                    if row.progress_classification != "none"
                    else "Dispatch finished without recorded progress"
                )
            elif row.status.value == "failure":
                kind = "tool_failed"
                summary = row.error_message or f"{row.tool_name} failed"
            else:
                kind = "tool_completed"
                summary = f"{row.tool_name} completed"
            return {
                "id": row.id,
                "dispatch_id": row.dispatch_id,
                "agent_id": row.agent_id,
                "agent_display_name": (
                    (agent.display_name or agent.id) if agent is not None else row.agent_id
                ),
                "team_id": agent.team_id if agent is not None else None,
                "kind": kind,
                "tool_name": (
                    None
                    if row.tool_name in {"team_dispatch_started", "team_dispatch_finished"}
                    else row.tool_name
                ),
                "status": row.status.value,
                "progress_classification": row.progress_classification,
                "summary": summary,
                "error_code": row.error_code,
                "correction_context_summary": row.correction_context_summary,
                "occurred_at": row.created_at.isoformat(),
            }

        return JSONResponse(
            {
                "revision": state.revision,
                "generated_at": utc_now().isoformat(),
                "retention_days": 30,
                "filters": {
                    key: value
                    for key, value in {
                        "team_id": team_id,
                        "agent_id": agent_id,
                        "dispatch_id": dispatch_id,
                        "tool_name": tool_name,
                        "status": status,
                        "q": search_text,
                    }.items()
                    if value is not None
                },
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(page) < total_count,
                "events": [_event_payload(row) for row in page],
            }
        )

    async def get_view(request: Request) -> JSONResponse:
        view = request.path_params["view"]
        if view not in TEAM_VIEW_IDS:
            return JSONResponse({"error": "view not found"}, status_code=404)
        state = await run_in_threadpool(repository.read)
        return JSONResponse(
            _view_payload_for_request(
                context.auth,
                request,
                state,
                view=view,
                runtime_dir=context.runtime_dir,
                session_repository=_session_repository_from_context(context),
            )
        )

    async def stream_state(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _state_snapshot_events(
                lambda: _state_revision_payload(repository.read_revision()),
                request,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def list_templates(_request: Request) -> JSONResponse:
        return JSONResponse(
            {"templates": [template.model_dump(mode="json") for template in template_catalog()]}
        )

    async def get_template_details(request: Request) -> JSONResponse:
        template = get_template(request.path_params["template_id"])
        if template is None:
            return JSONResponse({"error": "template not found"}, status_code=404)
        return JSONResponse(template.model_dump(mode="json"))

    async def materialize_template(request: Request) -> JSONResponse:
        template_id = request.path_params["template_id"]
        if get_template(template_id) is None:
            return JSONResponse({"error": "template not found"}, status_code=404)
        payload = await _read_payload(request)
        state, result = admin_service.materialize_template(
            template_id,
            inputs=payload,
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            status_code=201,
            extra={"materialized": result},
        )

    async def get_timeline(request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(_timeline_payload_for_request(request, state))

    async def get_costs(request: Request) -> JSONResponse:
        backfill = _query_bool(request, "backfill")
        if backfill is True:
            if locked_response := _privileged_operations_locked_response(context, request):
                return locked_response
        payload = await run_in_threadpool(
            _team_costs_payload,
            context,
            repository,
            backfill_transcripts=backfill is True,
        )
        return JSONResponse(payload)

    async def backfill_costs(request: Request) -> JSONResponse:
        payload = await run_in_threadpool(
            _team_costs_payload,
            context,
            repository,
            backfill_transcripts=True,
        )
        return JSONResponse(payload)

    async def create_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        team_id = _optional(payload, "id") or _optional(payload, "team_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_team(
                team_id=team_id,
                name=_required(payload, "name"),
                manager_agent_id=_optional(payload, "manager_agent_id"),
                manager_session_id=_required(payload, "manager_session_id"),
                manager_display_name=_optional(payload, "manager_display_name"),
                manager_role_id=_optional(payload, "manager_role_id"),
                manager_role_name=_optional(payload, "manager_role_name"),
                operator_communication_target_id=_optional(
                    payload, "operator_communication_target_id"
                ),
                escalate_to_governance=bool(
                    _optional_bool(payload, "escalate_to_governance") or False
                ),
                max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
                cost_warning_ratio=(
                    payload.get("cost_warning_ratio") if "cost_warning_ratio" in payload else None
                ),
                cost_budget_renewal_mode=(
                    payload.get("cost_budget_renewal_mode")
                    if "cost_budget_renewal_mode" in payload
                    else None
                ),
                expected_revision=expected_revision,
            ),
            create_collection="teams",
            create_id=team_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def copy_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        source_team_id = request.path_params["team_id"]
        current_state = repository.read()
        source_team = current_state.teams.get(source_team_id)
        if source_team is None:
            return JSONResponse({"error": "source team not found"}, status_code=404)
        for directory_path in sorted(
            {
                project.directory_path
                for project in current_state.projects.values()
                if project.team_id == source_team_id
                and project.status in {ProjectStatus.ACTIVE, ProjectStatus.BLOCKED}
            }
        ):
            authorization_error = _directory_owner_authorization_error(
                request,
                directory_path=directory_path,
                action="Team copy",
            )
            if authorization_error is not None:
                return authorization_error

        raw_agent_session_ids = payload.get("agent_session_ids")
        if not isinstance(raw_agent_session_ids, Mapping):
            raise ValueError("agent_session_ids must be a JSON object")
        requested_session_ids = {
            _required(raw_agent_session_ids, source_agent_id)
            for source_agent_id in raw_agent_session_ids
        }
        session_records = _session_records_by_id(_session_repository_from_context(context))
        if session_records is None:
            raise ValueError("core session repository is unavailable")
        missing_session_ids = sorted(requested_session_ids - set(session_records))
        if missing_session_ids:
            raise ValueError(
                "core sessions were not found: " + ", ".join(missing_session_ids)
            )

        state, result = admin_service.copy_team(
            source_team_id=source_team_id,
            team_id=_optional(payload, "id") or _optional(payload, "team_id"),
            name=_required(payload, "name"),
            agent_session_ids=raw_agent_session_ids,
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            status_code=201,
            extra={"copied": result},
        )

    async def patch_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_archived = payload.get("archived")
        team_id = request.path_params["team_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_team(
                team_id=team_id,
                name=_optional(payload, "name"),
                manager_agent_id=_optional(payload, "manager_agent_id"),
                operator_communication_target_id=_optional(
                    payload, "operator_communication_target_id"
                ),
                set_operator_communication_target_id="operator_communication_target_id" in payload,
                escalate_to_governance=_optional_bool(payload, "escalate_to_governance"),
                max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
                set_max_cost_usd="max_cost_usd" in payload,
                cost_warning_ratio=(
                    payload.get("cost_warning_ratio") if "cost_warning_ratio" in payload else None
                ),
                set_cost_warning_ratio="cost_warning_ratio" in payload,
                cost_budget_renewal_mode=(
                    payload.get("cost_budget_renewal_mode")
                    if "cost_budget_renewal_mode" in payload
                    else None
                ),
                set_cost_budget_renewal_mode="cost_budget_renewal_mode" in payload,
                archived=raw_archived if isinstance(raw_archived, bool) else None,
                expected_revision=expected_revision,
            ),
            target_collection="teams",
            target_id=team_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def reset_team_cost_budget(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = admin_service.reset_team_cost_budget(
            team_id=request.path_params["team_id"],
            reason=_required(payload, "reason"),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return _state_response(request, state)

    async def create_governance_group(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        governance_group_id = _optional(payload, "id") or _optional(payload, "governance_group_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_governance_group(
                governance_group_id=governance_group_id,
                name=_required(payload, "name"),
                manager_agent_id=_required(payload, "manager_agent_id"),
                member_team_ids=list(_optional_list(payload, "member_team_ids")),
                goals=list(_optional_list(payload, "goals")),
                max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
                expected_revision=expected_revision,
            ),
            create_collection="governance_groups",
            create_id=governance_group_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_governance_group(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_archived = payload.get("archived")
        goals = list(_optional_list(payload, "goals")) if "goals" in payload else None
        governance_group_id = request.path_params["governance_group_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_governance_group(
                governance_group_id=governance_group_id,
                name=_optional(payload, "name"),
                manager_agent_id=_optional(payload, "manager_agent_id"),
                member_team_ids=list(_optional_list(payload, "member_team_ids")),
                set_member_team_ids="member_team_ids" in payload,
                goals=goals,
                max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
                set_max_cost_usd="max_cost_usd" in payload,
                archived=raw_archived if isinstance(raw_archived, bool) else None,
                expected_revision=expected_revision,
            ),
            target_collection="governance_groups",
            target_id=governance_group_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def create_governance_agenda_item(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_priority = _optional(payload, "priority") or TeamInboxPriority.NORMAL.value
        agenda_item_id = _optional(payload, "id") or _optional(payload, "agenda_item_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_governance_agenda_item(
                agenda_item_id=agenda_item_id,
                governance_group_id=_required(payload, "governance_group_id"),
                item_type=GovernanceAgendaItemType(_required(payload, "item_type")),
                title=_required(payload, "title"),
                summary=_required(payload, "summary"),
                team_id=_optional(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                agent_id=_optional(payload, "agent_id"),
                payload=payload.get("payload")
                if isinstance(payload.get("payload"), Mapping)
                else None,
                priority=TeamInboxPriority(raw_priority),
                expected_revision=expected_revision,
            ),
            create_collection="governance_agenda",
            create_id=agenda_item_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def handle_governance_agenda_item(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        agenda_item_id = request.path_params["agenda_item_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.mark_governance_agenda_handled(
                agenda_item_id=agenda_item_id,
                expected_revision=expected_revision,
            ),
            target_collection="governance_agenda",
            target_id=agenda_item_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def create_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        goals = list(_optional_list(payload, "goals"))
        git_config = _optional_git_config(payload)
        directory_path = normalize_directory_path(
            _optional(payload, "directory_path")
            or (git_config.directory_path if git_config is not None else "root")
        )
        authorization_error = _directory_owner_authorization_error(
            request,
            directory_path=directory_path,
            action="Project creation",
        )
        if authorization_error is not None:
            return authorization_error
        release_policy = _resolved_release_policy_for_admin(
            context.auth,
            payload,
            directory_path=directory_path,
        )
        lead_agent_id = _optional(payload, "lead_agent_id")
        lead_session_id = _optional(payload, "lead_session_id")
        if lead_session_id is None:
            lead_agent_id = _required(payload, "lead_agent_id")
        project_id = _optional(payload, "id") or _optional(payload, "project_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_project(
                project_id=project_id,
                team_id=_required(payload, "team_id"),
                name=_required(payload, "name"),
                lead_agent_id=lead_agent_id,
                lead_session_id=lead_session_id,
                lead_display_name=_optional(payload, "lead_display_name"),
                lead_role_id=_optional(payload, "lead_role_id"),
                lead_role_name=_optional(payload, "lead_role_name"),
                goals=goals,
                directory_path=directory_path,
                git=git_config,
                release_policy=release_policy,
                expected_revision=expected_revision,
            ),
            create_collection="projects",
            create_id=project_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        has_git = "git" in payload
        has_directory_path = "directory_path" in payload
        has_release_policy = "release_policy" in payload
        git_config = _optional_git_config(payload)
        has_goals = "goals" in payload
        goals = (
            [ProjectGoal.model_validate(item) for item in _optional_list(payload, "goals")]
            if has_goals
            else None
        )
        raw_status = _optional(payload, "status")
        project_id = request.path_params["project_id"]
        current_project = repository.read().projects.get(project_id)
        effective_git = (
            git_config
            if has_git
            else (current_project.git if current_project is not None else None)
        )
        effective_directory_path = normalize_directory_path(
            _optional(payload, "directory_path")
            or (
                effective_git.directory_path
                if has_git and effective_git is not None
                else (current_project.directory_path if current_project is not None else "root")
            )
        )
        if current_project is None:
            return JSONResponse({"error": "project not found"}, status_code=404)
        authorization_paths = list(
            dict.fromkeys([current_project.directory_path, effective_directory_path])
        )
        for authorization_path in authorization_paths:
            authorization_error = _directory_owner_authorization_error(
                request,
                directory_path=authorization_path,
                action="Project update",
            )
            if authorization_error is not None:
                return authorization_error
        release_policy_payload: Mapping[str, Any] = payload
        re_resolve_existing_release_policy = (
            (has_git or has_directory_path)
            and not has_release_policy
            and current_project is not None
            and current_project.release_policy is not None
        )
        if re_resolve_existing_release_policy:
            release_policy_payload = {
                **payload,
                "release_policy": current_project.release_policy.model_dump(mode="python"),
            }
        release_policy = _resolved_release_policy_for_admin(
            context.auth,
            release_policy_payload,
            directory_path=effective_directory_path,
        )
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_project(
                project_id=project_id,
                name=_optional(payload, "name"),
                directory_path=effective_directory_path,
                set_directory_path=(has_directory_path or (has_git and effective_git is not None)),
                git=git_config,
                set_git=has_git,
                release_policy=release_policy,
                set_release_policy=(has_release_policy or re_resolve_existing_release_policy),
                goals=goals,
                status=ProjectStatus(raw_status) if raw_status else None,
                expected_revision=expected_revision,
            ),
            target_collection="projects",
            target_id=project_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def transfer_project_lead(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        project_id = request.path_params["project_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.transfer_project_lead(
                project_id=project_id,
                lead_agent_id=_required(payload, "lead_agent_id"),
                expected_revision=expected_revision,
            ),
            target_collection="projects",
            target_id=project_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def import_project_git_repo(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        service = getattr(context, "service", None)
        import_git_repo = getattr(service, "import_git_repo", None)
        if not callable(import_git_repo):
            return JSONResponse({"error": "Git repo import is unavailable"}, status_code=503)
        try:
            repo_source = _required(payload, "repo_source")
            directory_path = _optional(payload, "directory_path") or "root"
            imported = await run_in_threadpool(
                lambda: import_git_repo(
                    repo_source=repo_source,
                    directory_path=directory_path,
                )
            )
        except RuntimeError as exc:
            status_code = int(getattr(exc, "status_code", 503))
            return JSONResponse({"error": str(exc)}, status_code=status_code)
        return JSONResponse({"imported": imported, "git": imported.get("git")}, status_code=201)

    async def create_role(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        role_id = _optional(payload, "id") or _optional(payload, "role_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_role(
                role_id=role_id,
                team_id=_required(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                name=_required(payload, "name"),
                description=_optional(payload, "description"),
                permissions=_optional_string_list(payload, "permissions"),
                ephemeral_policy=(
                    EphemeralAgentPolicy.model_validate(ephemeral_policy)
                    if ephemeral_policy is not None
                    else None
                ),
                expected_revision=expected_revision,
            ),
            create_collection="roles",
            create_id=role_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_role(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        raw_archived = payload.get("archived")
        role_id = request.path_params["role_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_role(
                role_id=role_id,
                name=_optional(payload, "name"),
                project_id=_optional(payload, "project_id"),
                set_project_id="project_id" in payload,
                description=_optional_present_text(payload, "description"),
                set_description="description" in payload,
                permissions=(
                    _optional_string_list(payload, "permissions")
                    if "permissions" in payload
                    else None
                ),
                ephemeral_policy=(
                    EphemeralAgentPolicy.model_validate(ephemeral_policy)
                    if ephemeral_policy is not None
                    else None
                ),
                archived=raw_archived if isinstance(raw_archived, bool) else None,
                expected_revision=expected_revision,
            ),
            target_collection="roles",
            target_id=role_id,
        )
        return _state_response(
            request, state, rebased_from_revision=rebased_from_revision, publish_role_queue=True
        )

    async def list_roles(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "roles": [role.model_dump(mode="json") for role in state.roles.values()],
            }
        )

    async def get_role(request: Request) -> JSONResponse:
        state = repository.read()
        role = state.roles.get(request.path_params["role_id"])
        if role is None:
            return JSONResponse({"error": "role not found"}, status_code=404)
        return JSONResponse(role.model_dump(mode="json"))

    async def register_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        agent_id = _optional(payload, "id") or _optional(payload, "agent_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.register_agent(
                agent_id=agent_id,
                team_id=_optional(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                role_id=_optional(payload, "role_id"),
                session_id=_required(payload, "session_id"),
                reports_to_agent_id=_optional(payload, "reports_to_agent_id"),
                display_name=_optional(payload, "display_name"),
                ephemeral_policy=(
                    EphemeralAgentPolicy.model_validate(ephemeral_policy)
                    if ephemeral_policy is not None
                    else None
                ),
                expected_revision=expected_revision,
            ),
            create_collection="agents",
            create_id=agent_id,
        )
        return _state_response(
            request,
            state,
            publish_role_queue=True,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        raw_voice_profile = payload.get("voice_profile")
        raw_status = _optional(payload, "status")
        agent_id = request.path_params["agent_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_agent(
                agent_id=agent_id,
                team_id=_optional(payload, "team_id"),
                set_team_id="team_id" in payload,
                project_id=_optional(payload, "project_id"),
                set_project_id="project_id" in payload,
                role_id=_optional(payload, "role_id"),
                set_role_id="role_id" in payload,
                session_id=_optional(payload, "session_id"),
                reports_to_agent_id=_optional(payload, "reports_to_agent_id"),
                set_reports_to_agent_id="reports_to_agent_id" in payload,
                display_name=_optional(payload, "display_name"),
                set_display_name="display_name" in payload,
                ephemeral_policy=(
                    EphemeralAgentPolicy.model_validate(ephemeral_policy)
                    if ephemeral_policy is not None
                    else None
                ),
                status=AgentStatus(raw_status) if raw_status else None,
                voice_profile=(
                    AgentVoiceProfile.model_validate(raw_voice_profile)
                    if raw_voice_profile is not None
                    else None
                ),
                set_voice_profile="voice_profile" in payload,
                context_clearing_policy=_optional(payload, "context_clearing_policy"),
                set_context_clearing_policy="context_clearing_policy" in payload,
                context_clearing_idle_grace_seconds=_optional(
                    payload,
                    "context_clearing_idle_grace_seconds",
                ),
                set_context_clearing_idle_grace_seconds=(
                    "context_clearing_idle_grace_seconds" in payload
                ),
                auto_context_clearing_enabled=_optional_bool(
                    payload,
                    "auto_context_clearing_enabled",
                ),
                set_auto_context_clearing_enabled=("auto_context_clearing_enabled" in payload),
                expected_revision=expected_revision,
            ),
            target_collection="agents",
            target_id=agent_id,
        )
        idle_wake_result: dict[str, Any] = {"events": []}
        if (
            "context_clearing_policy" in payload
            or "context_clearing_idle_grace_seconds" in payload
            or "auto_context_clearing_enabled" in payload
        ):
            _append_idle_context_rotation_wake_requested_event(
                state,
                agent_id=agent_id,
                result=idle_wake_result,
                now=datetime.now(UTC),
            )
        published_event_ids = _publish_team_events(
            context,
            idle_wake_result.get("events"),
        )
        return _state_response(
            request,
            state,
            publish_role_queue=True,
            rebased_from_revision=rebased_from_revision,
            extra=({"published_event_ids": published_event_ids} if published_event_ids else None),
        )

    async def create_avatar_preview(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.read()
        agent_id = request.path_params["agent_id"]
        agent = state.agents.get(agent_id)
        if agent is None:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        role = state.roles.get(agent.role_id) if agent.role_id is not None else None
        team = state.teams.get(agent.team_id) if agent.team_id is not None else None
        style_preset = normalize_style_preset(payload.get("style_preset"))
        prompt_note = normalize_prompt_note(payload.get("prompt_note"))
        prompt = build_avatar_prompt(
            agent=agent,
            role_name=role.name if role is not None else None,
            team_name=team.name if team is not None else None,
            style_preset=style_preset,
            prompt_note=prompt_note,
        )
        generator = openai_avatar_generator_from_config(context.config)
        try:
            content = await generator.generate(prompt=prompt)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        try:
            preview = avatar_storage.store_generated_preview(
                agent_id=agent.id,
                content=content,
                style_preset=style_preset,
                prompt_note=prompt_note,
                model=generator.model,
            )
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse({"preview": preview.payload()}, status_code=201)

    async def upload_agent_avatar(request: Request) -> JSONResponse:
        try:
            form = await request.form()
        except Exception as exc:
            raise ValueError("Request body must be multipart form data") from exc
        expected_revision = _coerce_int(form.get("expected_revision"), "expected_revision")
        file_value = form.get("file")
        if not isinstance(file_value, UploadFile):
            raise ValueError("file is required")
        content = await file_value.read(MAX_AVATAR_UPLOAD_BYTES + 1)
        try:
            avatar = avatar_storage.store_upload(content)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        old_asset_id = _current_agent_avatar_asset_id(
            repository.read(),
            request.path_params["agent_id"],
        )
        try:
            state = admin_service.set_agent_avatar(
                agent_id=request.path_params["agent_id"],
                avatar=avatar,
                expected_revision=expected_revision,
            )
        except Exception:
            avatar_storage.delete_asset_if_unreferenced(
                asset_id=avatar.asset_id,
                state=repository.read(),
            )
            raise
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return _state_response(request, state)

    async def accept_agent_avatar_preview(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        expected_revision = _expected_revision(payload)
        agent_id = request.path_params["agent_id"]
        before = repository.read()
        if agent_id not in before.agents:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        if before.revision != expected_revision:
            raise TeamStateConflictError(
                expected_revision=expected_revision,
                current_revision=before.revision,
            )
        old_asset_id = _current_agent_avatar_asset_id(before, agent_id)
        try:
            avatar = avatar_storage.avatar_from_preview(
                preview_id=_required(payload, "preview_id"),
                agent_id=agent_id,
            )
        except FileNotFoundError:
            return JSONResponse({"error": "avatar preview not found"}, status_code=404)
        state = admin_service.set_agent_avatar(
            agent_id=agent_id,
            avatar=avatar,
            expected_revision=expected_revision,
        )
        avatar_storage.promote_preview(preview_id=avatar.preview_id or avatar.asset_id)
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return _state_response(request, state)

    async def delete_agent_avatar(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        old_asset_id = _current_agent_avatar_asset_id(
            repository.read(),
            request.path_params["agent_id"],
        )
        state = admin_service.clear_agent_avatar(
            agent_id=request.path_params["agent_id"],
            expected_revision=_expected_revision(payload),
        )
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return _state_response(request, state)

    async def list_conversations(request: Request) -> JSONResponse:
        state = await run_in_threadpool(_reconciled_chat_state, repository)
        scope = _chat_list_scope(context.auth, request, state)
        scope = _add_team_chat_mutation_modes(
            scope,
            auth=context.auth,
            service=getattr(context, "service", None),
            session_repository=_session_repository_from_context(context),
            request=request,
            state=state,
        )
        return JSONResponse(
            _conversation_collection_payload(
                state,
                scope=scope,
                selected_conversation_id=_query_text(request, "selected_conversation_id"),
                include_selected_messages=bool(_query_bool(request, "include_selected_messages")),
            )
        )

    async def create_conversation(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        identity = _operator_identity_from_request(context.auth, request)
        # A chat is resolved for the current operator and agent, rather than applying
        # a client-side state snapshot. Do not reject it when unrelated Team data moved.
        state, conversation, created = chat_service.create_or_get_conversation(
            agent_id=_required(payload, "agent_id"),
            operator_user_id=identity["user_id"],
            operator_display_name=identity["display_name"],
            created_by=identity["actor"],
        )
        scope = _chat_scope_for_operator(context.auth, request, state, identity["user_id"])
        scope = _add_team_chat_mutation_modes(
            scope,
            auth=context.auth,
            service=getattr(context, "service", None),
            session_repository=_session_repository_from_context(context),
            request=request,
            state=state,
        )
        response_payload = _conversation_collection_payload(
            state,
            scope=scope,
            selected_conversation_id=conversation.id,
            include_selected_messages=True,
        )
        response_payload["created"] = created
        return JSONResponse(response_payload, status_code=201 if created else 200)

    async def get_conversation(request: Request) -> JSONResponse:
        state = await run_in_threadpool(_reconciled_chat_state, repository)
        conversation_id = request.path_params["conversation_id"]
        conversation = state.conversations.get(conversation_id)
        if conversation is None:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        scope = _require_conversation_chat_scope(
            context.auth,
            request,
            state,
            conversation,
            write=False,
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "conversation": _conversation_payload(
                    state,
                    conversation,
                    include_messages=True,
                    current_operator_user_id=scope["current_operator"]["user_id"],
                    mutation_mode=(
                        _team_chat_mutation_authorization_for_request(
                            context,
                            request,
                            state,
                            conversation,
                            principal_id=scope["current_operator"]["user_id"],
                        ).mode
                        if conversation.operator_user_id == scope["current_operator"]["user_id"]
                        else "reply_only"
                    ),
                ),
                **_chat_scope_payload(state, scope),
            }
        )

    async def list_conversation_messages(request: Request) -> JSONResponse:
        state = await run_in_threadpool(_reconciled_chat_state, repository)
        conversation_id = request.path_params["conversation_id"]
        conversation = state.conversations.get(conversation_id)
        if conversation is None:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        scope = _require_conversation_chat_scope(
            context.auth,
            request,
            state,
            conversation,
            write=False,
        )
        return JSONResponse(
            _conversation_message_page_payload(
                state,
                conversation,
                current_operator_user_id=scope["current_operator"]["user_id"],
                before_message_id=_query_text(request, "before"),
                limit=_query_chat_message_limit(request),
            )
        )

    async def stream_conversation(request: Request) -> StreamingResponse | JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        try:
            initial_snapshot = await run_in_threadpool(
                _conversation_snapshot_state_and_payload,
                repository,
                conversation_id,
                auth=context.auth,
                request=request,
                context=context,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return StreamingResponse(
            _conversation_snapshot_events(
                repository,
                request,
                conversation_id,
                auth=context.auth,
                context=context,
                initial_snapshot=initial_snapshot,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def send_conversation_message(request: Request) -> JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        before = repository.read()
        conversation = before.conversations.get(conversation_id)
        if conversation is None:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        identity = _operator_identity_from_request(context.auth, request)
        _require_conversation_chat_scope(
            context.auth,
            request,
            before,
            conversation,
            write=True,
            identity=identity,
        )
        mutation_authorization = _team_chat_mutation_authorization_for_request(
            context,
            request,
            before,
            conversation,
            principal_id=identity["user_id"],
        )
        prepared_attachments = []
        content_type = str(request.headers.get("content-type") or "").lower()
        if content_type.startswith("multipart/form-data"):
            try:
                form = await request.form()
            except Exception as exc:
                raise ValueError("Request body must be multipart form data") from exc
            body = _optional({"body": form.get("body")}, "body")
            files = [
                value
                for key, value in form.multi_items()
                if key == "files" and isinstance(value, UploadFile)
            ]
            directory_path = _conversation_agent_directory_path(
                context,
                before,
                conversation,
            )
            policy = chat_attachment_policy_service.effective_policy(
                directory_path,
                state=before,
            )
            validate_attachment_count(policy, len(files))
            try:
                for file_value in files:
                    max_bytes = int(policy.max_attachment_bytes or 0)
                    content = await file_value.read(max_bytes + 1 if max_bytes is not None else -1)
                    validate_attachment_policy_content(
                        policy,
                        direction="operator_to_agent",
                        filename=file_value.filename or "attachment",
                        mime_type=file_value.content_type,
                        byte_count=len(content),
                    )
                    prepared_attachments.append(
                        chat_attachment_storage.prepare_upload(
                            content=content,
                            filename=file_value.filename or "attachment",
                            mime_type=file_value.content_type,
                        )
                    )
            except Exception:
                chat_attachment_storage.delete_prepared(
                    attachment.id for attachment in prepared_attachments
                )
                raise
        else:
            payload = await _read_payload(request)
            body = _optional(payload, "body")
        # Chat messages are append-only. Persist the message and its queued dispatch in one
        # update, so a send does not need two full Team-state transactions. The service validates
        # the current conversation and agent state, so a stale global revision must not block it.
        try:
            state, message, dispatch_id = chat_service.record_and_queue_operator_chat_message(
                conversation_id=conversation_id,
                body=body,
                attachments=prepared_attachments,
                created_by=identity["actor"],
                mutation_authorization=mutation_authorization,
                reason="queued by Team chat API for asynchronous dispatch",
            )
        except Exception:
            chat_attachment_storage.delete_prepared(
                attachment.id for attachment in prepared_attachments
            )
            raise
        try:
            state, message, steering = queue_operator_chat_steering(
                repository,
                message_id=message.id,
            )
        except Exception:
            steering = None
        dispatch_payload: dict[str, Any] = {
            "success": False,
            "queued": True,
            "dispatch_id": dispatch_id,
            "message": "Chat message queued for asynchronous agent dispatch.",
        }
        if steering is not None:
            dispatch_payload["delivery"] = "next_model_call"
        conversation = state.conversations[conversation_id]
        agent = state.agents[conversation.agent_id]
        published_event_ids = _publish_team_events(
            context,
            [
                {
                    "event_type": CHAT_MESSAGE_QUEUED_EVENT_TYPE,
                    "correlation_key": dispatch_id,
                    "idempotency_key": f"team-chat-message-queued:{message.id}",
                    "payload": {
                        "team_id": agent.team_id,
                        "agent_id": agent.id,
                        "session_id": agent.session_id,
                        "conversation_id": conversation.id,
                        "message_id": message.id,
                        "dispatch_id": dispatch_id,
                    },
                }
            ],
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "conversation": _conversation_payload(
                    state,
                    conversation,
                    include_messages=True,
                    current_operator_user_id=identity["user_id"],
                    mutation_mode=mutation_authorization.mode,
                ),
                **_chat_scope_payload(
                    state,
                    _chat_scope_for_operator(
                        context.auth,
                        request,
                        state,
                        conversation.operator_user_id,
                        identity=identity,
                    ),
                ),
                "message": _conversation_message_payload(message, state),
                "dispatch": dispatch_payload,
                "published_event_ids": published_event_ids,
            },
            status_code=202,
        )

    async def get_chat_attachment_policy(request: Request) -> JSONResponse:
        state = repository.read()
        directory_path = normalize_directory_path(request.query_params.get("directory_path"))
        override = state.chat_attachment_policies.get(directory_path)
        effective = chat_attachment_policy_service.effective_policy(directory_path, state=state)
        return JSONResponse(
            {
                "revision": state.revision,
                "directory_path": directory_path,
                "override": override.model_dump(mode="json") if override is not None else None,
                "effective": effective.model_dump(mode="json"),
            }
        )

    async def put_chat_attachment_policy(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        directory_path = normalize_directory_path(
            payload.get("directory_path") or request.query_params.get("directory_path")
        )
        values = payload.get("policy")
        if not isinstance(values, Mapping):
            raise ValueError("policy must be a JSON object")
        identity = _operator_identity_from_request(context.auth, request)
        state = chat_attachment_policy_service.set_override(
            directory_path=directory_path,
            values=values,
            updated_by=identity["actor"],
            expected_revision=_expected_revision(payload),
        )
        effective = chat_attachment_policy_service.effective_policy(directory_path, state=state)
        return JSONResponse(
            {
                "revision": state.revision,
                "directory_path": directory_path,
                "override": state.chat_attachment_policies[directory_path].model_dump(mode="json"),
                "effective": effective.model_dump(mode="json"),
            }
        )

    async def get_chat_attachment(request: Request) -> FileResponse | JSONResponse:
        state = repository.read()
        conversation_id = request.path_params["conversation_id"]
        attachment_id = request.path_params["attachment_id"]
        conversation = state.conversations.get(conversation_id)
        if conversation is None:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        _require_conversation_chat_scope(context.auth, request, state, conversation, write=False)
        attachment = state.chat_attachments.get(attachment_id)
        if attachment is None or attachment.conversation_id != conversation_id:
            return JSONResponse({"error": "attachment not found"}, status_code=404)
        try:
            path = chat_attachment_storage.asset_path(attachment.id)
        except ValueError:
            return JSONResponse({"error": "attachment not found"}, status_code=404)
        if not path.is_file():
            return JSONResponse({"error": "attachment file not found"}, status_code=404)
        return FileResponse(
            path,
            media_type=attachment.mime_type or "application/octet-stream",
            filename=attachment.filename,
            content_disposition_type=(
                "inline"
                if attachment.mime_type in {"image/png", "image/jpeg", "image/gif", "image/webp"}
                else "attachment"
            ),
        )

    async def create_agent_voice_session(request: Request) -> JSONResponse:
        payload = await _read_optional_payload(request)
        state = repository.read()
        agent_id = request.path_params["agent_id"]
        agent = state.agents.get(agent_id)
        if agent is None:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        if _enum_value(agent.status) == AgentStatus.ARCHIVED.value:
            return JSONResponse({"error": "archived agents cannot be called"}, status_code=403)
        identity = _operator_identity_from_request(context.auth, request)
        conversation_id = _optional(payload, "conversation_id")
        conversation = None
        if conversation_id is not None:
            conversation = state.conversations.get(conversation_id)
            if conversation is None:
                return JSONResponse({"error": "conversation not found"}, status_code=404)
            _require_conversation_chat_scope(
                context.auth,
                request,
                state,
                conversation,
                write=True,
                identity=identity,
            )
            if conversation.agent_id != agent.id:
                return JSONResponse(
                    {"error": "conversation belongs to a different agent"},
                    status_code=400,
                )
        runtime = _runtime_from_context(context)
        session = runtime.session_repository.get_session(agent.session_id)
        if session is None:
            return JSONResponse({"error": "agent session not found"}, status_code=404)
        if _session_is_expired(session):
            return JSONResponse({"error": "agent session is expired"}, status_code=403)
        resolved_config = runtime.resolve_provider_config_for_session(session.session_id)
        request_id = _optional(payload, "request_id")
        existing_record = (
            runtime.voice_session_store.get_by_request_id(request_id)
            if request_id is not None
            else None
        )
        voice_profile = _agent_voice_profile_payload(agent)
        if existing_record is not None:
            if (
                existing_record.origin_session_id != session.session_id
                or existing_record.directory_path != session.directory_path
                or existing_record.channel != "browser_agent_call"
                or existing_record.contact_id != agent.id
                or existing_record.caller_metadata.get("conversation_id") != conversation_id
            ):
                return JSONResponse(
                    {"error": ("Voice session request_id was already used with different inputs")},
                    status_code=400,
                )
            _require_browser_voice_session_operator(
                context.auth,
                request,
                existing_record,
            )
            existing_voice_profile = existing_record.prompt_metadata.get("voice_profile")
            if not isinstance(existing_voice_profile, Mapping):
                existing_voice_profile = voice_profile
            existing_prompt = str(existing_record.projection.get("prompt") or "")
            effective_prompt = _agent_voice_effective_prompt(
                existing_prompt,
                voice_profile=existing_voice_profile,
            )
            try:
                client_secret = OpenAIRealtimeClientSecretService(
                    config=resolved_config,
                ).create_client_secret(
                    instructions=effective_prompt,
                    safety_identifier=f"team-addon:{agent.id}",
                    include_query_origin_agent=payload.get("include_query_origin_agent")
                    is not False,
                    voice=existing_voice_profile.get("voice"),
                )
            except (RuntimeError, ProviderRequestError) as exc:
                runtime.voice_session_store.complete_session(
                    existing_record.voice_session_id,
                    status="failed",
                    error=str(exc),
                )
                return JSONResponse({"error": str(exc)}, status_code=503)
            if existing_record.status == "created":
                existing_record = runtime.voice_session_store.attach_bridge_session(
                    existing_record.voice_session_id,
                    bridge_session_id=None,
                    status="connecting",
                    realtime_metadata={
                        "provider": "openai_realtime",
                        "transport": "webrtc_browser",
                        "model": getattr(
                            resolved_config,
                            "openai_realtime_model",
                            "gpt-realtime-2",
                        ),
                        "call_surface": (
                            "team_chat_tab" if conversation is not None else "team_people_tab"
                        ),
                        "voice": existing_voice_profile.get("voice"),
                        "speaking_style": existing_voice_profile.get("speaking_style"),
                    },
                )
            conversation_message = None
            if conversation is not None:
                state, conversation_message = chat_service.record_conversation_voice_call(
                    conversation_id=conversation.id,
                    voice_session_id=existing_record.voice_session_id,
                    status="running",
                    summary=f"Voice call with {_agent_display_name(agent)}",
                )
            return JSONResponse(
                {
                    "revision": state.revision,
                    **_team_voice_session_payload(existing_record),
                    "created": False,
                    "projection": existing_record.projection,
                    "conversation": (
                        _conversation_payload(
                            state,
                            state.conversations[conversation.id],
                            include_messages=True,
                            current_operator_user_id=identity["user_id"],
                        )
                        if conversation is not None
                        else None
                    ),
                    "conversation_message": (
                        _conversation_message_payload(conversation_message, state)
                        if conversation_message is not None
                        else None
                    ),
                    "client_secret": client_secret,
                    "realtime": {
                        "provider": "openai_realtime",
                        "transport": "webrtc_browser",
                        "calls_url": _realtime_calls_url(resolved_config),
                        "model": getattr(
                            resolved_config,
                            "openai_realtime_model",
                            "gpt-realtime-2",
                        ),
                        "voice": existing_voice_profile.get("voice"),
                        "speaking_style": existing_voice_profile.get("speaking_style"),
                    },
                },
                status_code=200,
            )
        try:
            tool_summary = runtime.policy.allowed_tool_summary_for_session(session)
        except Exception:
            tool_summary = None
        projection = _build_agent_voice_projection(
            state=state,
            agent=agent,
            session=session,
            tool_summary=tool_summary,
        )
        prompt = str(projection["prompt"])
        effective_prompt = _agent_voice_effective_prompt(
            prompt,
            voice_profile=voice_profile,
        )
        prompt_metadata = _agent_voice_prompt_metadata(
            state=state,
            agent=agent,
            session=session,
            prompt=prompt,
            effective_prompt=effective_prompt,
            voice_profile=voice_profile,
        )
        record, created = runtime.voice_session_store.prepare_session(
            origin_session_id=session.session_id,
            directory_path=session.directory_path,
            channel="browser_agent_call",
            request_id=request_id,
            prompt_metadata=prompt_metadata,
            projection=projection,
            caller_metadata={
                "call_surface": "team_chat_tab" if conversation is not None else "team_people_tab",
                "agent_id": agent.id,
                "team_id": agent.team_id,
                "project_ids": agent_project_ids(state, agent.id),
                "conversation_id": conversation_id,
                "operator_user_id": identity["user_id"],
                "operator_display_name": identity["display_name"],
                "voice_profile": voice_profile,
            },
            contact_id=agent.id,
            contact_display_name=_agent_display_name(agent),
        )
        try:
            client_secret = OpenAIRealtimeClientSecretService(
                config=resolved_config,
            ).create_client_secret(
                instructions=effective_prompt,
                safety_identifier=f"team-addon:{agent.id}",
                include_query_origin_agent=payload.get("include_query_origin_agent") is not False,
                voice=voice_profile.get("voice"),
            )
        except (RuntimeError, ProviderRequestError) as exc:
            runtime.voice_session_store.complete_session(
                record.voice_session_id,
                status="failed",
                error=str(exc),
            )
            return JSONResponse({"error": str(exc)}, status_code=503)
        record = runtime.voice_session_store.attach_bridge_session(
            record.voice_session_id,
            bridge_session_id=None,
            status="connecting",
            realtime_metadata={
                "provider": "openai_realtime",
                "transport": "webrtc_browser",
                "model": getattr(resolved_config, "openai_realtime_model", "gpt-realtime-2"),
                "call_surface": "team_chat_tab" if conversation is not None else "team_people_tab",
                "voice": voice_profile.get("voice"),
                "speaking_style": voice_profile.get("speaking_style"),
            },
        )
        conversation_message = None
        if conversation is not None:
            state, conversation_message = chat_service.record_conversation_voice_call(
                conversation_id=conversation.id,
                voice_session_id=record.voice_session_id,
                status="running",
                summary=f"Voice call with {_agent_display_name(agent)}",
            )
        return JSONResponse(
            {
                "revision": state.revision,
                **_team_voice_session_payload(record),
                "created": created,
                "projection": projection,
                "conversation": (
                    _conversation_payload(
                        state,
                        state.conversations[conversation.id],
                        include_messages=True,
                        current_operator_user_id=identity["user_id"],
                    )
                    if conversation is not None
                    else None
                ),
                "conversation_message": (
                    _conversation_message_payload(conversation_message, state)
                    if conversation_message is not None
                    else None
                ),
                "client_secret": client_secret,
                "realtime": {
                    "provider": "openai_realtime",
                    "transport": "webrtc_browser",
                    "calls_url": _realtime_calls_url(resolved_config),
                    "model": getattr(
                        resolved_config,
                        "openai_realtime_model",
                        "gpt-realtime-2",
                    ),
                    "voice": voice_profile.get("voice"),
                    "speaking_style": voice_profile.get("speaking_style"),
                },
            },
            status_code=201 if created else 200,
        )

    async def record_agent_voice_event(request: Request) -> JSONResponse:
        runtime = _runtime_from_context(context)
        voice_session_id = request.path_params["voice_session_id"]
        record = runtime.voice_session_store.get_by_voice_session_id(voice_session_id)
        if record is None or record.channel != "browser_agent_call":
            return JSONResponse({"error": "voice session not found"}, status_code=404)
        _require_browser_voice_session_operator(context.auth, request, record)
        payload = await _read_payload(request)
        updated = record
        event_type = _optional(payload, "event_type")
        event_metadata = (
            payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
        )
        transcript_text = _optional(payload, "text")
        transcript_speaker_role = _optional(payload, "speaker")
        if transcript_text:
            updated = (
                runtime.voice_session_store.append_transcript_event(
                    voice_session_id,
                    text=transcript_text,
                    speaker=_browser_voice_transcript_speaker_label(
                        record,
                        transcript_speaker_role,
                    ),
                    metadata={
                        "event_type": event_type or "browser_call.transcript",
                        **dict(event_metadata),
                        "speaker_role": transcript_speaker_role,
                    },
                )
                or updated
            )
        raw_status = _optional(payload, "status")
        if raw_status:
            status = normalize_voice_session_status(raw_status, default=updated.status)
            if status in {"ended", "failed"}:
                reconciliation_context = _browser_voice_reconciliation_context(
                    state=repository.read(),
                    runtime=runtime,
                    record=record,
                )
                updated = runtime.voice_session_store.complete_session(
                    voice_session_id,
                    status=status,
                    transcript=_browser_voice_reconciliation_transcript(
                        record,
                        _optional(payload, "transcript"),
                    ),
                    summary=_optional(payload, "summary"),
                    decisions=_optional_string_list(payload, "decisions")
                    if "decisions" in payload
                    else None,
                    action_items=_optional_string_list(payload, "action_items")
                    if "action_items" in payload
                    else None,
                    reconciliation_context=reconciliation_context,
                    error=_optional(payload, "error"),
                )
                _record_chat_voice_call_status(chat_service, updated)
                runtime.voice_session_reconciler.schedule_reconciliation(voice_session_id)
            else:
                updated = runtime.voice_session_store.attach_bridge_session(
                    voice_session_id,
                    bridge_session_id=None,
                    status=status,
                    realtime_metadata={
                        "last_browser_event": event_type or "browser_call.status",
                        **dict(event_metadata),
                    },
                )
        return JSONResponse(_team_voice_session_payload(updated))

    async def end_agent_voice_session(request: Request) -> JSONResponse:
        runtime = _runtime_from_context(context)
        voice_session_id = request.path_params["voice_session_id"]
        record = runtime.voice_session_store.get_by_voice_session_id(voice_session_id)
        if record is None or record.channel != "browser_agent_call":
            return JSONResponse({"error": "voice session not found"}, status_code=404)
        _require_browser_voice_session_operator(context.auth, request, record)
        payload = await _read_optional_payload(request)
        status = normalize_voice_session_status(payload.get("status"), default="ended")
        if status not in {"ended", "failed"}:
            raise ValueError("status must be ended or failed")
        reconciliation_context = _browser_voice_reconciliation_context(
            state=repository.read(),
            runtime=runtime,
            record=record,
        )
        updated = runtime.voice_session_store.complete_session(
            voice_session_id,
            status=status,
            transcript=_browser_voice_reconciliation_transcript(
                record,
                _optional(payload, "transcript"),
            ),
            summary=_optional(payload, "summary"),
            decisions=_optional_string_list(payload, "decisions")
            if "decisions" in payload
            else None,
            action_items=_optional_string_list(payload, "action_items")
            if "action_items" in payload
            else None,
            reconciliation_context=reconciliation_context,
            error=_optional(payload, "error"),
        )
        _record_chat_voice_call_status(chat_service, updated)
        reconciled = runtime.voice_session_reconciler.schedule_reconciliation(voice_session_id)
        return JSONResponse(_team_voice_session_payload(reconciled or updated))

    async def end_agent_voice_session_by_request(request: Request) -> JSONResponse:
        runtime = _runtime_from_context(context)
        request_id = request.path_params["request_id"]
        record = runtime.voice_session_store.get_by_request_id(request_id)
        if record is None or record.channel != "browser_agent_call":
            return JSONResponse({"error": "voice session not found"}, status_code=404)
        _require_browser_voice_session_operator(context.auth, request, record)
        payload = await _read_optional_payload(request)
        status = normalize_voice_session_status(payload.get("status"), default="ended")
        if status not in {"ended", "failed"}:
            raise ValueError("status must be ended or failed")
        reconciliation_context = _browser_voice_reconciliation_context(
            state=repository.read(),
            runtime=runtime,
            record=record,
        )
        updated = runtime.voice_session_store.complete_session(
            record.voice_session_id,
            status=status,
            transcript=_browser_voice_reconciliation_transcript(
                record,
                _optional(payload, "transcript"),
            ),
            summary=_optional(payload, "summary"),
            decisions=_optional_string_list(payload, "decisions")
            if "decisions" in payload
            else None,
            action_items=_optional_string_list(payload, "action_items")
            if "action_items" in payload
            else None,
            reconciliation_context=reconciliation_context,
            error=_optional(payload, "error"),
        )
        _record_chat_voice_call_status(chat_service, updated)
        reconciled = runtime.voice_session_reconciler.schedule_reconciliation(
            record.voice_session_id
        )
        return JSONResponse(_team_voice_session_payload(reconciled or updated))

    async def create_agent_voice_query(request: Request) -> JSONResponse:
        runtime = _runtime_from_context(context)
        voice_session_id = request.path_params["voice_session_id"]
        record = runtime.voice_session_store.get_by_voice_session_id(voice_session_id)
        if record is None or record.channel != "browser_agent_call":
            return JSONResponse({"error": "voice session not found"}, status_code=404)
        _require_browser_voice_session_operator(context.auth, request, record)
        payload = await _read_payload(request)
        query = VoiceQueryDelegator(
            store=runtime.voice_session_store,
            wake_store=runtime.wake_store,
        ).create_query_and_wake_origin(
            voice_session_id,
            question=_required(payload, "question"),
            transcript_excerpt=_browser_voice_reconciliation_transcript(
                record,
                _optional(payload, "transcript_excerpt"),
            ),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            deadline_seconds=_optional_int(payload, "deadline_seconds")
            if "deadline_seconds" in payload
            else None,
        )
        return JSONResponse(voice_query_payload(query), status_code=201)

    async def get_agent_voice_query(request: Request) -> JSONResponse:
        runtime = _runtime_from_context(context)
        voice_session_id = request.path_params["voice_session_id"]
        record = runtime.voice_session_store.get_by_voice_session_id(voice_session_id)
        if record is None or record.channel != "browser_agent_call":
            return JSONResponse({"error": "voice session not found"}, status_code=404)
        _require_browser_voice_session_operator(context.auth, request, record)
        query = runtime.voice_session_store.get_query_for_voice_session(
            voice_session_id,
            request.path_params["query_id"],
        )
        if query is None:
            return JSONResponse({"error": "voice query not found"}, status_code=404)
        return JSONResponse(voice_query_payload(query))

    async def timeout_agent_voice_query(request: Request) -> JSONResponse:
        runtime = _runtime_from_context(context)
        voice_session_id = request.path_params["voice_session_id"]
        record = runtime.voice_session_store.get_by_voice_session_id(voice_session_id)
        if record is None or record.channel != "browser_agent_call":
            return JSONResponse({"error": "voice session not found"}, status_code=404)
        _require_browser_voice_session_operator(context.auth, request, record)
        query = runtime.voice_session_store.get_query_for_voice_session(
            voice_session_id,
            request.path_params["query_id"],
        )
        if query is None:
            return JSONResponse({"error": "voice query not found"}, status_code=404)
        payload = await _read_optional_payload(request)
        query = runtime.voice_session_store.mark_query_timeout(
            query.query_id,
            error=_optional(payload, "error"),
        )
        return JSONResponse(voice_query_payload(query))

    async def get_avatar_asset(request: Request) -> FileResponse | JSONResponse:
        try:
            path = avatar_storage.asset_path(request.path_params["asset_id"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "avatar not found"}, status_code=404)
        return FileResponse(path, media_type=AVATAR_MIME_TYPE)

    async def get_avatar_preview(request: Request) -> FileResponse | JSONResponse:
        try:
            path = avatar_storage.preview_path(request.path_params["preview_id"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "avatar preview not found"}, status_code=404)
        return FileResponse(path, media_type=AVATAR_MIME_TYPE)

    async def request_agent_context_rotation(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        agent_id = request.path_params["agent_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: runtime_service.request_agent_context_rotation(
                agent_id=agent_id,
                requested_by=_optional(payload, "requested_by")
                or _operator_actor_from_request(context.auth, request),
                request_note=_optional(payload, "reason"),
                expected_revision=expected_revision,
            ),
            target_collection="agents",
            target_id=agent_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def stand_down_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        agent_id = request.path_params["agent_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: runtime_service.stand_down_agent(
                agent_id=agent_id,
                reason=_required(payload, "reason"),
                set_by=_optional(payload, "set_by")
                or _operator_actor_from_request(context.auth, request),
                scope=_optional(payload, "scope") or "subtree",
                expected_revision=expected_revision,
            ),
            target_collection="agents",
            target_id=agent_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def revoke_agent_stand_down(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        agent_id = request.path_params["agent_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: runtime_service.revoke_stand_downs(
                agent_id=agent_id,
                revoked_by=_optional(payload, "revoked_by")
                or _operator_actor_from_request(context.auth, request),
                expected_revision=expected_revision,
            ),
            target_collection="agents",
            target_id=agent_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def create_case(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        case_id = _optional(payload, "id") or _optional(payload, "case_id")
        raw_status = _optional(payload, "status")
        raw_priority = _optional(payload, "priority")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_case(
                case_id=case_id,
                team_id=_required(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                title=_required(payload, "title"),
                description=_optional(payload, "description") or "",
                status=TeamCaseStatus(raw_status) if raw_status else TeamCaseStatus.OPEN,
                priority=(
                    TeamInboxPriority(raw_priority) if raw_priority else TeamInboxPriority.NORMAL
                ),
                owner_agent_id=_optional(payload, "owner_agent_id"),
                source=_optional(payload, "source"),
                source_ref=_optional(payload, "source_ref"),
                tags=_optional_string_list(payload, "tags") or [],
                metadata=_optional_object(payload, "metadata") or {},
                task_ids=_optional_string_list(payload, "task_ids") or [],
                expected_revision=expected_revision,
            ),
            create_collection="cases",
            create_id=case_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_case(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        case_id = request.path_params["case_id"]
        raw_status = _optional(payload, "status")
        raw_priority = _optional(payload, "priority")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_case(
                case_id=case_id,
                title=_optional(payload, "title"),
                description=_optional_present_text(payload, "description"),
                set_description="description" in payload,
                project_id=_optional_present_text(payload, "project_id"),
                set_project_id="project_id" in payload,
                status=TeamCaseStatus(raw_status) if raw_status else None,
                priority=TeamInboxPriority(raw_priority) if raw_priority else None,
                owner_agent_id=_optional_present_text(payload, "owner_agent_id"),
                set_owner_agent_id="owner_agent_id" in payload,
                source=_optional_present_text(payload, "source"),
                set_source="source" in payload,
                source_ref=_optional_present_text(payload, "source_ref"),
                set_source_ref="source_ref" in payload,
                tags=_optional_string_list(payload, "tags"),
                metadata=(_optional_object(payload, "metadata") if "metadata" in payload else None),
                task_ids=_optional_string_list(payload, "task_ids"),
                expected_revision=expected_revision,
            ),
            target_collection="cases",
            target_id=case_id,
        )
        closure_outbox_ids = [
            record.id
            for record in repository.list_outbox_records(
                status=TeamOutboxStatus.PENDING,
                intent_type=TEAM_CASE_CLOSED_EVENT_TYPE,
                aggregate_id=case_id,
            )
        ]
        publication_wake = request_team_outbox_publication_for_records(
            context,
            outbox_record_ids=closure_outbox_ids,
        )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra=(
                {"outbox_publication_wake": publication_wake}
                if publication_wake is not None
                else None
            ),
        )

    async def create_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        created_by_agent_id = _optional(payload, "created_by_agent_id")
        owner_agent_id = _required(payload, "owner_agent_id")
        task_id = _optional(payload, "id") or _optional(payload, "task_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_or_reprioritize_task(
                task_id=task_id,
                owner_agent_id=owner_agent_id,
                project_id=_required(payload, "project_id"),
                case_id=_optional(payload, "case_id"),
                set_case_id="case_id" in payload,
                priority=_required_int(payload, "priority"),
                title=_required(payload, "title"),
                description=_required(payload, "description"),
                created_by_agent_id=created_by_agent_id,
                created_by_actor=(
                    None
                    if created_by_agent_id is not None
                    else _operator_actor_from_request(context.auth, request)
                ),
                state=TeamTaskState(raw_state) if raw_state else TeamTaskState.PENDING,
                parent_task_id=_optional(payload, "parent_task_id"),
                originating_task_id=_optional(payload, "originating_task_id"),
                authored_by=_optional_object(payload, "authored_by"),
                set_authored_by="authored_by" in payload,
                git_base_branch=_optional(payload, "git_base_branch"),
                git_base_commit_sha=_optional(payload, "git_base_commit_sha"),
                set_git_base_branch="git_base_branch" in payload,
                set_parent_task_id="parent_task_id" in payload,
                dependency_task_ids=_optional_string_list(payload, "dependency_task_ids"),
                task_kind=_optional(payload, "task_kind"),
                outcome_contract=_optional_object(payload, "outcome_contract"),
                set_outcome_contract="outcome_contract" in payload,
                integration_boundary=_optional_bool(
                    payload,
                    "integration_boundary",
                ),
                deliverable_contract=_optional_object(
                    payload,
                    "deliverable_contract",
                ),
                set_deliverable_contract="deliverable_contract" in payload,
                expected_revision=expected_revision,
            ),
            create_collection="tasks",
            create_id=task_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
            extra=_idle_context_rotation_outbox_publication_extra(
                context,
                state=state,
                agent_id=owner_agent_id,
            ),
        )

    async def patch_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        task_id = request.path_params["task_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_task(
                task_id=task_id,
                priority=_optional_int(payload, "priority"),
                state=TeamTaskState(raw_state) if raw_state else None,
                title=_optional(payload, "title"),
                description=_optional(payload, "description"),
                case_id=_optional_present_text(payload, "case_id"),
                set_case_id="case_id" in payload,
                parent_task_id=_optional(payload, "parent_task_id"),
                set_parent_task_id="parent_task_id" in payload,
                authored_by=_optional_object(payload, "authored_by"),
                set_authored_by="authored_by" in payload,
                git_base_branch=_optional_present_text(payload, "git_base_branch"),
                set_git_base_branch="git_base_branch" in payload,
                dependency_task_ids=_optional_string_list(payload, "dependency_task_ids"),
                integration_boundary=_optional_bool(
                    payload,
                    "integration_boundary",
                ),
                deliverable_contract=_optional_object(
                    payload,
                    "deliverable_contract",
                ),
                set_deliverable_contract="deliverable_contract" in payload,
                expected_revision=expected_revision,
            ),
            target_collection="tasks",
            target_id=task_id,
        )
        task = state.tasks.get(task_id)
        return _state_response(
            request,
            state,
            publish_role_queue=True,
            rebased_from_revision=rebased_from_revision,
            extra=(
                _idle_context_rotation_outbox_publication_extra(
                    context,
                    state=state,
                    agent_id=task.owner_agent_id,
                )
                if task is not None
                else None
            ),
        )

    async def cancel_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        task_id = request.path_params["task_id"]
        rebind_dependents = _optional_bool(payload, "rebind_dependents") is True

        def _cancel(expected_revision: int) -> TeamState:
            return admin_service.cancel_task(
                task_id=task_id,
                reason_code=_required(payload, "reason_code"),
                reason=_required(payload, "reason"),
                actor_id=_operator_actor_from_request(context.auth, request),
                successor_task_id=_optional(payload, "successor_task_id"),
                rebind_dependents=rebind_dependents,
                expected_dependent_task_ids=_optional_string_list(
                    payload,
                    "expected_dependent_task_ids",
                ),
                expected_revision=expected_revision,
            )

        if rebind_dependents:
            current = repository.read()
            authorization_error = _task_project_owner_authorization_error(
                request,
                state=current,
                task_id=task_id,
                action="Task dependency rebinding",
            )
            if authorization_error is not None:
                return authorization_error
            # Graph rewrites never auto-rebase: the authorization and frozen
            # affected set both belong to this exact state revision.
            state = _cancel(_expected_revision(payload))
            rebased_from_revision = None
        else:
            state, rebased_from_revision = _run_ui_rebasable_mutation(
                payload,
                _cancel,
                target_collection="tasks",
                target_id=task_id,
            )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra=(
                _idle_context_rotation_outbox_publication_extra(
                    context,
                    state=state,
                    agent_id=state.tasks[task_id].owner_agent_id,
                )
                if task_id in state.tasks
                else None
            ),
        )

    async def repair_cancelled_task_dependencies(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        predecessor_task_id = request.path_params["task_id"]
        current = repository.read()
        authorization_error = _task_project_owner_authorization_error(
            request,
            state=current,
            task_id=predecessor_task_id,
            action="Cancelled-task dependency repair",
        )
        if authorization_error is not None:
            return authorization_error
        expected_dependent_task_ids = _optional_string_list(
            payload,
            "expected_dependent_task_ids",
        )
        if expected_dependent_task_ids is None:
            raise ValueError("expected_dependent_task_ids is required")
        state, result = admin_service.repair_cancelled_task_dependencies(
            predecessor_task_id=predecessor_task_id,
            successor_task_id=_required(payload, "successor_task_id"),
            expected_dependent_task_ids=expected_dependent_task_ids,
            reason=_required(payload, "reason"),
            actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            extra={"task_dependency_repair": result},
        )

    async def recover_submitted_integration_boundary(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        integration_task_id = request.path_params["task_id"]
        current = repository.read()
        authorization_error = _task_project_owner_authorization_error(
            request,
            state=current,
            task_id=integration_task_id,
            action="Submitted integration-boundary recovery",
        )
        if authorization_error is not None:
            return authorization_error
        expected_dependency_task_ids = _optional_string_list(
            payload,
            "expected_dependency_task_ids",
        )
        expected_required_contribution_task_ids = _optional_string_list(
            payload,
            "expected_required_contribution_task_ids",
        )
        if expected_dependency_task_ids is None:
            raise ValueError("expected_dependency_task_ids is required")
        if expected_required_contribution_task_ids is None:
            raise ValueError("expected_required_contribution_task_ids is required")
        state, result = admin_service.recover_submitted_integration_boundary(
            predecessor_task_id=_required(payload, "predecessor_task_id"),
            successor_task_id=_required(payload, "successor_task_id"),
            integration_task_id=integration_task_id,
            replacement_task_id=_required(payload, "replacement_task_id"),
            expected_dependency_task_ids=expected_dependency_task_ids,
            expected_required_contribution_task_ids=(expected_required_contribution_task_ids),
            reason=_required(payload, "reason"),
            actor_id=_operator_actor_from_request(context.auth, request),
            git_base_commit_sha=_optional(payload, "git_base_commit_sha"),
            git_base_branch=_optional(payload, "git_base_branch"),
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            extra={"submitted_integration_boundary_recovery": result},
        )

    async def repair_task_state(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        task_id = request.path_params["task_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.repair_task_state(
                task_id=task_id,
                state=TeamTaskState(_required(payload, "state")),
                reason=_required(payload, "reason"),
                actor=_operator_actor_from_request(context.auth, request),
                reference_ids=_optional_string_list(payload, "reference_ids"),
                expected_revision=expected_revision,
            ),
            target_collection="tasks",
            target_id=task_id,
        )
        task = state.tasks.get(task_id)
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra=(
                _idle_context_rotation_outbox_publication_extra(
                    context,
                    state=state,
                    agent_id=task.owner_agent_id,
                )
                if task is not None
                else None
            ),
        )

    async def record_deliverable(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        deliverable_id = _optional(payload, "id") or _optional(payload, "deliverable_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.record_deliverable(
                deliverable_id=deliverable_id,
                task_id=_required(payload, "task_id"),
                producer_agent_id=_required(payload, "producer_agent_id"),
                summary=_required(payload, "summary"),
                artifact_refs=_optional_string_list(payload, "artifact_refs"),
                outcomes=_optional_list(payload, "outcomes"),
                source_deliverable_ids=_optional_string_list(
                    payload,
                    "source_deliverable_ids",
                ),
                completion=_optional_object(payload, "completion"),
                state=TeamRecordState(raw_state) if raw_state else TeamRecordState.PENDING,
                supersedes_deliverable_id=_optional(
                    payload,
                    "supersedes_deliverable_id",
                ),
                enforce_task_assessment=True,
                expected_revision=expected_revision,
            ),
            create_collection="deliverables",
            create_id=deliverable_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def record_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        raw_decision_at = _optional(payload, "decision_at")
        review_request_id = _optional(payload, "id") or _optional(payload, "review_request_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.record_review_request(
                review_request_id=review_request_id,
                deliverable_id=_required(payload, "deliverable_id"),
                reviewer_agent_id=_required(payload, "reviewer_agent_id"),
                state=TeamRecordState(raw_state) if raw_state else TeamRecordState.PENDING,
                findings=_optional_string_list(payload, "findings"),
                review_profile_id=_optional(payload, "review_profile_id"),
                instructions=_optional(payload, "instructions"),
                requested_by_actor=_operator_actor_from_request(
                    context.auth,
                    request,
                ),
                decision_at=datetime.fromisoformat(raw_decision_at) if raw_decision_at else None,
                expected_revision=expected_revision,
            ),
            create_collection="review_requests",
            create_id=review_request_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def decide_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        criteria = (
            _optional_list(payload, "criteria") if payload.get("criteria") is not None else None
        )
        finding_resolutions = (
            _optional_list(payload, "finding_resolutions")
            if payload.get("finding_resolutions") is not None
            else None
        )
        review_request_id = request.path_params["review_request_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.record_review_decision(
                review_request_id=review_request_id,
                state=TeamRecordState(_required(payload, "state")),
                findings=_optional_string_list(payload, "findings"),
                criteria=criteria,
                finding_resolutions=finding_resolutions,
                expected_revision=expected_revision,
            ),
            target_collection="review_requests",
            target_id=review_request_id,
        )
        review = state.review_requests.get(review_request_id)
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra=(
                _idle_context_rotation_outbox_publication_extra(
                    context,
                    state=state,
                    agent_id=review.reviewer_agent_id,
                )
                if review is not None
                else None
            ),
        )

    async def retire_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        review_request_id = request.path_params["review_request_id"]
        operator_actor_id = _operator_actor_from_request(context.auth, request)
        reason = _required(payload, "reason")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.retire_review_request(
                review_request_id=review_request_id,
                reason=reason,
                operator_actor_id=operator_actor_id,
                expected_revision=expected_revision,
            ),
            target_collection="review_requests",
            target_id=review_request_id,
        )
        steering = await run_in_threadpool(
            steer_closed_review_dispatch,
            runtime_dir=context.runtime_dir,
            state=state,
            review_request_id=review_request_id,
            action="retired",
            reason=reason,
            created_by=operator_actor_id,
        )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra={"review_dispatch_steering": steering},
        )

    async def withdraw_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        review_request_id = request.path_params["review_request_id"]
        operator_actor_id = _operator_actor_from_request(context.auth, request)
        reason = _required(payload, "reason")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.withdraw_review_request(
                review_request_id=review_request_id,
                reason=reason,
                operator_actor_id=operator_actor_id,
                expected_revision=expected_revision,
            ),
            target_collection="review_requests",
            target_id=review_request_id,
        )
        steering = await run_in_threadpool(
            steer_closed_review_dispatch,
            runtime_dir=context.runtime_dir,
            state=state,
            review_request_id=review_request_id,
            action="withdrawn",
            reason=reason,
            created_by=operator_actor_id,
        )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra={"review_dispatch_steering": steering},
        )

    async def create_operator_directive(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        created_by = _optional(payload, "created_by") or _operator_actor_from_request(
            context.auth,
            request,
        )
        state, item, wake_result = admin_service.create_operator_directive(
            team_id=_required(payload, "team_id"),
            message=_required(payload, "message"),
            recipient_agent_id=_optional(payload, "recipient_agent_id"),
            project_id=_optional(payload, "project_id"),
            task_id=_optional(payload, "task_id"),
            title=_optional(payload, "title"),
            source=_optional(payload, "source"),
            source_message_id=_optional(payload, "source_message_id"),
            created_by=created_by,
            requesting_session_id=_optional(payload, "requesting_session_id"),
            expected_revision=_expected_revision(payload),
        )
        wake_delivery = publish_team_wake_events_with_outbox_recovery(
            context,
            repository=TeamStateRepository(context.runtime_dir),
            events=wake_result.get("events"),
            outbox_record_ids=wake_result.get("outbox_record_ids") or [],
            publish_events=lambda events: _publish_team_events(context, events),
        )
        return _state_response(
            request,
            state,
            status_code=201,
            extra={
                "operator_directive": item.model_dump(mode="json"),
                **wake_delivery,
            },
        )

    async def dismiss_action_record(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        action_record_id = request.path_params["action_record_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.dismiss_action_record_attention(
                action_record_id=action_record_id,
                dismissed_by=_optional(payload, "dismissed_by"),
                dismissed_reason=_optional(payload, "dismissed_reason"),
                expected_revision=expected_revision,
            ),
            target_collection="action_records",
            target_id=action_record_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def resolve_terminal_blocker(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        blocker_id = request.path_params["blocker_id"]
        state, result = admin_service.resolve_terminal_blocker_as_operator(
            blocker_id=blocker_id,
            note=_required(payload, "note"),
            resolution_refs=_optional_string_list(payload, "resolution_refs"),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        extra: dict[str, Any] = {"resolved_blocker": result["blocker"]}
        if any(
            event.get("event_type") == AGENT_WAKE_REQUESTED_EVENT_TYPE
            for event in result.get("events") or []
        ):
            extra["outbox_publication_wake"] = _request_team_outbox_publication(
                context,
                correlation_key=(f"terminal-blocker-resolution:{blocker_id}:{state.revision}"),
            )
        return _state_response(
            request,
            state,
            extra=extra,
        )

    async def authorize_deliverable_acceptance_verification_waiver(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        deliverable_id = request.path_params["deliverable_id"]
        state, deliverable = (
            admin_service.authorize_deliverable_acceptance_verification_waiver_as_operator(
                deliverable_id=deliverable_id,
                reason=_required(payload, "reason"),
                operator_actor_id=_operator_actor_from_request(context.auth, request),
                expected_revision=_expected_revision(payload),
            )
        )
        return _state_response(
            request,
            state,
            extra={
                "warning": ACCEPTANCE_VERIFICATION_RETIRED_WARNING,
                "deliverable_acceptance_verification": (
                    deliverable.verification.model_dump(mode="json", exclude_none=True)
                    if deliverable.verification is not None
                    else None
                ),
            },
        )

    async def recover_candidate_correction_successor(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        blocker_id = request.path_params["blocker_id"]
        state, result = admin_service.recover_candidate_correction_successor_as_operator(
            blocker_id=blocker_id,
            action=_required(payload, "action"),
            reason=_optional(payload, "reason"),
            instruction=_optional(payload, "instruction"),
            owner_agent_id=_optional(payload, "owner_agent_id"),
            operator_actor_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            expected_revision=_expected_revision(payload),
        )
        result["outbox_publication_wake"] = _request_team_outbox_publication(
            context,
            correlation_key=(f"candidate-correction-recovery:{blocker_id}:{state.revision}"),
        )
        return _state_response(
            request,
            state,
            extra={"candidate_correction_recovery": result},
        )

    async def start_dependency_composition_recovery(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        blocker_id = request.path_params["blocker_id"]
        state, result = admin_service.start_dependency_composition_recovery_as_operator(
            blocker_id=blocker_id,
            resolver_agent_id=_required(payload, "resolver_agent_id"),
            operator_actor_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            status_code=201,
            extra={"dependency_composition_recovery": result},
        )

    async def create_outcome_kind_definition(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        definition_id = _optional(payload, "id") or _optional(
            payload,
            "definition_id",
        )
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_outcome_kind_definition(
                definition_id=definition_id,
                team_id=_required(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                kind=_required(payload, "kind"),
                name=_required(payload, "name"),
                description=_optional(payload, "description") or "",
                reference_requirements=(_optional(payload, "reference_requirements") or ""),
                evidence_requirements=(_optional(payload, "evidence_requirements") or ""),
                expected_revision=expected_revision,
            ),
            create_collection="outcome_kind_definitions",
            create_id=definition_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_outcome_kind_definition(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        raw_archived = payload.get("archived")
        definition_id = request.path_params["definition_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_outcome_kind_definition(
                definition_id=definition_id,
                name=_optional(payload, "name"),
                description=_optional_present_text(payload, "description"),
                reference_requirements=_optional_present_text(
                    payload,
                    "reference_requirements",
                ),
                evidence_requirements=_optional_present_text(
                    payload,
                    "evidence_requirements",
                ),
                archived=raw_archived if isinstance(raw_archived, bool) else None,
                expected_revision=expected_revision,
            ),
            target_collection="outcome_kind_definitions",
            target_id=definition_id,
        )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
        )

    async def list_outcome_kind_definitions(
        _request: Request,
    ) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "outcome_kind_definitions": [
                    definition.model_dump(mode="json")
                    for definition in state.outcome_kind_definitions.values()
                ],
            }
        )

    async def get_outcome_kind_definition(
        request: Request,
    ) -> JSONResponse:
        state = repository.read()
        definition = state.outcome_kind_definitions.get(request.path_params["definition_id"])
        if definition is None:
            return JSONResponse(
                {"error": "outcome kind definition not found"},
                status_code=404,
            )
        return JSONResponse(definition.model_dump(mode="json"))

    async def create_review_profile(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        criteria = [
            ReviewProfileCriterion.model_validate(item)
            for item in _optional_list(payload, "criteria")
        ]
        profile_id = _optional(payload, "id") or _optional(payload, "review_profile_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_review_profile(
                profile_id=profile_id,
                team_id=_required(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                name=_required(payload, "name"),
                description=_optional(payload, "description") or "",
                applicable_role_ids=_optional_string_list(payload, "applicable_role_ids"),
                criteria=criteria,
                required_outcome_kinds=(
                    _optional_string_list(payload, "required_outcome_kinds")
                    if "required_outcome_kinds" in payload
                    else None
                ),
                expected_revision=expected_revision,
            ),
            create_collection="review_profiles",
            create_id=profile_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_review_profile(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        has_project = "project_id" in payload
        has_criteria = "criteria" in payload
        criteria = (
            [
                ReviewProfileCriterion.model_validate(item)
                for item in _optional_list(payload, "criteria")
            ]
            if has_criteria
            else None
        )
        raw_archived = payload.get("archived")
        profile_id = request.path_params["review_profile_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_review_profile(
                profile_id=profile_id,
                name=_optional(payload, "name"),
                description=_optional_present_text(payload, "description"),
                applicable_role_ids=(
                    _optional_string_list(payload, "applicable_role_ids")
                    if "applicable_role_ids" in payload
                    else None
                ),
                criteria=criteria,
                required_outcome_kinds=(
                    _optional_string_list(payload, "required_outcome_kinds")
                    if "required_outcome_kinds" in payload
                    else None
                ),
                archived=raw_archived if isinstance(raw_archived, bool) else None,
                project_id=_optional(payload, "project_id"),
                set_project_id=has_project,
                expected_revision=expected_revision,
            ),
            target_collection="review_profiles",
            target_id=profile_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def list_review_profiles(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "review_profiles": [
                    profile.model_dump(mode="json") for profile in state.review_profiles.values()
                ],
            }
        )

    async def get_review_profile(request: Request) -> JSONResponse:
        state = repository.read()
        profile = state.review_profiles.get(request.path_params["review_profile_id"])
        if profile is None:
            return JSONResponse({"error": "review profile not found"}, status_code=404)
        return JSONResponse(profile.model_dump(mode="json"))

    async def list_human_review_requests(request: Request) -> JSONResponse:
        identity = _strict_human_identity_from_request(context.auth, request)
        assigned_to = _query_text(request, "assigned_to")
        if assigned_to not in {None, "me"}:
            raise ValueError("assigned_to must be me")
        states = set(_query_values(request, "state") or ())
        allowed_states = {
            "preparing",
            "pending",
            "blocked",
            "approved",
            "changes_requested",
            "cancelled",
        }
        unknown_states = states - allowed_states
        if unknown_states:
            raise ValueError(f"unknown human review state: {', '.join(sorted(unknown_states))}")
        state = repository.read()
        rows = []
        for human_request in sorted(
            state.human_review_requests.values(),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        ):
            if states and human_request.state not in states:
                continue
            if assigned_to == "me":
                if not _human_review_is_assigned_to(
                    human_request,
                    identity["user_id"],
                ):
                    continue
            elif not _can_read_human_review_request(
                context.auth,
                request,
                human_request,
                identity=identity,
            ):
                continue
            rows.append(human_review_request_detail_payload(state, human_request))
        return JSONResponse(
            {
                "revision": state.revision,
                "human_review_requests": rows,
            }
        )

    async def get_human_review_request(request: Request) -> JSONResponse:
        identity = _strict_human_identity_from_request(context.auth, request)
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        if not _can_read_human_review_request(
            context.auth,
            request,
            human_request,
            identity=identity,
        ):
            raise PermissionError("Human review request is not assigned to this user")
        return JSONResponse(human_review_request_detail_payload(state, human_request))

    async def download_human_review_bundle(request: Request) -> FileResponse | JSONResponse:
        identity = _strict_human_identity_from_request(context.auth, request)
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        if not _can_read_human_review_request(
            context.auth,
            request,
            human_request,
            identity=identity,
        ):
            raise PermissionError("Human review request is not assigned to this user")

        release = state.release_packages.get(human_request.release_package_id)
        if release is None:
            return JSONResponse(
                {"error": "release package not found"},
                status_code=404,
            )
        project = state.projects.get(release.project_id)
        git = getattr(project, "git", None)
        candidate_sha = str(getattr(release, "candidate_commit_sha", "") or "").strip()
        candidate_tree = str(getattr(release, "candidate_tree_hash", "") or "").strip()
        release_branch = str(getattr(release, "release_branch", "") or "").strip()
        if (
            git is None
            or not candidate_sha
            or not candidate_tree
            or not release_branch
            or human_request.candidate_commit_sha != candidate_sha
            or human_request.candidate_tree_hash != candidate_tree
        ):
            raise ValueError(
                "Human review request does not have an exportable pinned release candidate"
            )
        manifest = getattr(release, "manifest", None)
        if manifest is not None and (
            str(getattr(manifest, "candidate_commit_sha", "") or "").strip() != candidate_sha
            or str(getattr(manifest, "candidate_tree_hash", "") or "").strip() != candidate_tree
        ):
            raise ValueError("Release manifest does not match the pinned human-review candidate")
        managed_git_admin = getattr(context.service, "managed_git_admin", None)
        export_archive = getattr(managed_git_admin, "export_pinned_source_archive", None)
        if not callable(export_archive):
            return JSONResponse(
                {"error": "Pinned source bundle export is unavailable"},
                status_code=503,
            )

        release_ref = (
            release_branch if release_branch.startswith("refs/") else f"refs/heads/{release_branch}"
        )
        descriptor, archive_name = tempfile.mkstemp(
            prefix="evander-human-review-",
            suffix=".zip",
        )
        os.close(descriptor)
        archive_path = Path(archive_name)
        try:
            await run_in_threadpool(
                export_archive,
                git.managed_repo_id,
                git.directory_path,
                source_ref=release_ref,
                commit_sha=candidate_sha,
                tree_hash=candidate_tree,
                archive_path=archive_path,
            )
            _append_human_review_bundle_manifest(
                archive_path,
                _human_review_bundle_manifest(
                    human_request=human_request,
                    release=release,
                    project=project,
                    source_ref=release_ref,
                ),
            )
        except ManagedGitNotFoundError as exc:
            _delete_temporary_file(archive_path)
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ManagedGitConflictError as exc:
            _delete_temporary_file(archive_path)
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception:
            _delete_temporary_file(archive_path)
            raise

        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=_human_review_bundle_filename(release.id),
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            background=BackgroundTask(_delete_temporary_file, archive_path),
        )

    async def decide_human_review(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        _reconcile_human_review_request_assignment(
            context.auth,
            repository,
            request.path_params["human_review_request_id"],
        )
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        identity = _require_human_review_decision_identity(
            context.auth,
            request,
            human_request,
            payload=payload,
        )
        state, result = admin_service.record_human_review_decision_with_result(
            human_review_request_id=human_request.id,
            reviewer_user_id=identity["user_id"],
            reviewer_display_name=identity["display_name"],
            decision=_required(payload, "decision"),
            request_id=_mutation_request_id(request, payload),
            expected_request_revision=_expected_revision(payload),
            checklist_results=_optional_object(payload, "checklist_results"),
            confirmed_finding_ids=(_optional_string_list(payload, "confirmed_finding_ids") or []),
            summary=_optional(payload, "summary") or "",
            evidence_refs=_optional_string_list(payload, "evidence_refs") or [],
            findings=_optional_mapping_list(payload, "findings"),
        )
        published_event_ids = _publish_team_events(context, result.get("events"))
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "published_event_ids": published_event_ids,
                "human_review_request": human_review_request_detail_payload(
                    state,
                    state.human_review_requests[human_request.id],
                ),
            }
        )

    async def reassign_human_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        _reconcile_human_review_request_assignment(
            context.auth,
            repository,
            request.path_params["human_review_request_id"],
        )
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        identity = _require_human_review_owner_identity(
            context.auth,
            request,
            human_request,
            payload=payload,
        )
        user_ids = _validate_human_review_reassignment_targets(
            context.auth,
            human_request,
            user_ids=_optional_string_list(payload, "user_ids") or [],
        )
        state, result = admin_service.reassign_human_review_with_result(
            human_review_request_id=human_request.id,
            user_ids=user_ids,
            assigned_by_user_id=identity["user_id"],
            request_id=_mutation_request_id(request, payload),
            expected_request_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_request": human_review_request_detail_payload(
                    state,
                    state.human_review_requests[human_request.id],
                ),
            }
        )

    async def resume_human_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        _reconcile_human_review_request_assignment(
            context.auth,
            repository,
            request.path_params["human_review_request_id"],
        )
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        identity = _require_human_review_owner_identity(
            context.auth,
            request,
            human_request,
            payload=payload,
        )
        if not _current_human_review_decision_eligible_user_ids(
            context.auth,
            human_request,
        ):
            raise ValueError(
                "human review assignment is unavailable; audited reassignment is required"
            )
        state, result = admin_service.resume_human_review_with_result(
            human_review_request_id=human_request.id,
            resumed_by_user_id=identity["user_id"],
            request_id=_mutation_request_id(request, payload),
            expected_request_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_request": human_review_request_detail_payload(
                    state,
                    state.human_review_requests[human_request.id],
                ),
            }
        )

    async def record_human_review_preparation(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        _reconcile_human_review_request_assignment(
            context.auth,
            repository,
            request.path_params["human_review_request_id"],
        )
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        identity = _require_human_review_owner_identity(
            context.auth,
            request,
            human_request,
            payload=payload,
        )
        target = _optional_object(payload, "target")
        error = _optional(payload, "error")
        if (target is None) == (error is None):
            raise ValueError("exactly one of target or error is required")
        if error is None and not _current_human_review_decision_eligible_user_ids(
            context.auth,
            human_request,
        ):
            raise ValueError(
                "human review assignment is unavailable; audited reassignment is required"
            )
        state, result = admin_service.record_human_review_preparation_result_with_result(
            human_review_request_id=human_request.id,
            recorded_by_user_id=identity["user_id"],
            request_id=_mutation_request_id(request, payload),
            expected_request_revision=_expected_revision(payload),
            candidate_commit_sha=_required(payload, "candidate_commit_sha"),
            candidate_tree_hash=_required(payload, "candidate_tree_hash"),
            preparation_generation=_required_int(
                payload,
                "preparation_generation",
            ),
            target=target,
            error=error,
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_request": human_review_request_detail_payload(
                    state,
                    state.human_review_requests[human_request.id],
                ),
            }
        )

    async def report_release_issue(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        _reject_client_human_identity_fields(payload)
        identity = _strict_human_identity_from_request(context.auth, request)
        release_package_id = request.path_params["release_package_id"]
        state, result = admin_service.report_post_release_feedback_with_result(
            release_package_id=release_package_id,
            reporter_user_id=identity["user_id"],
            reporter_display_name=identity["display_name"],
            report=_required(payload, "report"),
            evidence_refs=_optional_string_list(payload, "evidence_refs") or [],
            request_id=_mutation_request_id(request, payload),
            expected_state_revision=_expected_revision(payload),
        )
        published_event_ids = _publish_team_events(context, result.get("events"))
        case_id = str(result["case_id"])
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "published_event_ids": published_event_ids,
                "case": state.cases[case_id].model_dump(mode="json"),
            }
        )

    async def propose_human_finding_resolution(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        _reject_client_human_identity_fields(payload)
        finding_id = request.path_params["human_review_finding_id"]
        state = repository.read()
        if finding_id not in state.human_review_findings:
            return JSONResponse(
                {"error": "human review finding not found"},
                status_code=404,
            )
        state, result = admin_service.record_human_finding_resolution_proposal_with_result(
            finding_id=finding_id,
            proposed_resolution=_required(payload, "proposed_resolution"),
            evidence_refs=(_optional_string_list(payload, "evidence_refs") or []),
            proposed_by_deliverable_ids=(
                _optional_string_list(
                    payload,
                    "proposed_by_deliverable_ids",
                )
                or []
            ),
            proposed_by_user_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            request_id=_mutation_request_id(request, payload),
            expected_state_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_finding": state.human_review_findings[finding_id].model_dump(
                    mode="json"
                ),
            }
        )

    async def route_human_finding_correction(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        _reject_client_human_identity_fields(payload)
        finding_id = request.path_params["human_review_finding_id"]
        state = repository.read()
        if finding_id not in state.human_review_findings:
            return JSONResponse(
                {"error": "human review finding not found"},
                status_code=404,
            )
        state, result = admin_service.route_human_finding_correction_with_result(
            finding_id=finding_id,
            owner_agent_id=_required(payload, "owner_agent_id"),
            target_deliverable_ids=_optional_string_list(
                payload,
                "target_deliverable_ids",
            ),
            routed_by_user_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            request_id=_mutation_request_id(request, payload),
            expected_state_revision=_expected_revision(payload),
        )
        correction_task_id = str(result["correction_task_id"])
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_finding": state.human_review_findings[finding_id].model_dump(
                    mode="json"
                ),
                "correction_task": task_payload_with_assessment(
                    state,
                    state.tasks[correction_task_id],
                ),
            },
        )

    async def close_post_release_feedback_case(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        _reject_client_human_identity_fields(payload)
        case_id = request.path_params["case_id"]
        state = repository.read()
        if case_id not in state.cases:
            return JSONResponse({"error": "case not found"}, status_code=404)
        state, result = admin_service.close_post_release_feedback_case_with_result(
            case_id=case_id,
            reason=_required(payload, "reason"),
            evidence_refs=(_optional_string_list(payload, "evidence_refs") or []),
            closed_by_user_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            request_id=_mutation_request_id(request, payload),
            expected_state_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "case": state.cases[case_id].model_dump(mode="json"),
            }
        )

    async def create_release_package(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state, result = admin_service.create_release_package_with_result(
            project_id=_required(payload, "project_id"),
            deliverable_ids=_optional_string_list(payload, "deliverable_ids") or [],
            release_id=_optional(payload, "id") or _optional(payload, "release_package_id"),
            supersedes_release_package_id=_optional(
                payload,
                "supersedes_release_package_id",
            ),
            post_release_feedback_case_id=_optional(
                payload,
                "post_release_feedback_case_id",
            ),
            release_branch=_optional(payload, "release_branch"),
            canonical_branch=_optional(payload, "canonical_branch"),
            summary=_optional(payload, "summary"),
            verification_waivers=(
                payload.get("verification_waivers")
                if payload.get("verification_waivers") is not None
                else []
                if "verification_waivers" in payload
                else None
            ),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        response_payload = _state_payload(request, state)
        published_event_ids = _publish_team_events(context, result.get("events"))
        if published_event_ids:
            response_payload["published_event_ids"] = published_event_ids
        warnings = list(result.get("warnings") or [])
        if warnings:
            response_payload["warnings"] = warnings
        if _request_wants_view_scoped_response(request):
            return _state_response(
                request,
                state,
                status_code=201,
                extra={
                    "published_event_ids": published_event_ids,
                    **({"warnings": warnings} if warnings else {}),
                },
            )
        return JSONResponse(response_payload, status_code=201)

    async def list_release_packages(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "release_packages": [
                    release_package_api_payload(
                        state,
                        release,
                        include_human_details=False,
                    )
                    for release in state.release_packages.values()
                ],
            }
        )

    async def cancel_release_package(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        release_id = request.path_params["release_package_id"]
        current = repository.read()
        release = current.release_packages.get(release_id)
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        project = current.projects.get(release.project_id)
        authorization_error = _directory_owner_authorization_error(
            request,
            directory_path=project.directory_path if project is not None else "root",
            action="Release cancellation",
        )
        if authorization_error is not None:
            return authorization_error
        expected_revision = _expected_revision(payload)
        if expected_revision is None:
            return JSONResponse({"error": "expected_revision is required"}, status_code=400)
        state, result = admin_service.cancel_release_package(
            release_package_id=release_id,
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            reason=_optional(payload, "reason") or "",
            expected_revision=expected_revision,
        )
        return _state_response(request, state, extra={"release_cancellation": result})

    async def retry_release_gate(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        release_id = request.path_params["release_package_id"]
        state, result = admin_service.retry_release_gate_with_result(
            release_package_id=release_id,
            gate_kind=_required(payload, "gate_kind"),
            gate_id=_required(payload, "gate_id"),
            reason=_required(payload, "reason"),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            allow_rejected_review_exception=(
                _optional_bool(payload, "allow_rejected_review_exception") or False
            ),
            allow_rejected_verification_retry=(
                _optional_bool(payload, "allow_rejected_verification_retry") or False
            ),
            expected_revision=_expected_revision(payload),
        )
        published_event_ids = _publish_team_events(context, result.get("events"))
        if _request_wants_view_scoped_response(request):
            return _state_response(
                request,
                state,
                extra={
                    "release_package": result["release_package"],
                    "replacement_gate_id": result.get("replacement_gate_id"),
                    "published_event_ids": published_event_ids,
                },
            )
        return JSONResponse(
            {
                **result,
                "published_event_ids": published_event_ids,
            }
        )

    async def authorize_release_gate_obligation_waiver(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        release_id = request.path_params["release_package_id"]
        current = repository.read()
        release = current.release_packages.get(release_id)
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        project = current.projects.get(release.project_id)
        directory_path = project.directory_path if project is not None else "root"
        authorization_error = _directory_owner_authorization_error(
            request,
            directory_path=directory_path,
            action="Release gate obligation waiver",
        )
        if authorization_error is not None:
            return authorization_error
        state, result = admin_service.authorize_release_gate_obligation_waiver_with_result(
            release_package_id=release_id,
            gate_kind=_required(payload, "gate_kind"),
            candidate_commit_sha=_required(payload, "candidate_commit_sha"),
            reason=_required(payload, "reason"),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        published_event_ids = _publish_team_events(context, result.get("events"))
        return JSONResponse({**result, "published_event_ids": published_event_ids})

    async def retry_worktree_cleanup(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        candidate_id = request.path_params["cleanup_candidate_id"]
        current = repository.read()
        candidate = current.worktree_cleanup_candidates.get(candidate_id)
        if candidate is None:
            return JSONResponse(
                {"error": "worktree cleanup candidate not found"},
                status_code=404,
            )
        project = current.projects.get(candidate.project_id)
        directory_path = project.directory_path if project is not None else "root"
        if not _auth_can(
            context.auth,
            request,
            ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return JSONResponse(
                {"error": "Worktree cleanup retry requires directory owner permission."},
                status_code=403,
            )
        state, requeued = TeamWorktreeCleanupService(
            repository,
            EffectTaskWorktreeCleanupBackend(context, repository=repository),
        ).requeue_operator_action(
            candidate_id,
            requested_by=_operator_actor_from_request(context.auth, request),
            reason=_required(payload, "reason"),
            expected_revision=_expected_revision(payload),
        )
        if _request_wants_view_scoped_response(request):
            return _state_response(
                request,
                state,
                extra={"worktree_cleanup_candidate": requeued.model_dump(mode="json")},
            )
        return JSONResponse(
            {
                "revision": state.revision,
                "worktree_cleanup_candidate": requeued.model_dump(mode="json"),
            }
        )

    async def get_release_package(request: Request) -> JSONResponse:
        state = repository.read()
        release = state.release_packages.get(request.path_params["release_package_id"])
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        include_human_details = _can_read_release_human_review(
            context.auth,
            request,
            state,
            release.id,
        )
        payload = release_package_api_payload(
            state,
            release,
            include_human_details=include_human_details,
        )
        if include_human_details:
            candidate_checkout = _release_candidate_checkout_metadata(
                getattr(context.service, "managed_git_admin", None),
                state,
                release,
                runtime_dir=context.runtime_dir,
            )
            if candidate_checkout is not None:
                payload["candidate_checkout"] = candidate_checkout
        return JSONResponse(payload)

    async def get_release_verification_live(request: Request) -> JSONResponse:
        state = repository.read()
        release = state.release_packages.get(request.path_params["release_package_id"])
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        project = state.projects.get(release.project_id)
        directory_path = project.directory_path if project is not None else "root"
        if not _auth_can(
            context.auth,
            request,
            ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return JSONResponse(
                {"error": "Live verification output is restricted to directory owners."},
                status_code=403,
            )
        runtime = _runtime_from_context(context)
        payload = await run_in_threadpool(
            _release_verification_live_payload,
            state,
            release.id,
            directory_path,
            runtime.provider_config,
            _release_verification_live_log_limit(request),
        )
        return JSONResponse(payload)

    async def list_git_promotions(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "git_promotions": [
                    promotion.model_dump(mode="json") for promotion in state.git_promotions.values()
                ],
            }
        )

    async def get_git_promotion(request: Request) -> JSONResponse:
        state = repository.read()
        promotion = state.git_promotions.get(request.path_params["promotion_id"])
        if promotion is None:
            return JSONResponse({"error": "git promotion not found"}, status_code=404)
        return JSONResponse(promotion.model_dump(mode="json"))

    async def list_external_source_evidence_snapshots(request: Request) -> JSONResponse:
        state = repository.read()
        rows = [
            row
            for row in state.external_source_evidence_snapshots.values()
            if _can_read_external_source_evidence(context.auth, request, state, row)
        ]
        team_id = _query_text(request, "team_id")
        project_id = _query_text(request, "project_id")
        provider_type = _query_text(request, "provider_type")
        source_type = _query_text(request, "source_type")
        external_identifier = _query_text(request, "external_identifier")
        if team_id is not None:
            rows = [row for row in rows if row.team_id == team_id]
        if project_id is not None:
            rows = [row for row in rows if row.project_id == project_id]
        if provider_type is not None:
            rows = [row for row in rows if row.provider_type == provider_type]
        if source_type is not None:
            rows = [row for row in rows if row.source_type == source_type]
        if external_identifier is not None:
            rows = [row for row in rows if row.external_identifier == external_identifier]
        rows.sort(key=lambda row: (row.fetched_at, row.created_at, row.id), reverse=True)
        return JSONResponse(
            {
                "revision": state.revision,
                "external_source_evidence_snapshots": [
                    external_source_evidence_snapshot_payload(row) for row in rows
                ],
            }
        )

    async def get_external_source_evidence_snapshot(request: Request) -> JSONResponse:
        state = repository.read()
        snapshot = state.external_source_evidence_snapshots.get(request.path_params["snapshot_id"])
        if snapshot is None:
            return JSONResponse(
                {"error": "external source evidence snapshot not found"},
                status_code=404,
            )
        if not _can_read_external_source_evidence(
            context.auth,
            request,
            state,
            snapshot,
        ):
            return JSONResponse(
                {"error": "external source evidence snapshot not found"},
                status_code=404,
            )
        return JSONResponse(
            {
                "revision": state.revision,
                "external_source_evidence_snapshot": (
                    external_source_evidence_snapshot_payload(snapshot)
                ),
            }
        )

    async def create_external_source_evidence_snapshot(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        current = repository.read()
        project_id = _optional(payload, "project_id")
        directory_path = _external_source_evidence_project_directory_path(
            current,
            project_id,
        )
        if not _auth_can(
            context.auth,
            request,
            ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return JSONResponse(
                {
                    "error": (
                        "External source evidence creation requires directory owner permission."
                    )
                },
                status_code=403,
            )
        raw_freshness_status = _optional(payload, "freshness_status")
        state, snapshot = admin_service.create_external_source_evidence_snapshot(
            snapshot_id=_optional(payload, "id") or _optional(payload, "snapshot_id"),
            team_id=_required(payload, "team_id"),
            project_id=project_id,
            provider_type=_required(payload, "provider_type"),
            source_type=_required(payload, "source_type"),
            external_identifier=_required(payload, "external_identifier"),
            external_version=_required(payload, "external_version"),
            fetched_at=_required_iso_datetime(payload, "fetched_at"),
            expires_at=_optional_iso_datetime(payload, "expires_at"),
            freshness_status=(
                ExternalSourceEvidenceFreshnessStatus(raw_freshness_status)
                if raw_freshness_status is not None
                else ExternalSourceEvidenceFreshnessStatus.UNKNOWN
            ),
            access_classification=_required(payload, "access_classification"),
            title=_optional(payload, "title"),
            body=_optional_object(payload, "body"),
            acceptance_criteria=_optional_mapping_list(payload, "acceptance_criteria"),
            discussion=_optional_mapping_list(payload, "discussion"),
            links=_optional_mapping_list(payload, "links"),
            metadata=_optional_object(payload, "metadata") or {},
            content_digest=_optional(payload, "content_digest"),
            case_ids=_optional_string_list(payload, "case_ids") or [],
            task_ids=_optional_string_list(payload, "task_ids") or [],
            created_by_actor=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "external_source_evidence_snapshot": (
                    external_source_evidence_snapshot_payload(snapshot)
                ),
            },
            status_code=201,
        )

    async def attach_external_source_evidence_snapshot(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        current = repository.read()
        existing = current.external_source_evidence_snapshots.get(
            request.path_params["snapshot_id"]
        )
        if existing is None:
            return JSONResponse(
                {"error": "external source evidence snapshot not found"},
                status_code=404,
            )
        if not _can_read_external_source_evidence(
            context.auth,
            request,
            current,
            existing,
        ):
            return JSONResponse(
                {"error": "external source evidence snapshot not found"},
                status_code=404,
            )
        state, snapshot = admin_service.attach_external_source_evidence_snapshot(
            snapshot_id=request.path_params["snapshot_id"],
            case_ids=_optional_string_list(payload, "case_ids") or [],
            task_ids=_optional_string_list(payload, "task_ids") or [],
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "external_source_evidence_snapshot": (
                    external_source_evidence_snapshot_payload(snapshot)
                ),
            }
        )

    async def list_memories(request: Request) -> JSONResponse:
        state = repository.read()
        statuses = _query_values(request, "statuses") or _query_values(request, "status")
        include_archived = _query_bool(request, "include_archived") is True
        result = list_memory_records(
            state,
            team_id=_query_text(request, "team_id"),
            project_id=_query_text(request, "project_id"),
            codebase_id=_query_text(request, "codebase_id"),
            agent_id=_query_text(request, "agent_id"),
            kinds=_query_values(request, "kinds") or _query_values(request, "kind"),
            statuses=statuses,
            tags_any=_query_values(request, "tags_any") or _query_values(request, "tag"),
            query_text=_query_text(request, "query"),
            include_archived=include_archived,
            limit=_query_limited_int(request, "limit", default=20, max_value=50),
            offset=_query_non_negative_int(request, "offset", default=0),
        )
        records = [
            memory_record_payload(memory, include_body=False) for memory in result.pop("records")
        ]
        return JSONResponse({"revision": state.revision, "memories": records, **result})

    async def get_memory(request: Request) -> JSONResponse:
        state = repository.read()
        memory = state.memories.get(request.path_params["memory_id"])
        if memory is None:
            return JSONResponse({"error": "memory not found"}, status_code=404)
        return JSONResponse(
            {
                "revision": state.revision,
                "memory": memory_record_payload(memory, include_body=True),
            }
        )

    async def create_memory(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state, memory = admin_service.create_or_update_memory(
            memory_id=_optional(payload, "id") or _optional(payload, "memory_id"),
            team_id=_required(payload, "team_id"),
            project_id=_optional(payload, "project_id"),
            codebase_id=_optional(payload, "codebase_id"),
            agent_id=_optional(payload, "agent_id"),
            scope=_optional(payload, "scope"),
            kind=_required(payload, "kind"),
            title=_required(payload, "title"),
            summary=_required(payload, "summary"),
            body=_optional(payload, "body"),
            tags=_optional_string_list(payload, "tags") or [],
            source_refs=_optional_string_list(payload, "source_refs") or [],
            artifact_refs=_memory_artifact_refs(payload),
            confidence=(
                float(payload["confidence"]) if payload.get("confidence") is not None else None
            ),
            supersedes_memory_id=_optional(payload, "supersedes_memory_id"),
            created_by=_operator_identity_from_request(context.auth, request)["actor"],
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "memory": memory_record_payload(memory, include_body=True),
            },
            status_code=201,
        )

    async def patch_memory(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.read()
        existing = state.memories.get(request.path_params["memory_id"])
        if existing is None:
            return JSONResponse({"error": "memory not found"}, status_code=404)
        next_payload = existing.model_dump(mode="json")
        for key in (
            "team_id",
            "project_id",
            "codebase_id",
            "agent_id",
            "scope",
            "kind",
            "title",
            "summary",
            "body",
            "tags",
            "source_refs",
            "artifact_refs",
            "confidence",
            "supersedes_memory_id",
        ):
            if key in payload:
                next_payload[key] = payload.get(key)
        if "artifact_space_ref" in payload:
            next_payload["artifact_space_ref"] = payload.get("artifact_space_ref")
            next_payload["artifact_refs"] = _memory_artifact_refs(next_payload)
        state, memory = admin_service.create_or_update_memory(
            memory_id=existing.id,
            team_id=_required(next_payload, "team_id"),
            project_id=_optional(next_payload, "project_id"),
            codebase_id=_optional(next_payload, "codebase_id"),
            agent_id=_optional(next_payload, "agent_id"),
            scope=_optional(next_payload, "scope"),
            kind=_required(next_payload, "kind"),
            title=_required(next_payload, "title"),
            summary=_required(next_payload, "summary"),
            body=_optional(next_payload, "body"),
            tags=_optional_string_list(next_payload, "tags") or [],
            source_refs=_optional_string_list(next_payload, "source_refs") or [],
            artifact_refs=_memory_artifact_refs(next_payload),
            confidence=(
                float(next_payload["confidence"])
                if next_payload.get("confidence") is not None
                else None
            ),
            supersedes_memory_id=_optional(next_payload, "supersedes_memory_id"),
            created_by=_operator_identity_from_request(context.auth, request)["actor"],
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "memory": memory_record_payload(memory, include_body=True),
            }
        )

    async def archive_memory(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state, memory = admin_service.archive_memory(
            memory_id=request.path_params["memory_id"],
            superseded_by_memory_id=_optional(payload, "superseded_by_memory_id"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "memory": memory_record_payload(memory, include_body=True),
            }
        )

    def route(
        path: str,
        handler: Any,
        *,
        methods: Sequence[str],
        require_privileged: bool | None = None,
    ) -> Route:
        protected_handler = handler
        needs_privileged = (
            _route_requires_privileged_operations(methods)
            if require_privileged is None
            else require_privileged
        )
        if needs_privileged:
            protected_handler = _with_privileged_operations(context, protected_handler)
        return Route(path, _with_errors(protected_handler), methods=methods)

    return [
        route("/bootstrap", get_bootstrap, methods=["GET"]),
        route("/views/{view}", get_view, methods=["GET"]),
        route("/state", get_state, methods=["GET"]),
        route("/state/stream", stream_state, methods=["GET"]),
        route("/inbox", get_inbox_history, methods=["GET"]),
        route("/dispatch-events", get_dispatch_events, methods=["GET"]),
        route("/templates", list_templates, methods=["GET"]),
        route(
            "/templates/{template_id}",
            get_template_details,
            methods=["GET"],
        ),
        route(
            "/templates/{template_id}/materialize",
            materialize_template,
            methods=["POST"],
        ),
        route("/timeline", get_timeline, methods=["GET"]),
        route("/costs", get_costs, methods=["GET"]),
        route("/costs/backfill", backfill_costs, methods=["POST"]),
        route("/teams", create_team, methods=["POST"]),
        route("/teams/{team_id}/copy", copy_team, methods=["POST"]),
        route("/teams/{team_id}", patch_team, methods=["PATCH"]),
        route("/teams/{team_id}/cost-budget/reset", reset_team_cost_budget, methods=["POST"]),
        route("/governance-groups", create_governance_group, methods=["POST"]),
        route(
            "/governance-groups/{governance_group_id}",
            patch_governance_group,
            methods=["PATCH"],
        ),
        route(
            "/governance-agenda",
            create_governance_agenda_item,
            methods=["POST"],
        ),
        route(
            "/governance-agenda/{agenda_item_id}/handle",
            handle_governance_agenda_item,
            methods=["POST"],
        ),
        route("/projects", create_project, methods=["POST"]),
        route("/projects/git/import", import_project_git_repo, methods=["POST"]),
        route("/projects/{project_id}/lead", transfer_project_lead, methods=["POST"]),
        route("/projects/{project_id}", patch_project, methods=["PATCH"]),
        route("/roles", create_role, methods=["POST"]),
        route("/roles", list_roles, methods=["GET"]),
        route("/roles/{role_id}", get_role, methods=["GET"]),
        route("/roles/{role_id}", patch_role, methods=["PATCH"]),
        route("/conversations", list_conversations, methods=["GET"]),
        route(
            "/conversations",
            create_conversation,
            methods=["POST"],
            require_privileged=False,
        ),
        route(
            "/conversations/{conversation_id}",
            get_conversation,
            methods=["GET"],
        ),
        route(
            "/conversations/{conversation_id}/stream",
            stream_conversation,
            methods=["GET"],
        ),
        route(
            "/conversations/{conversation_id}/messages",
            list_conversation_messages,
            methods=["GET"],
        ),
        route(
            "/conversations/{conversation_id}/messages",
            send_conversation_message,
            methods=["POST"],
            require_privileged=False,
        ),
        route(
            "/conversations/{conversation_id}/attachments/{attachment_id}",
            get_chat_attachment,
            methods=["GET"],
        ),
        route(
            "/chat/attachment-policy",
            get_chat_attachment_policy,
            methods=["GET"],
        ),
        route(
            "/chat/attachment-policy",
            put_chat_attachment_policy,
            methods=["PUT"],
        ),
        route("/agents", register_agent, methods=["POST"]),
        route("/agents/{agent_id}", patch_agent, methods=["PATCH"]),
        route(
            "/agents/{agent_id}/voice-sessions",
            create_agent_voice_session,
            methods=["POST"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/events",
            record_agent_voice_event,
            methods=["POST"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/end",
            end_agent_voice_session,
            methods=["POST"],
        ),
        route(
            "/voice-session-requests/{request_id}/end",
            end_agent_voice_session_by_request,
            methods=["POST"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/queries",
            create_agent_voice_query,
            methods=["POST"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/queries/{query_id}",
            get_agent_voice_query,
            methods=["GET"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/queries/{query_id}/timeout",
            timeout_agent_voice_query,
            methods=["POST"],
        ),
        route(
            "/agents/{agent_id}/avatar/previews",
            create_avatar_preview,
            methods=["POST"],
        ),
        route(
            "/agents/{agent_id}/avatar/upload",
            upload_agent_avatar,
            methods=["POST"],
        ),
        route(
            "/agents/{agent_id}/avatar/accept-preview",
            accept_agent_avatar_preview,
            methods=["POST"],
        ),
        route(
            "/agents/{agent_id}/avatar",
            delete_agent_avatar,
            methods=["DELETE"],
        ),
        route(
            "/avatars/{asset_id}",
            get_avatar_asset,
            methods=["GET"],
        ),
        route(
            "/avatar-previews/{preview_id}",
            get_avatar_preview,
            methods=["GET"],
        ),
        route(
            "/agents/{agent_id}/context-rotation",
            request_agent_context_rotation,
            methods=["POST"],
        ),
        route(
            "/agents/{agent_id}/stand-down",
            stand_down_agent,
            methods=["POST"],
        ),
        route(
            "/agents/{agent_id}/stand-down/revoke",
            revoke_agent_stand_down,
            methods=["POST"],
        ),
        route("/cases", create_case, methods=["POST"]),
        route("/cases/{case_id}", patch_case, methods=["PATCH"]),
        route("/tasks", create_task, methods=["POST"]),
        route("/tasks/{task_id}", patch_task, methods=["PATCH"]),
        route(
            "/tasks/{task_id}/repair-state",
            repair_task_state,
            methods=["POST"],
        ),
        route("/tasks/{task_id}/cancel", cancel_task, methods=["POST"]),
        route(
            "/tasks/{task_id}/repair-cancelled-dependencies",
            repair_cancelled_task_dependencies,
            methods=["POST"],
        ),
        route(
            "/tasks/{task_id}/recover-submitted-integration-boundary",
            recover_submitted_integration_boundary,
            methods=["POST"],
        ),
        route("/deliverables", record_deliverable, methods=["POST"]),
        route(
            "/deliverables/{deliverable_id}/acceptance-verification-waiver",
            authorize_deliverable_acceptance_verification_waiver,
            methods=["POST"],
        ),
        route("/review-requests", record_review_request, methods=["POST"]),
        route(
            "/review-requests/{review_request_id}/decision",
            decide_review_request,
            methods=["POST"],
        ),
        route(
            "/review-requests/{review_request_id}/withdraw",
            withdraw_review_request,
            methods=["POST"],
        ),
        route(
            "/review-requests/{review_request_id}/retire",
            retire_review_request,
            methods=["POST"],
        ),
        route(
            "/operator-directives",
            create_operator_directive,
            methods=["POST"],
        ),
        route(
            "/external-source-evidence-snapshots",
            list_external_source_evidence_snapshots,
            methods=["GET"],
        ),
        route(
            "/external-source-evidence-snapshots",
            create_external_source_evidence_snapshot,
            methods=["POST"],
        ),
        route(
            "/external-source-evidence-snapshots/{snapshot_id}",
            get_external_source_evidence_snapshot,
            methods=["GET"],
        ),
        route(
            "/external-source-evidence-snapshots/{snapshot_id}/attachments",
            attach_external_source_evidence_snapshot,
            methods=["POST"],
        ),
        route("/memory", list_memories, methods=["GET"]),
        route("/memory", create_memory, methods=["POST"]),
        route("/memory/{memory_id}", get_memory, methods=["GET"]),
        route("/memory/{memory_id}", patch_memory, methods=["PATCH"]),
        route("/memory/{memory_id}/archive", archive_memory, methods=["POST"]),
        route(
            "/action-records/{action_record_id}/dismiss",
            dismiss_action_record,
            methods=["POST"],
        ),
        route(
            "/blockers/{blocker_id}/resolve",
            resolve_terminal_blocker,
            methods=["POST"],
        ),
        route(
            "/blockers/{blocker_id}/dependency-composition-recovery",
            start_dependency_composition_recovery,
            methods=["POST"],
        ),
        route(
            "/candidate-correction-recoveries/{blocker_id}",
            recover_candidate_correction_successor,
            methods=["POST"],
        ),
        route(
            "/outcome-kinds",
            create_outcome_kind_definition,
            methods=["POST"],
        ),
        route(
            "/outcome-kinds",
            list_outcome_kind_definitions,
            methods=["GET"],
        ),
        route(
            "/outcome-kinds/{definition_id}",
            get_outcome_kind_definition,
            methods=["GET"],
        ),
        route(
            "/outcome-kinds/{definition_id}",
            patch_outcome_kind_definition,
            methods=["PATCH"],
        ),
        route("/review-profiles", create_review_profile, methods=["POST"]),
        route("/review-profiles", list_review_profiles, methods=["GET"]),
        route(
            "/review-profiles/{review_profile_id}",
            get_review_profile,
            methods=["GET"],
        ),
        route(
            "/review-profiles/{review_profile_id}",
            patch_review_profile,
            methods=["PATCH"],
        ),
        route("/release-packages", create_release_package, methods=["POST"]),
        route("/release-packages", list_release_packages, methods=["GET"]),
        route(
            "/human-review-requests",
            list_human_review_requests,
            methods=["GET"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}",
            get_human_review_request,
            methods=["GET"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/review-bundle",
            download_human_review_bundle,
            methods=["GET"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/decisions",
            decide_human_review,
            methods=["POST"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/reassign",
            reassign_human_review_request,
            methods=["POST"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/resume",
            resume_human_review_request,
            methods=["POST"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/preparation-result",
            record_human_review_preparation,
            methods=["POST"],
        ),
        route(
            "/human-review-findings/{human_review_finding_id}/resolution-proposal",
            propose_human_finding_resolution,
            methods=["POST"],
        ),
        route(
            "/human-review-findings/{human_review_finding_id}/route",
            route_human_finding_correction,
            methods=["POST"],
        ),
        route(
            "/cases/{case_id}/close-post-release",
            close_post_release_feedback_case,
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/report-issue",
            report_release_issue,
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/retry-gate",
            retry_release_gate,
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/cancel",
            cancel_release_package,
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/gate-obligation-waivers",
            authorize_release_gate_obligation_waiver,
            methods=["POST"],
        ),
        route(
            "/worktree-cleanup-candidates/{cleanup_candidate_id}/retry",
            retry_worktree_cleanup,
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/verification-live",
            get_release_verification_live,
            methods=["GET"],
        ),
        route(
            "/release-packages/{release_package_id}",
            get_release_package,
            methods=["GET"],
        ),
        route("/git-promotions", list_git_promotions, methods=["GET"]),
        route(
            "/git-promotions/{promotion_id}",
            get_git_promotion,
            methods=["GET"],
        ),
    ]


def _with_errors(handler):
    async def _wrapped(request: Request) -> JSONResponse:
        try:
            return await handler(request)
        except TeamStateConflictError as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "expected_revision": exc.expected_revision,
                    "current_revision": exc.current_revision,
                },
                status_code=409,
            )
        except TeamIdempotencyConflictError as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "request_id": exc.request_id,
                    "existing_digest": exc.existing_digest,
                    "attempted_digest": exc.attempted_digest,
                },
                status_code=409,
            )
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except TaskGitBaseValidationError as exc:
            return JSONResponse(
                {"error": exc.message, "code": exc.code},
                status_code=400,
            )
        except ReleasePackageValidationError as exc:
            return JSONResponse(
                {"error": exc.message, "code": exc.code},
                status_code=400,
            )
        except (ValueError, TypeError, ValidationError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return _wrapped


def _route_requires_privileged_operations(methods: Sequence[str]) -> bool:
    return any(str(method).upper() in {"DELETE", "PATCH", "POST", "PUT"} for method in methods)


def _with_privileged_operations(context: AddonContext, handler):
    async def _wrapped(request: Request) -> JSONResponse:
        if locked_response := _privileged_operations_locked_response(context, request):
            return locked_response
        return await handler(request)

    return _wrapped


def _request_has_unlocked_privileged_operations(context: AddonContext, request: Request) -> bool:
    auth = getattr(context, "auth", None)
    if auth is None:
        return False
    return request_has_privileged_operations(
        auth=auth,
        service=getattr(context, "service", None),
        request=request,
    )


def _privileged_operations_locked_response(
    context: AddonContext, request: Request
) -> JSONResponse | None:
    if not _request_has_unlocked_privileged_operations(context, request):
        return JSONResponse({"error": "Privileged operations are locked."}, status_code=403)
    return None


def _request_wants_view_scoped_response(request: Request) -> bool:
    scope = str(request.headers.get("X-Team-Addon-Response-Scope") or "").strip().casefold()
    if scope in {"view", "scoped"}:
        return True
    query_scope = str(request.query_params.get("response_scope") or "").strip().casefold()
    return query_scope in {"view", "scoped"}


def _mutation_view_response_payload(
    state: TeamState,
    *,
    rebased_from_revision: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "response_scope": "view",
        "revision": state.revision,
        "invalidated_views": list(TEAM_VIEW_IDS),
        "resource_tokens": {},
    }
    if rebased_from_revision is not None:
        payload["rebased_from_revision"] = rebased_from_revision
    return payload


def _state_revision_payload(revision: int) -> dict[str, Any]:
    return {
        "response_scope": "revision",
        "revision": revision,
    }


def _session_repository_from_context(context: AddonContext) -> Any | None:
    service = getattr(context, "service", None)
    runtime = getattr(service, "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    if repository is not None:
        return repository
    return getattr(service, "session_repository", None)


def _conversation_agent_directory_path(
    context: AddonContext,
    state: TeamState,
    conversation: Any,
) -> str:
    return _strict_conversation_agent_directory_path(context, state, conversation) or "root"


def _strict_conversation_agent_directory_path(
    context: AddonContext,
    state: TeamState,
    conversation: Any,
) -> str | None:
    agent = state.agents.get(conversation.agent_id)
    if agent is None:
        return None
    repository = _session_repository_from_context(context)
    get_session = getattr(repository, "get_session", None)
    if not callable(get_session):
        return None
    try:
        session = get_session(agent.session_id)
    except Exception:
        return None
    if session is None or not getattr(session, "directory_path", None):
        return None
    try:
        return normalize_directory_path(session.directory_path)
    except (TypeError, ValueError):
        return None


def _team_chat_mutation_authorization_for_request(
    context: AddonContext,
    request: Request,
    state: TeamState,
    conversation: Any,
    *,
    principal_id: str,
):
    return _team_chat_mutation_authorization(
        auth=context.auth,
        service=getattr(context, "service", None),
        session_repository=_session_repository_from_context(context),
        request=request,
        state=state,
        conversation=conversation,
        principal_id=principal_id,
    )


def _team_chat_mutation_authorization(
    *,
    auth: Any | None,
    service: Any | None,
    session_repository: Any | None,
    request: Request,
    state: TeamState,
    conversation: Any,
    principal_id: str,
):
    agent = state.agents.get(conversation.agent_id)
    get_session = getattr(session_repository, "get_session", None)
    session = None
    if agent is not None and callable(get_session):
        try:
            session = get_session(agent.session_id)
        except Exception:
            session = None
    raw_directory_path = getattr(session, "directory_path", None)
    try:
        recipient_directory_path = (
            normalize_directory_path(raw_directory_path) if raw_directory_path else None
        )
    except (TypeError, ValueError):
        recipient_directory_path = None
    if recipient_directory_path is None:
        return reply_only_team_chat_authorization(
            source="admin_rbac",
            principal_id=principal_id,
            reason="recipient_directory_unresolved",
        )
    if auth is None or not request_has_privileged_operations(
        auth=auth,
        service=service,
        request=request,
    ):
        return reply_only_team_chat_authorization(
            source="admin_rbac",
            principal_id=principal_id,
            recipient_directory_path=recipient_directory_path,
            reason="privileged_operations_locked",
        )
    if not _auth_can(
        auth,
        request,
        TEAM_CHATS_MUTATE_PERMISSION,
        directory_path=recipient_directory_path,
    ):
        return reply_only_team_chat_authorization(
            source="admin_rbac",
            principal_id=principal_id,
            recipient_directory_path=recipient_directory_path,
            reason="permission_denied",
        )
    if agent is None:
        return reply_only_team_chat_authorization(
            source="admin_rbac",
            principal_id=principal_id,
            recipient_directory_path=recipient_directory_path,
            reason="recipient_agent_missing",
        )
    return project_scoped_team_chat_authorization(
        state,
        agent=agent,
        source="admin_rbac",
        principal_id=principal_id,
        recipient_directory_path=recipient_directory_path,
        project_is_authorized=lambda directory_path: _auth_can(
            auth,
            request,
            TEAM_CHATS_MUTATE_PERMISSION,
            directory_path=directory_path,
        ),
    )


def _agent_execution_records_by_session(service: Any | None) -> dict[str, Any] | None:
    list_agents = getattr(service, "list_agents", None)
    if not callable(list_agents):
        return None
    try:
        records = list_agents(
            include_inactive=True,
            direct_only=False,
            all_directories=True,
            reconcile_containers=False,
            omit_vm_state=True,
            summary_only=True,
            backend="container_worker_vm",
        )
    except Exception:
        return None
    by_session: dict[str, Any] = {}
    for record in records:
        session_id = str(getattr(record, "session_id", "") or "").strip()
        if session_id:
            by_session[session_id] = record
    return by_session


def _runtime_from_context(context: AddonContext) -> Any:
    service = getattr(context, "service", None)
    runtime = getattr(service, "runtime", None)
    if runtime is None and service is not None and hasattr(service, "voice_session_store"):
        runtime = service
    if runtime is None:
        raise RuntimeError("Admin runtime is unavailable")
    return runtime


def _release_verification_live_log_limit(request: Request) -> int:
    raw_limit = request.query_params.get("limit", "160")
    try:
        parsed = int(raw_limit)
    except (TypeError, ValueError):
        return 160
    return max(20, min(parsed, 400))


def _release_verification_live_payload(
    state: TeamState,
    release_package_id: str,
    directory_path: str,
    provider_config: Any,
    limit: int,
) -> dict[str, Any]:
    """Read a bounded worker-log tail without ever returning worker credentials."""

    release = state.release_packages.get(release_package_id)
    if release is None:
        raise ValueError("release package not found")
    runs = [
        state.release_verification_runs[run_id]
        for run_id in release.verification_run_ids
        if run_id in state.release_verification_runs
        and state.release_verification_runs[run_id].superseded_by_verification_run_id is None
    ]
    runs.sort(key=lambda run: (run.attempt_number, run.created_at, run.id), reverse=True)
    run = runs[0] if runs else None
    if run is None:
        return {
            "release_package_id": release.id,
            "run": None,
            "log": {"available": False, "message": "No verification run has been created."},
        }
    command_index = (
        min(max(run.current_command_index, 0), len(run.command_results) - 1)
        if run.command_results
        else 0
    )
    command = run.command_results[command_index] if run.command_results else None
    run_payload: dict[str, Any] = {
        "id": run.id,
        "state": run.state,
        "attempt_number": run.attempt_number,
        "current_command": (
            {
                "index": command_index,
                "name": command.name,
                "state": command.state,
            }
            if command is not None
            else None
        ),
    }
    if run.state != "running" or command is None or not command.job_id:
        return {
            "release_package_id": release.id,
            "run": run_payload,
            "log": {
                "available": False,
                "message": (
                    "Waiting for the verifier to start the current command."
                    if run.state == "running"
                    else "The verifier is not currently running."
                ),
            },
        }
    try:
        config = provider_config.resolve_for_directory(directory_path)
        worker_url = str(getattr(config, "container_worker_base_url", "") or "").strip()
        if not worker_url:
            raise ValueError("container worker is not configured")
        worker_client = ContainerWorkerClient(
            base_url=worker_url,
            token_env=str(getattr(config, "container_worker_token_env", "EVANDER_WORKER_TOKEN")),
            timeout_seconds=float(
                getattr(config, "container_worker_request_timeout_seconds", 30.0)
            ),
        )
        log_text, metadata = worker_client.logs_page(
            command.job_id,
            mode="raw",
            limit=limit,
        )
    except (ContainerWorkerClientError, ValueError, TypeError, AttributeError):
        return {
            "release_package_id": release.id,
            "run": run_payload,
            "log": {
                "available": False,
                "message": "Live verifier output is temporarily unavailable.",
            },
        }
    return {
        "release_package_id": release.id,
        "run": run_payload,
        "log": {
            "available": True,
            "text": log_text,
            "returned_lines": metadata.get("returned_lines"),
            "has_more_before": bool(metadata.get("has_more_before")),
        },
    }


def _team_state_payload_for_request(
    auth: Any | None,
    request: Request,
    state: TeamState,
    *,
    runtime_dir: Path | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = team_state_payload(
        state,
        session_records=session_records,
        agent_execution_records=agent_execution_records,
    )
    from .services.domain.project_status import project_blocking_sources

    for project_id, project_payload in payload.get("projects", {}).items():
        if isinstance(project_payload, dict):
            project_payload["blocking_sources"] = project_blocking_sources(
                state,
                project_id,
            )
    apply_compact_human_review_projection(payload, state)
    _add_team_resource_tokens(payload, state)
    _filter_external_source_evidence_payload(
        payload,
        allowed_snapshot_ids=_authorized_external_source_evidence_snapshot_ids(
            auth,
            request,
            state,
        ),
    )
    if runtime_dir is not None:
        _add_context_reset_usage(payload, state, runtime_dir=runtime_dir)
    if not _can_view_all_team_chats(auth, request):
        identity = _operator_identity_from_request(auth, request)
        allowed_conversation_ids = {
            conversation.id
            for conversation in state.conversations.values()
            if conversation.operator_user_id == identity["user_id"]
        }
        payload["conversations"] = {
            conversation_id: conversation_payload
            for conversation_id, conversation_payload in payload.get("conversations", {}).items()
            if conversation_id in allowed_conversation_ids
        }
        payload["conversation_messages"] = {
            message_id: message_payload
            for message_id, message_payload in payload.get("conversation_messages", {}).items()
            if isinstance(message_payload, Mapping)
            and message_payload.get("conversation_id") in allowed_conversation_ids
        }
        payload["work_lineages"] = filter_work_lineage_payloads(
            payload.get("work_lineages"),
            visible_conversation_ids=allowed_conversation_ids,
        )
        filter_work_lineage_references(
            payload,
            visible_lineage_ids=set(payload["work_lineages"]),
        )
    _window_state_history_payload(
        payload,
        state,
        history_limit=_query_state_history_limit(request),
    )
    return payload


def _add_release_outbox_metadata(
    payload: dict[str, Any],
    repository: TeamStateRepository,
) -> None:
    release_packages = payload.get("release_packages")
    if not isinstance(release_packages, dict) or not release_packages:
        return
    promotion_to_release: dict[str, dict[str, Any]] = {}
    for release_payload in release_packages.values():
        if not isinstance(release_payload, dict):
            continue
        for promotion_id in release_payload.get("git_promotion_record_ids") or []:
            if isinstance(promotion_id, str) and promotion_id:
                promotion_to_release[promotion_id] = release_payload
    if not promotion_to_release:
        return
    for record in repository.list_outbox_records(intent_type=GIT_PROMOTION_EVENT_TYPE):
        if record.aggregate_id not in promotion_to_release:
            continue
        release_payload = promotion_to_release[record.aggregate_id]
        release_payload["promotion_attempt_count"] = max(
            int(release_payload.get("promotion_attempt_count") or 0),
            record.attempt_count,
        )
        row = {
            "id": record.id,
            "status": record.status.value,
            "aggregate_id": record.aggregate_id,
            "available_at": record.available_at.isoformat(),
            "attempt_count": record.attempt_count,
            "claimed_by": record.claimed_by,
            "completed_at": (
                record.completed_at.isoformat() if record.completed_at is not None else None
            ),
            "last_error_code": record.last_error_code,
            "last_error_message": record.last_error_message,
        }
        release_payload.setdefault("outbox_records", []).append(
            {key: value for key, value in row.items() if value is not None}
        )
        if record.status in {TeamOutboxStatus.PENDING, TeamOutboxStatus.CLAIMED}:
            release_payload.setdefault("active_outbox_record_ids", []).append(record.id)


def _bootstrap_payload_for_request(
    auth: Any | None,
    request: Request,
    state: TeamState,
    *,
    communication_targets: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    identity = _operator_identity_from_request(auth, request)
    scope = _chat_scope_for_operator(auth, request, state, identity["user_id"], identity=identity)
    selected_operator_user_id = str(scope["selected_operator"]["user_id"])
    return {
        "revision": state.revision,
        "views": list(TEAM_VIEW_IDS),
        "default_view": "people",
        "generated_at": utc_now().isoformat(),
        "current_operator": scope["current_operator"],
        "selected_operator": scope["selected_operator"],
        "can_view_all_operators": bool(scope.get("can_view_all_operators")),
        "operator_options": _operator_options(state, scope=scope),
        "communication_targets": list(communication_targets or ()),
        "counts": {
            "teams": len(state.teams),
            "governance_groups": len(state.governance_groups),
            "projects": len(state.projects),
            "agents": len(state.agents),
            "cases": len(state.cases),
            "tasks": len(state.tasks),
            "deliverables": len(state.deliverables),
            "review_requests": len(state.review_requests),
            "conversations": sum(
                1
                for conversation in state.conversations.values()
                if conversation.operator_user_id == selected_operator_user_id
            ),
        },
    }


def _registered_communication_target_options(
    context: AddonContext,
) -> list[dict[str, Any]]:
    """Return every root-directory communication target for Team configuration."""

    service = context.service
    runtime = getattr(service, "runtime", service)
    directory_store = getattr(runtime, "directories", None)
    if directory_store is None:
        return []
    try:
        targets = load_communication_targets_config(
            runtime_dir=context.runtime_dir,
            directory_store=directory_store,
            directory_path="root",
        ).targets
    except (OSError, ValueError):
        return []
    options: list[dict[str, Any]] = []
    for target in targets:
        capabilities = sorted(target.policy.capabilities)
        capability_suffix = f", {', '.join(capabilities)}" if capabilities else ""
        options.append(
            {
                "value": target.id,
                "label": f"{target.id} ({target.provider}/{target.kind}{capability_suffix})",
            }
        )
    return options


def _view_payload_for_request(
    auth: Any | None,
    request: Request,
    state: TeamState,
    *,
    view: str,
    runtime_dir: Path | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    session_repository: Any | None = None,
) -> dict[str, Any]:
    generated_at = utc_now().isoformat()
    if view == "chat":
        scope = _chat_list_scope(auth, request, state)
        scope = _add_team_chat_mutation_modes(
            scope,
            auth=auth,
            service=None,
            session_repository=session_repository,
            request=request,
            state=state,
        )
        selected_conversation_id = _query_text(request, "selected_conversation_id")
        chat_payload = _conversation_collection_payload(
            state,
            scope=scope,
            selected_conversation_id=selected_conversation_id,
            include_selected_messages=True,
        )
        agent_ids = {
            str(conversation.get("agent_id"))
            for conversation in chat_payload.get("conversations", [])
            if isinstance(conversation, Mapping) and conversation.get("agent_id")
        }
        agent_ids.update(
            str(agent.get("id"))
            for agent in chat_payload.get("eligible_agents", [])
            if isinstance(agent, Mapping) and agent.get("id")
        )
        agent_state = {
            agent_id: _chat_agent_payload(state.agents.get(agent_id))
            for agent_id in agent_ids
            if state.agents.get(agent_id) is not None
        }
        return {
            "revision": state.revision,
            "view": view,
            "generated_at": generated_at,
            "data": {
                "chat": chat_payload,
                "state": {
                    "revision": state.revision,
                    "agents": agent_state,
                    "resource_tokens": _team_resource_tokens_for_collections(
                        state,
                        {"agents"},
                        ids_by_collection={"agents": agent_ids},
                    ),
                },
            },
            "resource_tokens": _team_resource_tokens_for_collections(
                state,
                {"agents"},
                ids_by_collection={"agents": agent_ids},
            ),
        }

    collections = TEAM_VIEW_COLLECTIONS.get(view, TEAM_VIEW_COLLECTIONS["workspace"])
    ids_by_collection = _view_ids_by_collection(state, view)
    ids_by_collection["external_source_evidence_snapshots"] = (
        _authorized_external_source_evidence_snapshot_ids(auth, request, state)
    )
    data: dict[str, Any] = {
        "state": _state_slice_payload_for_request(
            auth,
            request,
            state,
            collections=collections,
            ids_by_collection=ids_by_collection,
            runtime_dir=runtime_dir,
            session_records=session_records,
            agent_execution_records=agent_execution_records,
            session_repository=session_repository,
        )
    }
    if view == "timeline":
        data["timeline"] = _timeline_payload_for_request(request, state)
    return {
        "revision": state.revision,
        "view": view,
        "generated_at": generated_at,
        "data": data,
        "resource_tokens": _team_resource_tokens_for_collections(
            state,
            collections,
            ids_by_collection=ids_by_collection,
        ),
    }


def _timeline_payload_for_request(request: Request, state: TeamState) -> dict[str, Any]:
    team_id = _query_text(request, "team_id")
    project_id = _query_text(request, "project_id")
    agent_id = _query_text(request, "agent_id")
    types = _query_values(request, "type")
    statuses = _query_values(request, "status")
    attention_needed = _query_bool(request, "attention_needed")
    limit = _query_limited_int(
        request,
        "limit",
        default=DEFAULT_TIMELINE_LIMIT,
        max_value=MAX_TIMELINE_LIMIT,
    )
    offset = _query_non_negative_int(request, "offset", default=0)
    filters: dict[str, object] = {}
    if team_id is not None:
        filters["team_id"] = team_id
    if project_id is not None:
        filters["project_id"] = project_id
    if agent_id is not None:
        filters["agent_id"] = agent_id
    if types is not None:
        filters["type"] = types
    if statuses is not None:
        filters["status"] = statuses
    if attention_needed is not None:
        filters["attention_needed"] = attention_needed
    return {
        **derive_team_timeline(
            state,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
            types=types,
            statuses=statuses,
            attention_needed=attention_needed,
            limit=limit,
            offset=offset,
        ),
        "source": "derived_from_current_team_state",
        "kind": "activity_timeline",
        "completeness": "retained_current_state_only",
        "retention_boundary": (
            "Only records and timestamps retained in the current TeamState snapshot are "
            "represented."
        ),
        "description": (
            "Activity reconstructed from records retained in the current TeamState snapshot; "
            "earlier transitions, deleted or compacted records, and previous field values may "
            "be unavailable. This is not an audit log."
        ),
        "filters": filters,
    }


def _inbox_history_payload_for_request(request: Request, state: TeamState) -> dict[str, Any]:
    team_id = _query_text(request, "team_id")
    agent_id = _query_text(request, "agent_id")
    direction = (_query_text(request, "direction") or "all").casefold()
    item_type = _query_text(request, "item_type")
    item_state = _query_text(request, "state")
    search_text = _query_text(request, "q")
    limit = _query_limited_int(
        request,
        "limit",
        default=DEFAULT_INBOX_HISTORY_LIMIT,
        max_value=MAX_INBOX_HISTORY_LIMIT,
    )
    offset = _query_non_negative_int(request, "offset", default=0)

    if direction not in {"all", "received", "sent"}:
        raise ValueError("direction must be all, received, or sent")
    if direction != "all" and not agent_id:
        raise ValueError("agent_id is required when filtering inbox direction")
    if item_type and item_type not in {value.value for value in TeamInboxItemType}:
        raise ValueError(f"unknown inbox item_type: {item_type}")
    if item_state and item_state not in {value.value for value in TeamInboxItemState}:
        raise ValueError(f"unknown inbox state: {item_state}")

    rows = list(state.inbox.values())
    if team_id:
        team_agent_ids = {agent.id for agent in state.agents.values() if agent.team_id == team_id}
        rows = [
            item
            for item in rows
            if item.recipient_agent_id in team_agent_ids or item.sender_agent_id in team_agent_ids
        ]
    if agent_id:
        if direction == "received":
            rows = [item for item in rows if item.recipient_agent_id == agent_id]
        elif direction == "sent":
            rows = [item for item in rows if item.sender_agent_id == agent_id]
        else:
            rows = [
                item
                for item in rows
                if item.recipient_agent_id == agent_id or item.sender_agent_id == agent_id
            ]
    if item_type:
        rows = [item for item in rows if item.item_type.value == item_type]
    if search_text:
        needle = search_text.casefold()
        rows = [item for item in rows if needle in _inbox_history_search_text(state, item)]

    state_counts = {
        value.value: sum(1 for item in rows if item.state == value) for value in TeamInboxItemState
    }
    if item_state:
        rows = [item for item in rows if item.state.value == item_state]

    rows.sort(key=_inbox_history_sort_key, reverse=True)
    total_count = len(rows)
    page = rows[offset : offset + limit]
    filters = {
        key: value
        for key, value in {
            "team_id": team_id,
            "agent_id": agent_id,
            "direction": direction if direction != "all" else None,
            "item_type": item_type,
            "state": item_state,
            "q": search_text,
        }.items()
        if value is not None
    }
    return {
        "revision": state.revision,
        "generated_at": state.updated_at.isoformat(),
        "filters": filters,
        "state_counts": state_counts,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total_count,
        "items": [
            _inbox_history_item_payload(state, item, focus_agent_id=agent_id) for item in page
        ],
    }


def _inbox_history_item_payload(
    state: TeamState,
    item: Any,
    *,
    focus_agent_id: str | None,
) -> dict[str, Any]:
    payload = item.model_dump(mode="json")
    payload["sender"] = _inbox_history_agent_payload(state, item.sender_agent_id)
    payload["recipient"] = _inbox_history_agent_payload(state, item.recipient_agent_id)
    task = state.tasks.get(item.task_id) if item.task_id else None
    payload["task"] = (
        {
            "id": task.id,
            "title": task.title,
            "project_id": task.project_id,
            "state": task.state.value,
        }
        if task is not None
        else None
    )
    if focus_agent_id and item.sender_agent_id == focus_agent_id:
        payload["direction"] = "sent"
    elif focus_agent_id and item.recipient_agent_id == focus_agent_id:
        payload["direction"] = "received"
    else:
        payload["direction"] = "internal" if item.sender_agent_id else "system"
    return payload


def _inbox_history_agent_payload(state: TeamState, agent_id: str | None) -> dict[str, Any] | None:
    if not agent_id:
        return None
    agent = state.agents.get(agent_id)
    if agent is None:
        return {"id": agent_id, "display_name": agent_id}
    role = state.roles.get(agent.role_id) if agent.role_id else None
    team = state.teams.get(agent.team_id) if agent.team_id else None
    return {
        "id": agent.id,
        "display_name": agent.display_name or agent.id,
        "team_id": agent.team_id,
        "team_name": team.name if team is not None else None,
        "role_name": role.name if role is not None else None,
        "avatar": agent.avatar.model_dump(mode="json") if agent.avatar is not None else None,
    }


def _inbox_history_search_text(state: TeamState, item: Any) -> str:
    sender = state.agents.get(item.sender_agent_id) if item.sender_agent_id else None
    recipient = state.agents.get(item.recipient_agent_id)
    values = [
        item.id,
        item.item_type.value,
        item.state.value,
        item.priority.value,
        item.sender_agent_id or "",
        sender.display_name if sender is not None else "",
        item.recipient_agent_id,
        recipient.display_name if recipient is not None else "",
        item.task_id or "",
        json.dumps(item.payload, sort_keys=True, ensure_ascii=False),
    ]
    return "\n".join(str(value or "") for value in values).casefold()


def _inbox_history_sort_key(item: Any) -> tuple[float, str]:
    timestamp = item.created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return (timestamp.timestamp(), item.id)


def _latest_task_preparation_invalidation_ids(state: TeamState) -> set[str]:
    latest_by_task_id: dict[str, Any] = {}
    for item in state.inbox.values():
        task_id = str(item.task_id or "").strip()
        if (
            not task_id
            or task_id not in state.tasks
            or item.payload.get("kind") != "task_preparation_invalidated"
        ):
            continue
        current = latest_by_task_id.get(task_id)
        if current is None:
            latest_by_task_id[task_id] = item
            continue
        item_timestamp = _inbox_history_sort_key(item)[0]
        current_timestamp = _inbox_history_sort_key(current)[0]
        if item_timestamp > current_timestamp or (
            item_timestamp == current_timestamp and item.id < current.id
        ):
            latest_by_task_id[task_id] = item
    return {item.id for item in latest_by_task_id.values()}


def _view_ids_by_collection(state: TeamState, view: str) -> dict[str, set[str]]:
    ids_by_collection: dict[str, set[str]] = {}
    if view in {"people", "org", "operations", "workspace"}:
        ids_by_collection["inbox"] = {
            item_id
            for item_id, item in state.inbox.items()
            if item.state != TeamInboxItemState.HANDLED
        }
    if view in {"planning", "task-board"}:
        ids_by_collection["inbox"] = _latest_task_preparation_invalidation_ids(state)
    if view in {"operations", "workspace"}:
        ids_by_collection["action_records"] = {
            record.id for record in current_failed_action_records(state)
        }
        open_blocker_ids = {
            blocker.id for blocker in state.blockers.values() if blocker.state.value == "open"
        }
        ids_by_collection["blockers"] = open_blocker_ids
        ids_by_collection["blocker_events"] = {
            event.id
            for event in state.blocker_events.values()
            if event.blocker_id in open_blocker_ids
        }
        ids_by_collection["dependency_composition_recoveries"] = {
            recovery.id
            for recovery in state.dependency_composition_recoveries.values()
            if recovery.blocker_id in open_blocker_ids
        }
    return ids_by_collection


def _state_slice_payload_for_request(
    auth: Any | None,
    request: Request,
    state: TeamState,
    *,
    collections: set[str],
    ids_by_collection: Mapping[str, set[str]] | None = None,
    runtime_dir: Path | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    session_repository: Any | None = None,
) -> dict[str, Any]:
    scoped_ids = dict(ids_by_collection or {})
    scoped_ids["external_source_evidence_snapshots"] = (
        _authorized_external_source_evidence_snapshot_ids(auth, request, state)
    )
    payload = state.model_dump(
        mode="json",
        include={"schema_version", "revision", "updated_at"},
    )
    payload["available_role_permissions"] = [permission.value for permission in RolePermission]
    for collection in TEAM_STATE_COLLECTION_KEYS:
        if collection not in collections:
            continue
        payload[collection] = _state_collection_slice_payload(
            state,
            collection,
            allowed_ids=scoped_ids.get(collection),
        )
    agent_ids = _state_slice_agent_ids(state, collections, scoped_ids)
    scoped_session_records = session_records
    if scoped_session_records is None:
        scoped_session_records = _session_records_for_agent_ids(
            session_repository,
            state,
            agent_ids,
        )
    runtime_agent_ids = None if agent_ids == set(state.agents) else agent_ids
    runtime_operations = runtime_operations_payload(
        state,
        session_records=scoped_session_records,
        agent_execution_records=agent_execution_records,
        agent_ids=runtime_agent_ids,
    )
    payload["runtime_operations"] = runtime_operations
    payload["agent_activity_summaries"] = agent_activity_summaries_payload(
        state,
        runtime_operations=runtime_operations,
        agent_ids=runtime_agent_ids,
    )
    for agent_id, summary in payload["agent_activity_summaries"].items():
        agent_payload = payload.get("agents", {}).get(agent_id)
        if isinstance(agent_payload, dict):
            agent_payload["activity_summary"] = summary
    if runtime_dir is not None:
        _add_context_reset_usage(payload, state, runtime_dir=runtime_dir)
    _add_team_resource_tokens_for_collections(
        payload,
        state,
        collections=collections,
        ids_by_collection=scoped_ids,
    )
    _filter_external_source_evidence_payload(
        payload,
        allowed_snapshot_ids=scoped_ids["external_source_evidence_snapshots"],
    )
    _window_state_history_payload(
        payload,
        state,
        history_limit=_query_state_history_limit(request),
    )
    return payload


def _add_context_reset_usage(
    payload: dict[str, Any],
    state: TeamState,
    *,
    runtime_dir: Path,
) -> None:
    """Attach reset-bounded input-token totals to eligible runtime payloads."""
    runtime_payloads = payload.get("agent_runtimes")
    if not isinstance(runtime_payloads, dict):
        return

    usage_store = None
    usage_store_unavailable = False
    if (Path(runtime_dir) / MODEL_USAGE_STORE_FILENAME).is_file():
        try:
            usage_store = model_usage_store(runtime_dir)
        except Exception:
            usage_store_unavailable = True

    for agent_id, runtime_payload in runtime_payloads.items():
        if not isinstance(runtime_payload, dict):
            continue
        runtime = state.agent_runtimes.get(agent_id)
        agent = state.agents.get(agent_id)
        if runtime is None or agent is None:
            continue
        policy = str(
            getattr(runtime.context_clearing_policy, "value", runtime.context_clearing_policy)
            or "off"
        )
        if policy in {"every_wake", "work_lineage"}:
            continue

        input_tokens_since_reset: int | None = None
        if runtime.last_context_rotated_at is not None:
            if usage_store_unavailable:
                input_tokens_since_reset = None
            elif usage_store is None:
                input_tokens_since_reset = 0
            else:
                try:
                    input_tokens_since_reset = usage_store.sum_input_tokens(
                        session_id=agent.session_id,
                        occurred_after=runtime.last_context_rotated_at,
                    )
                except Exception:
                    input_tokens_since_reset = None
        runtime_payload["context_reset_usage"] = {
            "input_tokens_since_reset": input_tokens_since_reset,
        }


def _state_collection_slice_payload(
    state: TeamState,
    collection: str,
    *,
    allowed_ids: set[str] | None = None,
) -> dict[str, Any]:
    if collection in HUMAN_REVIEW_COLLECTION_KEYS:
        return {}
    value = getattr(state, collection, {})
    if not isinstance(value, Mapping):
        return {}
    rows: dict[str, Any] = {}
    for item_id, item in value.items():
        if allowed_ids is not None and item_id not in allowed_ids:
            continue
        if collection == "agents":
            rows[item_id] = _agent_payload_with_role(state, item)
        elif collection == "tasks":
            rows[item_id] = task_payload_with_assessment(state, item)
        elif collection == "release_packages":
            rows[item_id] = release_package_api_payload(
                state,
                item,
                include_human_details=False,
            )
        elif collection == "memories":
            rows[item_id] = memory_record_payload(item, include_body=False)
        elif collection == "external_source_evidence_snapshots":
            rows[item_id] = external_source_evidence_snapshot_payload(item)
        elif hasattr(item, "model_dump"):
            rows[item_id] = item.model_dump(mode="json")
        else:
            rows[item_id] = item
    return rows


def _state_slice_agent_ids(
    state: TeamState,
    collections: set[str],
    ids_by_collection: Mapping[str, set[str]],
) -> set[str]:
    if "agents" in collections:
        allowed_ids = ids_by_collection.get("agents")
        if allowed_ids is not None:
            return {agent_id for agent_id in allowed_ids if agent_id in state.agents}
        return set(state.agents)
    return set()


def _session_records_for_agent_ids(
    repository: Any | None,
    state: TeamState,
    agent_ids: set[str],
) -> dict[str, Any] | None:
    records = _session_records_by_id(repository)
    if records is None or not agent_ids:
        return None
    session_ids: set[str] = set()
    for agent_id in sorted(agent_ids):
        agent = state.agents.get(agent_id)
        session_id = str(getattr(agent, "session_id", "") or "").strip() if agent else ""
        if session_id:
            session_ids.add(session_id)
    return {session_id: records[session_id] for session_id in session_ids if session_id in records}


def _filter_state_payload(
    payload: dict[str, Any],
    *,
    collections: set[str],
    ids_by_collection: Mapping[str, set[str]],
) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for key, value in payload.items():
        if key in TEAM_STATE_COLLECTION_KEYS:
            if key not in collections:
                continue
            allowed_ids = ids_by_collection.get(key)
            if allowed_ids is not None and isinstance(value, Mapping):
                filtered[key] = {
                    item_id: item for item_id, item in value.items() if item_id in allowed_ids
                }
                continue
        filtered[key] = value
    tokens = filtered.get("resource_tokens")
    if isinstance(tokens, Mapping):
        scoped_tokens: dict[str, Any] = {}
        for collection, collection_tokens in tokens.items():
            if collection not in collections:
                continue
            allowed_ids = ids_by_collection.get(str(collection))
            if allowed_ids is not None and isinstance(collection_tokens, Mapping):
                scoped_tokens[str(collection)] = {
                    item_id: token
                    for item_id, token in collection_tokens.items()
                    if item_id in allowed_ids
                }
            else:
                scoped_tokens[str(collection)] = collection_tokens
        filtered["resource_tokens"] = scoped_tokens
    return filtered


def _window_state_history_payload(
    payload: dict[str, Any],
    state: TeamState,
    *,
    history_limit: int,
) -> None:
    windows: dict[str, dict[str, Any]] = {}
    for collection in TEAM_STATE_HISTORY_COLLECTIONS:
        rows = payload.get(collection)
        if not isinstance(rows, Mapping):
            continue
        allowed_ids = {str(item_id) for item_id in rows}
        keep_ids = _state_history_keep_ids(
            state,
            collection,
            allowed_ids=allowed_ids,
            history_limit=history_limit,
        )
        payload[collection] = {
            item_id: item for item_id, item in rows.items() if str(item_id) in keep_ids
        }
        _trim_resource_tokens(payload, collection, keep_ids)
        returned_count = len(payload[collection])
        total_count = len(rows)
        windowed = returned_count < total_count
        windows[collection] = {
            "total_count": total_count,
            "returned_count": returned_count,
            "limit": history_limit,
            "windowed": windowed,
        }
        payload[f"{collection}_total_count"] = total_count
        payload[f"{collection}_returned_count"] = returned_count
        payload[f"{collection}_windowed"] = windowed
    if windows:
        payload["history_windows"] = windows


def _state_history_keep_ids(
    state: TeamState,
    collection: str,
    *,
    allowed_ids: set[str],
    history_limit: int,
) -> set[str]:
    source = getattr(state, collection, {})
    if not isinstance(source, Mapping) or not allowed_ids:
        return set()
    if collection == "action_records":
        current_ids = {
            record.id for record in current_failed_action_records(state) if record.id in allowed_ids
        }
    else:
        current_ids = {
            str(item_id)
            for item_id, item in source.items()
            if str(item_id) in allowed_ids and _state_history_item_is_current(collection, item)
        }
    newest_ids = [
        str(item_id)
        for item_id, _item in sorted(
            (
                (str(item_id), item)
                for item_id, item in source.items()
                if str(item_id) in allowed_ids and str(item_id) not in current_ids
            ),
            key=lambda row: _state_history_sort_key(row[0], row[1]),
            reverse=True,
        )
    ]
    keep_ids = set(current_ids)
    # Release history has its own allowance so active work cannot hide publications.
    remaining_slots = (
        history_limit
        if collection == "release_packages"
        else max(0, history_limit - len(keep_ids))
    )
    keep_ids.update(newest_ids[:remaining_slots])
    return keep_ids


def _state_history_item_is_current(collection: str, item: Any) -> bool:
    if collection == "inbox":
        return _record_state_value(item) != TeamInboxItemState.HANDLED.value
    if collection == "release_packages":
        return _record_state_value(item) not in {
            TeamRecordState.ACCEPTED.value,
            TeamRecordState.REJECTED.value,
            TeamRecordState.CANCELLED.value,
        }
    if collection == "blockers":
        return _record_state_value(item) == "open"
    if collection == "dependency_composition_recoveries":
        return _record_state_value(item) in {"awaiting_assignment", "in_progress"}
    if collection == "ephemeral_assignments":
        return _record_state_value(item) not in {
            "accepted",
            "cleaned_up",
            "completed",
        }
    if collection == "git_promotions":
        return _record_state_value(item) != "succeeded"
    return False


def _record_state_value(item: Any) -> str:
    value = getattr(item, "state", "") or ""
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _state_history_sort_key(item_id: str, item: Any) -> tuple[float, str]:
    for field_name in (
        "updated_at",
        "last_interaction_at",
        "published_at",
        "decision_at",
        "created_at",
    ):
        timestamp = getattr(item, field_name, None)
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            return (timestamp.timestamp(), item_id)
    return (float("-inf"), item_id)


def _trim_resource_tokens(payload: dict[str, Any], collection: str, keep_ids: set[str]) -> None:
    tokens = payload.get("resource_tokens")
    if not isinstance(tokens, dict):
        return
    collection_tokens = tokens.get(collection)
    if not isinstance(collection_tokens, Mapping):
        return
    tokens[collection] = {
        item_id: token for item_id, token in collection_tokens.items() if str(item_id) in keep_ids
    }


def _add_team_resource_tokens(payload: dict[str, Any], state: TeamState) -> None:
    tokens = _team_resource_tokens(state)
    payload["resource_tokens"] = tokens
    for collection, collection_tokens in tokens.items():
        rows = payload.get(collection)
        if not isinstance(rows, dict):
            continue
        for item_id, token in collection_tokens.items():
            row = rows.get(item_id)
            if isinstance(row, dict):
                row["resource_token"] = token


def _add_team_resource_tokens_for_collections(
    payload: dict[str, Any],
    state: TeamState,
    *,
    collections: set[str],
    ids_by_collection: Mapping[str, set[str]],
) -> None:
    tokens = _team_resource_tokens_for_collections(
        state,
        collections,
        ids_by_collection=ids_by_collection,
    )
    payload["resource_tokens"] = tokens
    for collection, collection_tokens in tokens.items():
        rows = payload.get(collection)
        if not isinstance(rows, dict):
            continue
        for item_id, token in collection_tokens.items():
            row = rows.get(item_id)
            if isinstance(row, dict):
                row["resource_token"] = token


def _team_resource_tokens(state: TeamState) -> dict[str, dict[str, str]]:
    return {
        collection: {
            item_id: _team_resource_token(
                _team_resource_value_for_token(state, collection, item_id, item)
            )
            for item_id, item in _team_state_collection(state, collection).items()
        }
        for collection in TEAM_RESOURCE_TOKEN_COLLECTIONS
    }


def _team_resource_tokens_for_collections(
    state: TeamState,
    collections: set[str],
    *,
    ids_by_collection: Mapping[str, set[str]] | None = None,
) -> dict[str, dict[str, str]]:
    ids_by_collection = ids_by_collection or {}
    tokens: dict[str, dict[str, str]] = {}
    for collection in TEAM_RESOURCE_TOKEN_COLLECTIONS:
        if collection not in collections:
            continue
        allowed_ids = ids_by_collection.get(collection)
        rows = _team_state_collection(state, collection)
        tokens[collection] = {
            item_id: _team_resource_token(
                _team_resource_value_for_token(state, collection, item_id, item)
            )
            for item_id, item in rows.items()
            if allowed_ids is None or item_id in allowed_ids
        }
    return tokens


def _team_resource_value_for_token(
    state: TeamState,
    collection: str,
    item_id: str,
    item: Any,
) -> Any:
    if collection != "agents":
        return item
    runtime = state.agent_runtimes.get(item_id)
    return {
        "agent": item.model_dump(mode="json") if hasattr(item, "model_dump") else item,
        "project_assignments": [
            assignment.model_dump(mode="json")
            for assignment in project_assignments_for_agent(state, item_id)
        ],
        "direct_stand_downs": [
            stand_down.model_dump(mode="json")
            for stand_down in sorted(
                (
                    stand_down
                    for stand_down in state.stand_downs.values()
                    if stand_down.agent_id == item_id
                ),
                key=lambda stand_down: stand_down.id,
            )
        ],
        "runtime": (
            {
                "context_clearing_policy": getattr(
                    runtime.context_clearing_policy,
                    "value",
                    runtime.context_clearing_policy,
                ),
                "auto_context_clearing_enabled": runtime.auto_context_clearing_enabled,
                "context_clearing_idle_grace_seconds": (
                    runtime.context_clearing_idle_grace_seconds
                ),
            }
            if runtime is not None
            else None
        ),
    }


def _team_resource_token(value: Any) -> str:
    if hasattr(value, "model_dump"):
        serializable = value.model_dump(mode="json")
    else:
        serializable = value
    encoded = json.dumps(
        serializable,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _team_resource_token_for(
    state: TeamState,
    collection: str | None,
    item_id: str | None,
) -> str | None:
    if not collection or not item_id:
        return None
    item = _team_state_collection(state, collection).get(item_id)
    if item is None:
        return None
    return _team_resource_token(_team_resource_value_for_token(state, collection, item_id, item))


def _team_state_collection(state: TeamState, collection: str) -> Mapping[str, Any]:
    if collection not in TEAM_RESOURCE_TOKEN_COLLECTIONS:
        return {}
    value = getattr(state, collection, {})
    return value if isinstance(value, Mapping) else {}


def _can_rebase_ui_mutation(
    payload: Mapping[str, Any],
    current: TeamState,
    *,
    target_collection: str | None = None,
    target_id: str | None = None,
    create_collection: str | None = None,
    create_id: str | None = None,
) -> bool:
    if str(payload.get("conflict_policy") or "").strip() != "target_unchanged":
        return False
    if target_collection and target_id:
        base_token = str(payload.get("base_resource_token") or "").strip()
        if not base_token:
            return False
        return _team_resource_token_for(current, target_collection, target_id) == base_token
    if create_collection:
        return bool(create_id) and create_id not in _team_state_collection(
            current,
            create_collection,
        )
    return False


def _chat_list_scope(
    auth: Any | None,
    request: Request,
    state: TeamState,
) -> dict[str, Any]:
    identity = _operator_identity_from_request(auth, request)
    requested_operator_user_id = _query_text(request, "operator_user_id") or _query_text(
        request,
        "view_operator_user_id",
    )
    if requested_operator_user_id is None:
        return _chat_scope_for_operator(
            auth, request, state, identity["user_id"], identity=identity
        )
    normalized_requested = _normalize_operator_user_id(requested_operator_user_id)
    if normalized_requested != identity["user_id"] and not _can_view_all_team_chats(
        auth,
        request,
    ):
        raise PermissionError(f"Permission required: {TEAM_CHATS_READ_ALL_PERMISSION}")
    return _chat_scope_for_operator(
        auth,
        request,
        state,
        normalized_requested,
        identity=identity,
    )


def _add_team_chat_mutation_modes(
    scope: dict[str, Any],
    *,
    auth: Any | None,
    service: Any | None,
    session_repository: Any | None,
    request: Request,
    state: TeamState,
) -> dict[str, Any]:
    current_operator_user_id = str(scope["current_operator"]["user_id"])
    selected_operator_user_id = str(scope["selected_operator"]["user_id"])
    modes: dict[str, str] = {}
    for conversation in state.conversations.values():
        if conversation.operator_user_id != selected_operator_user_id:
            continue
        if conversation.operator_user_id != current_operator_user_id:
            modes[conversation.id] = "reply_only"
            continue
        authorization = _team_chat_mutation_authorization(
            auth=auth,
            service=service,
            session_repository=session_repository,
            request=request,
            state=state,
            conversation=conversation,
            principal_id=current_operator_user_id,
        )
        modes[conversation.id] = authorization.mode
    return {**scope, "mutation_modes": modes}


def _chat_scope_for_operator(
    auth: Any | None,
    request: Request,
    state: TeamState,
    operator_user_id: str,
    *,
    identity: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    current = dict(identity or _operator_identity_from_request(auth, request))
    selected_user_id = _normalize_operator_user_id(operator_user_id)
    selected_display_name = _operator_display_name(
        state,
        selected_user_id,
        fallback=selected_user_id,
    )
    return {
        "current_operator": current,
        "selected_operator": {
            "user_id": selected_user_id,
            "display_name": selected_display_name,
        },
        "can_view_all_operators": _can_view_all_team_chats(auth, request),
        "operator_directory_options": _operator_directory_options(auth),
        "read_only": selected_user_id != current["user_id"],
    }


def _require_conversation_chat_scope(
    auth: Any | None,
    request: Request,
    state: TeamState,
    conversation: Any,
    *,
    write: bool,
    identity: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    scope = _chat_scope_for_operator(
        auth,
        request,
        state,
        conversation.operator_user_id,
        identity=identity,
    )
    if conversation.operator_user_id == scope["current_operator"]["user_id"]:
        return scope
    if write:
        raise PermissionError("Cannot send messages in another operator's Team chat")
    if not bool(scope["can_view_all_operators"]):
        raise PermissionError(f"Permission required: {TEAM_CHATS_READ_ALL_PERMISSION}")
    return scope


def _chat_scope_payload(
    state: TeamState,
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "current_operator": dict(scope["current_operator"]),
        "selected_operator": dict(scope["selected_operator"]),
        "can_view_all_operators": bool(scope.get("can_view_all_operators")),
        "read_only": bool(scope.get("read_only")),
        "operator_options": _operator_options(state, scope=scope),
    }


def _operator_options(
    state: TeamState,
    *,
    scope: Mapping[str, Any],
) -> list[dict[str, str]]:
    options: dict[str, dict[str, str]] = {}

    def _add(user_id: object, display_name: object | None = None) -> None:
        normalized = _normalize_operator_user_id(user_id)
        label = str(display_name or normalized).strip() or normalized
        existing = options.setdefault(
            normalized,
            {"user_id": normalized, "display_name": label},
        )
        if existing["display_name"] == normalized and label != normalized:
            existing["display_name"] = label

    current = scope["current_operator"]
    selected = scope["selected_operator"]
    _add(current["user_id"], current.get("display_name"))
    _add(selected["user_id"], selected.get("display_name"))
    if scope.get("can_view_all_operators"):
        for option in scope.get("operator_directory_options") or []:
            if isinstance(option, Mapping):
                _add(option.get("user_id"), option.get("display_name"))
        for conversation in state.conversations.values():
            _add(conversation.operator_user_id, conversation.operator_display_name)
    return sorted(options.values(), key=lambda item: item["display_name"].casefold())


def _operator_display_name(
    state: TeamState,
    operator_user_id: str,
    *,
    fallback: str,
) -> str:
    for conversation in sorted(
        state.conversations.values(),
        key=lambda item: (item.last_interaction_at, item.id),
        reverse=True,
    ):
        if conversation.operator_user_id == operator_user_id and conversation.operator_display_name:
            return conversation.operator_display_name
    return fallback


def _operator_identity_from_request(auth: Any | None, request: Request) -> dict[str, str]:
    session = _session_from_request(auth, request)
    raw_login = None
    for key in ("login", "preferred_username", "username", "sub", "name"):
        value = str((session or {}).get(key) or "").strip()
        if value:
            raw_login = value
            break
    user_id = _normalize_operator_user_id(raw_login or "admin")
    display_name = str((session or {}).get("name") or raw_login or user_id).strip() or user_id
    return {
        "user_id": user_id,
        "display_name": display_name,
        "actor": f"operator:{user_id}",
    }


def _strict_human_identity_from_request(
    auth: Any | None,
    request: Request,
) -> dict[str, str]:
    config = getattr(auth, "config", None)
    auth_mode = str(getattr(config, "auth_mode", "") or "").strip().casefold()
    if not auth_mode or auth_mode == "none":
        raise PermissionError("A real authenticated Admin Web session is required")
    read_session = getattr(auth, "read_session", None)
    if not callable(read_session):
        raise PermissionError("A real authenticated Admin Web session is required")
    session = read_session(request)
    if not isinstance(session, Mapping):
        raise PermissionError("A real authenticated Admin Web session is required")
    session_kind = str(session.get("kind") or "session").strip().casefold()
    if session_kind != "session":
        raise PermissionError("A real authenticated Admin Web session is required")
    session_id = str(session.get("sid") or "").strip()
    user_id = normalize_admin_web_login(session.get("login"))
    if user_id is None or not session_id or session_id == "local-dev":
        raise PermissionError("A real authenticated Admin Web session is required")
    display_name = str(session.get("name") or user_id).strip() or user_id
    return {
        "user_id": user_id,
        "display_name": display_name,
        "session_id": session_id,
    }


def _require_human_review_decision_identity(
    auth: Any | None,
    request: Request,
    human_request: Any,
    *,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if payload is not None:
        _reject_client_human_identity_fields(payload)
    identity = _strict_human_identity_from_request(auth, request)
    user_id = identity["user_id"]
    if not _human_review_is_assigned_to(human_request, user_id):
        raise PermissionError("Human review request is not assigned to this user")

    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    assignment = getattr(human_request, "current_assignment", None)
    explicitly_assigned = {
        str(value).strip().casefold()
        for value in (getattr(assignment, "explicitly_assigned_user_ids", None) or ())
        if str(value).strip()
    }
    if user_id not in explicitly_assigned and not _is_current_configured_human_reviewer(
        auth,
        human_request,
        user_id=user_id,
        directory_path=directory_path,
    ):
        raise PermissionError("Human reviewer is no longer a member of an assigned reviewer group")

    require_permission = getattr(auth, "require_permission", None)
    if not callable(require_permission):
        raise PermissionError(f"Permission required: {TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION}")
    require_permission(
        request,
        TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
        directory_path=directory_path,
    )
    return identity


def _require_human_review_owner_identity(
    auth: Any | None,
    request: Request,
    human_request: Any,
    *,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if payload is not None:
        _reject_client_human_identity_fields(payload)
    identity = _strict_human_identity_from_request(auth, request)
    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    require_permission = getattr(auth, "require_permission", None)
    if not callable(require_permission):
        raise PermissionError(f"Permission required: {ADMIN_OWNER_PERMISSION}")
    require_permission(
        request,
        ADMIN_OWNER_PERMISSION,
        directory_path=directory_path,
    )
    return identity


def _reject_client_human_identity_fields(payload: Mapping[str, Any]) -> None:
    forbidden_actor_fields = {
        "actor",
        "actor_id",
        "actor_user_id",
        "assigned_by_user_id",
        "display_name",
        "recorded_by_user_id",
        "reporter_display_name",
        "reporter_user_id",
        "resumed_by_user_id",
        "reviewer_display_name_snapshot",
        "reviewer_user_id",
        "user_id",
    }.intersection(payload)
    if forbidden_actor_fields:
        raise ValueError(
            "Human actor identity is derived from the authenticated session; "
            f"remove: {', '.join(sorted(forbidden_actor_fields))}"
        )


def _validate_human_review_reassignment_targets(
    auth: Any | None,
    human_request: Any,
    *,
    user_ids: Sequence[str],
) -> list[str]:
    normalized_user_ids = sorted(
        {
            normalized
            for value in user_ids
            if (normalized := normalize_admin_web_login(value)) is not None
        }
    )
    if len(normalized_user_ids) != len(set(user_ids)):
        raise ValueError("user_ids must contain valid unique Admin Web logins")
    if not normalized_user_ids:
        raise ValueError("human review reassignment requires at least one user_id")
    user_store = getattr(auth, "user_store", None)
    get_user = getattr(user_store, "get_user", None)
    can = getattr(user_store, "can", None)
    if not callable(get_user) or not callable(can):
        raise PermissionError("Admin Web user directory is unavailable")
    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    for user_id in normalized_user_ids:
        try:
            user = get_user(user_id)
            has_permission = can(
                user_id,
                TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
                directory_path=directory_path,
            )
        except Exception as exc:
            raise PermissionError("Admin Web user directory is unavailable") from exc
        if user is None:
            raise ValueError(f"Admin Web user {user_id} does not exist")
        if not has_permission:
            raise ValueError(
                f"Admin Web user {user_id} lacks "
                f"{TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION} in {directory_path}"
            )
    return normalized_user_ids


def _current_human_review_decision_eligible_user_ids(
    auth: Any | None,
    human_request: Any,
) -> list[str]:
    assignment = getattr(human_request, "current_assignment", None)
    assigned_user_ids = {
        normalized
        for value in (getattr(assignment, "eligible_user_ids", None) or ())
        if (normalized := normalize_admin_web_login(value)) is not None
    }
    explicit_user_ids = {
        normalized
        for value in (getattr(assignment, "explicitly_assigned_user_ids", None) or ())
        if (normalized := normalize_admin_web_login(value)) is not None
    }
    user_store = getattr(auth, "user_store", None)
    get_user = getattr(user_store, "get_user", None)
    can = getattr(user_store, "can", None)
    if not callable(get_user) or not callable(can):
        return []
    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    eligible: list[str] = []
    for user_id in sorted(assigned_user_ids):
        try:
            user = get_user(user_id)
            has_permission = can(
                user_id,
                TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
                directory_path=directory_path,
            )
        except Exception:
            continue
        if (
            user is not None
            and has_permission
            and (
                user_id in explicit_user_ids
                or _is_current_configured_human_reviewer(
                    auth,
                    human_request,
                    user_id=user_id,
                    directory_path=directory_path,
                )
            )
        ):
            eligible.append(user_id)
    return eligible


def _reconcile_human_review_request_assignment(
    auth: Any | None,
    repository: TeamStateRepository,
    human_review_request_id: str,
) -> dict[str, Any]:
    user_store = getattr(auth, "user_store", None)
    required_methods = (
        "list_user_group_assignments",
        "get_user",
        "can",
    )
    if user_store is None or any(
        not callable(getattr(user_store, method_name, None)) for method_name in required_methods
    ):
        raise PermissionError("Admin Web user directory is unavailable")
    return reconcile_current_human_review_assignments(
        repository=repository,
        user_store=user_store,
        request_ids=[human_review_request_id],
    )


def _can_read_human_review_request(
    auth: Any | None,
    request: Request,
    human_request: Any,
    *,
    identity: Mapping[str, str],
) -> bool:
    if _human_review_is_assigned_to(human_request, identity["user_id"]):
        return True
    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    return _auth_can(
        auth,
        request,
        ADMIN_OWNER_PERMISSION,
        directory_path=directory_path,
    )


def _can_read_release_human_review(
    auth: Any | None,
    request: Request,
    state: TeamState,
    release_package_id: str,
) -> bool:
    try:
        identity = _strict_human_identity_from_request(auth, request)
    except PermissionError:
        return False
    human_requests = [
        human_request
        for human_request in state.human_review_requests.values()
        if human_request.release_package_id == release_package_id
    ]
    if any(
        _can_read_human_review_request(
            auth,
            request,
            human_request,
            identity=identity,
        )
        for human_request in human_requests
    ):
        return True
    release = state.release_packages.get(release_package_id)
    directory_path = _release_human_review_directory(state, release)
    return _auth_can(
        auth,
        request,
        ADMIN_OWNER_PERMISSION,
        directory_path=directory_path,
    )


def _human_review_is_assigned_to(human_request: Any, user_id: str) -> bool:
    assignment = getattr(human_request, "current_assignment", None)
    assigned_ids = {
        str(value).strip().casefold()
        for field_name in (
            "eligible_user_ids",
            "explicitly_assigned_user_ids",
        )
        for value in (getattr(assignment, field_name, None) or ())
        if str(value).strip()
    }
    return user_id.casefold() in assigned_ids


def _is_current_configured_human_reviewer(
    auth: Any | None,
    human_request: Any,
    *,
    user_id: str,
    directory_path: str,
) -> bool:
    configured_group_ids = {
        str(value).strip().casefold()
        for value in (
            getattr(
                getattr(human_request, "requirement_snapshot", None),
                "configured_group_ids",
                None,
            )
            or ()
        )
        if str(value).strip()
    }
    if not configured_group_ids:
        return False
    user_store = getattr(auth, "user_store", None)
    list_assignments = getattr(user_store, "list_user_group_assignments", None)
    if not callable(list_assignments):
        return False
    try:
        assignments = list_assignments(user_id)
    except Exception:
        return False
    return any(
        str(getattr(assignment, "group_id", "") or "").strip().casefold() in configured_group_ids
        and scope_matches(
            getattr(assignment, "directory_path", None),
            directory_path,
        )
        for assignment in assignments
    )


def _release_human_review_directory(
    state: TeamState,
    release: Any | None,
) -> str:
    manifest = getattr(release, "manifest", None)
    requirement = getattr(manifest, "human_review_requirement", None)
    directory_path = str(getattr(requirement, "directory_path", "") or "").strip()
    if directory_path:
        return directory_path
    project = state.projects.get(getattr(release, "project_id", "") or "")
    return str(getattr(project, "directory_path", "") or "root").strip() or "root"


def _release_candidate_checkout_metadata(
    managed_git_admin: Any | None,
    state: TeamState,
    release: Any,
    *,
    runtime_dir: str | Path,
) -> dict[str, str] | None:
    project = state.projects.get(str(getattr(release, "project_id", "") or ""))
    git = getattr(project, "git", None)
    candidate_sha = str(getattr(release, "candidate_commit_sha", "") or "").strip().lower()
    candidate_tree = str(getattr(release, "candidate_tree_hash", "") or "").strip().lower()
    release_branch = str(getattr(release, "release_branch", "") or "").strip()
    release_id = str(getattr(release, "id", "") or "").strip()
    if (
        git is None
        or not candidate_sha
        or not candidate_tree
        or not release_branch
        or not release_id
        or managed_git_admin is None
    ):
        return None
    get_repository = getattr(managed_git_admin, "get_repository", None)
    if not callable(get_repository):
        return None
    try:
        repository = get_repository(
            git.managed_repo_id,
            directory_path=git.directory_path,
        )
    except Exception:
        return None
    if not isinstance(repository, Mapping):
        return None
    canonical_identity = str(repository.get("canonical_repository_identity") or "").strip()
    if not canonical_identity:
        return None
    release_ref = (
        release_branch if release_branch.startswith("refs/") else f"refs/heads/{release_branch}"
    )
    safe_release_id = re.sub(r"[^A-Za-z0-9._-]+", "-", release_id).strip(".-")
    suggested_target = f"./release-candidate-{safe_release_id or 'review'}"
    checkout_command = shlex.join(
        [
            "evander-cli",
            "team",
            "checkout-release-candidate",
            release_id,
            "--target",
            suggested_target,
            "--expected-commit-sha",
            candidate_sha,
            "--runtime-dir",
            str(Path(runtime_dir).expanduser().resolve(strict=False)),
        ]
    )
    return {
        "canonical_repository_identity": canonical_identity,
        "repository_display_name": str(repository.get("display_name") or "").strip(),
        "managed_repo_id": git.managed_repo_id,
        "release_ref": release_ref,
        "candidate_commit_sha": candidate_sha,
        "candidate_tree_hash": candidate_tree,
        "checkout_command": checkout_command,
    }


def _human_review_bundle_manifest(
    *,
    human_request: Any,
    release: Any,
    project: Any | None,
    source_ref: str,
) -> dict[str, Any]:
    release_manifest = getattr(release, "manifest", None)
    release_manifest_payload = (
        release_manifest.model_dump(mode="json")
        if release_manifest is not None and callable(getattr(release_manifest, "model_dump", None))
        else None
    )
    requirement = getattr(human_request, "requirement_snapshot", None)
    return {
        "schema": "evander.team.human-review-bundle/v1",
        "release_package_id": str(getattr(release, "id", "") or ""),
        "human_review_request_id": str(getattr(human_request, "id", "") or ""),
        "project": {
            "id": str(getattr(project, "id", "") or ""),
            "name": str(getattr(project, "name", "") or ""),
            "directory_path": str(getattr(project, "directory_path", "") or ""),
        },
        "candidate": {
            "source_ref": source_ref,
            "commit_sha": str(getattr(release, "candidate_commit_sha", "") or ""),
            "tree_hash": str(getattr(release, "candidate_tree_hash", "") or ""),
        },
        "test_instructions": str(getattr(requirement, "test_instructions", "") or ""),
        "release_manifest": release_manifest_payload,
    }


HUMAN_REVIEW_BUNDLE_MANIFEST_PATH = "EVANDER-REVIEW.json"


def _human_review_bundle_member_reserves_manifest(member_name: str) -> bool:
    normalized = posixpath.normpath(str(member_name or "").replace("\\", "/"))
    return normalized.casefold() == HUMAN_REVIEW_BUNDLE_MANIFEST_PATH.casefold()


def _append_human_review_bundle_manifest(
    archive_path: Path,
    manifest: Mapping[str, Any],
) -> None:
    with zipfile.ZipFile(archive_path, mode="a", compression=zipfile.ZIP_DEFLATED) as archive:
        if any(
            _human_review_bundle_member_reserves_manifest(info.filename)
            for info in archive.infolist()
        ):
            raise ValueError(
                f"Release candidate contains reserved review-bundle path "
                f"{HUMAN_REVIEW_BUNDLE_MANIFEST_PATH!r}"
            )
        archive.writestr(
            HUMAN_REVIEW_BUNDLE_MANIFEST_PATH,
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )


def _human_review_bundle_filename(release_package_id: str) -> str:
    safe_release_id = re.sub(
        r"[^A-Za-z0-9._-]+",
        "-",
        str(release_package_id or ""),
    ).strip(".-")
    return f"{safe_release_id or 'release'}-review-bundle.zip"


def _delete_temporary_file(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _auth_can(
    auth: Any | None,
    request: Request,
    permission: str,
    *,
    directory_path: str,
) -> bool:
    can = getattr(auth, "can", None)
    if not callable(can):
        return False
    try:
        return bool(can(request, permission, directory_path=directory_path))
    except Exception:
        return False


def _external_source_evidence_project_directory_path(
    state: TeamState,
    project_id: str | None,
) -> str:
    project = state.projects.get(project_id or "")
    if project is None:
        return "root"
    return normalize_directory_path(project.directory_path)


def _can_read_external_source_evidence(
    auth: Any | None,
    request: Request,
    state: TeamState,
    snapshot: Any,
) -> bool:
    return _auth_can(
        auth,
        request,
        ADMIN_OWNER_PERMISSION,
        directory_path=_external_source_evidence_project_directory_path(
            state,
            getattr(snapshot, "project_id", None),
        ),
    )


def _authorized_external_source_evidence_snapshot_ids(
    auth: Any | None,
    request: Request,
    state: TeamState,
) -> set[str]:
    return {
        snapshot.id
        for snapshot in state.external_source_evidence_snapshots.values()
        if _can_read_external_source_evidence(auth, request, state, snapshot)
    }


def _filter_external_source_evidence_payload(
    payload: dict[str, Any],
    *,
    allowed_snapshot_ids: set[str],
) -> None:
    snapshots = payload.get("external_source_evidence_snapshots")
    if isinstance(snapshots, dict):
        payload["external_source_evidence_snapshots"] = {
            snapshot_id: snapshot
            for snapshot_id, snapshot in snapshots.items()
            if snapshot_id in allowed_snapshot_ids
        }
    resource_tokens = payload.get("resource_tokens")
    if isinstance(resource_tokens, dict):
        snapshot_tokens = resource_tokens.get("external_source_evidence_snapshots")
        if isinstance(snapshot_tokens, dict):
            resource_tokens["external_source_evidence_snapshots"] = {
                snapshot_id: token
                for snapshot_id, token in snapshot_tokens.items()
                if snapshot_id in allowed_snapshot_ids
            }
    for collection in ("cases", "tasks", "dispatches"):
        rows = payload.get(collection)
        if not isinstance(rows, dict):
            continue
        for row in rows.values():
            if not isinstance(row, dict):
                continue
            snapshot_ids = row.get("source_evidence_snapshot_ids")
            if isinstance(snapshot_ids, list):
                row["source_evidence_snapshot_ids"] = [
                    snapshot_id
                    for snapshot_id in snapshot_ids
                    if snapshot_id in allowed_snapshot_ids
                ]


def _session_from_request(auth: Any | None, request: Request) -> Mapping[str, Any] | None:
    for method_name in ("read_session", "require"):
        method = getattr(auth, method_name, None)
        if not callable(method):
            continue
        raw_session = method(request)
        if isinstance(raw_session, Mapping):
            return raw_session
    return None


def _can_view_all_team_chats(auth: Any | None, request: Request) -> bool:
    can = getattr(auth, "can", None)
    if not callable(can):
        return False
    try:
        return bool(can(request, TEAM_CHATS_READ_ALL_PERMISSION))
    except Exception:
        return False


def _operator_directory_options(auth: Any | None) -> list[dict[str, str]]:
    user_store = getattr(auth, "user_store", None)
    list_users = getattr(user_store, "list_users", None)
    if not callable(list_users):
        return []
    options: list[dict[str, str]] = []
    try:
        users = list_users()
    except Exception:
        return []
    for user in users:
        user_id = _normalize_operator_user_id(getattr(user, "login", None))
        display_name = str(getattr(user, "display_name", "") or user_id).strip() or user_id
        options.append({"user_id": user_id, "display_name": display_name})
    return options


def _normalize_operator_user_id(value: object | None) -> str:
    normalized = normalize_admin_web_login(value)
    if normalized is not None:
        return normalized
    text = str(value or "").strip().casefold()
    return text or "admin"


def _conversation_collection_payload(
    state: TeamState,
    *,
    scope: Mapping[str, Any],
    selected_conversation_id: str | None = None,
    include_selected_messages: bool = False,
) -> dict[str, Any]:
    selected_operator = scope["selected_operator"]
    selected_operator_user_id = str(selected_operator["user_id"])
    current_operator_user_id = str(scope["current_operator"]["user_id"])
    conversations = sorted(
        [
            conversation
            for conversation in state.conversations.values()
            if conversation.operator_user_id == selected_operator_user_id
        ],
        key=lambda conversation: (
            conversation.last_interaction_at,
            conversation.id,
        ),
        reverse=True,
    )
    selected_id = (
        selected_conversation_id
        if selected_conversation_id
        and any(conversation.id == selected_conversation_id for conversation in conversations)
        else None
    )
    if selected_id is None and include_selected_messages and conversations:
        selected_id = conversations[0].id
    messages_by_conversation = _conversation_messages_by_conversation(state)
    mutation_modes = scope.get("mutation_modes", {})
    return {
        "revision": state.revision,
        "selected_conversation_id": selected_id,
        **_chat_scope_payload(state, scope),
        "eligible_agents": [
            _chat_agent_payload(agent)
            for agent in sorted(state.agents.values(), key=lambda item: _agent_display_name(item))
            if _chat_agent_is_eligible(agent)
        ],
        "conversations": [
            _conversation_payload(
                state,
                conversation,
                include_messages=include_selected_messages and conversation.id == selected_id,
                messages_by_conversation=messages_by_conversation,
                current_operator_user_id=current_operator_user_id,
                mutation_mode=mutation_modes.get(conversation.id),
            )
            for conversation in conversations
        ],
    }


def _conversation_payload(
    state: TeamState,
    conversation: Any,
    *,
    include_messages: bool,
    message_limit: int = DEFAULT_CHAT_MESSAGE_LIMIT,
    messages_by_conversation: Mapping[str, Sequence[Any]] | None = None,
    current_operator_user_id: str | None = None,
    mutation_mode: str | None = None,
) -> dict[str, Any]:
    messages = _conversation_messages(
        state,
        conversation.id,
        messages_by_conversation=messages_by_conversation,
    )
    newest_message = messages[-1] if messages else None
    payload = {
        "id": conversation.id,
        "agent_id": conversation.agent_id,
        "agent": _chat_agent_payload(state.agents.get(conversation.agent_id)),
        "operator_user_id": conversation.operator_user_id,
        "operator_display_name": conversation.operator_display_name,
        "created_by": conversation.created_by,
        "read_only": (
            current_operator_user_id is not None
            and conversation.operator_user_id != current_operator_user_id
        ),
        "mutation_mode": mutation_mode,
        "last_message_preview": conversation.last_message_preview,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
        "last_interaction_at": (
            conversation.last_interaction_at.isoformat()
            if conversation.last_interaction_at
            else None
        ),
        "message_count": len(messages),
        "newest_message_id": newest_message.id if newest_message is not None else None,
        "newest_message_sender": newest_message.sender if newest_message is not None else None,
    }
    if include_messages:
        page = _bounded_conversation_message_page(messages, limit=message_limit)
        payload.update(_conversation_message_page_fields(state, page, message_count=len(messages)))
    return payload


def _conversation_messages_by_conversation(state: TeamState) -> dict[str, list[Any]]:
    messages_by_conversation: dict[str, list[Any]] = {}
    for message in state.conversation_messages.values():
        messages_by_conversation.setdefault(message.conversation_id, []).append(message)
    for messages in messages_by_conversation.values():
        messages.sort(key=lambda message: (message.created_at, message.id))
    return messages_by_conversation


def _conversation_messages(
    state: TeamState,
    conversation_id: str,
    *,
    messages_by_conversation: Mapping[str, Sequence[Any]] | None = None,
) -> list[Any]:
    if messages_by_conversation is not None:
        return list(messages_by_conversation.get(conversation_id, ()))
    messages = [
        message
        for message in state.conversation_messages.values()
        if message.conversation_id == conversation_id
    ]
    messages.sort(key=lambda message: (message.created_at, message.id))
    return messages


def _bounded_conversation_message_page(
    messages: Sequence[Any],
    *,
    limit: int,
    before_message_id: str | None = None,
) -> tuple[list[Any], bool]:
    end = len(messages)
    if before_message_id is not None:
        for index, message in enumerate(messages):
            if message.id == before_message_id:
                end = index
                break
        else:
            raise ValueError("before message not found")
    start = max(0, end - limit)
    return list(messages[start:end]), start > 0


def _conversation_message_page_fields(
    state: TeamState,
    page: tuple[list[Any], bool],
    *,
    message_count: int,
) -> dict[str, Any]:
    messages, has_older = page
    return {
        "messages": [_conversation_message_payload(message, state) for message in messages],
        "messages_limit": DEFAULT_CHAT_MESSAGE_LIMIT,
        "message_count": message_count,
        "has_older": has_older,
        "oldest_message_id": messages[0].id if messages else None,
        "newest_message_id": messages[-1].id if messages else None,
    }


def _conversation_message_page_payload(
    state: TeamState,
    conversation: Any,
    *,
    current_operator_user_id: str | None = None,
    before_message_id: str | None = None,
    limit: int = DEFAULT_CHAT_MESSAGE_LIMIT,
) -> dict[str, Any]:
    messages = _conversation_messages(state, conversation.id)
    page = _bounded_conversation_message_page(
        messages,
        limit=limit,
        before_message_id=before_message_id,
    )
    payload = _conversation_message_page_fields(state, page, message_count=len(messages))
    payload["messages_limit"] = limit
    return {
        "revision": state.revision,
        "conversation_id": conversation.id,
        "read_only": (
            current_operator_user_id is not None
            and conversation.operator_user_id != current_operator_user_id
        ),
        "before_message_id": before_message_id,
        **payload,
    }


def _conversation_snapshot_payload(
    repository: TeamStateRepository,
    conversation_id: str,
    *,
    auth: Any | None = None,
    request: Request | None = None,
    context: AddonContext | None = None,
) -> dict[str, Any]:
    _state, payload = _conversation_snapshot_state_and_payload(
        repository,
        conversation_id,
        auth=auth,
        request=request,
        context=context,
    )
    return payload


def _conversation_snapshot_state_and_payload(
    repository: TeamStateRepository,
    conversation_id: str,
    *,
    auth: Any | None = None,
    request: Request | None = None,
    context: AddonContext | None = None,
) -> tuple[TeamState, dict[str, Any]]:
    state = _conversation_stream_state(_reconciled_chat_state(repository), conversation_id)
    return state, _conversation_snapshot_payload_from_state(
        state,
        conversation_id,
        auth=auth,
        request=request,
        context=context,
    )


def _conversation_stream_state(state: TeamState, conversation_id: str) -> TeamState:
    """Keep only the state needed to recheck one conversation between revisions."""

    conversation = state.conversations.get(conversation_id)
    if conversation is None:
        raise ValueError("conversation not found")
    agent = state.agents.get(conversation.agent_id)
    assignments = {
        assignment_id: assignment
        for assignment_id, assignment in state.project_assignments.items()
        if assignment.agent_id == conversation.agent_id
    }
    project_ids = {assignment.project_id for assignment in assignments.values()}
    roles = {}
    teams = {}
    if agent is not None:
        if agent.role_id is not None and agent.role_id in state.roles:
            role = state.roles[agent.role_id]
            roles[role.id] = role
            if role.project_id is not None:
                project_ids.add(role.project_id)
        if agent.team_id is not None and agent.team_id in state.teams:
            team = state.teams[agent.team_id]
            teams[team.id] = team
            project_ids.update(
                project.id
                for project in state.projects.values()
                if project.team_id == agent.team_id
                and (project.lead_agent_id == agent.id or team.manager_agent_id == agent.id)
            )
    messages = {
        message_id: message
        for message_id, message in state.conversation_messages.items()
        if message.conversation_id == conversation_id
    }
    attachment_ids = {
        attachment_id for message in messages.values() for attachment_id in message.attachment_ids
    }
    return TeamState.model_construct(
        schema_version=state.schema_version,
        revision=state.revision,
        updated_at=state.updated_at,
        teams=teams,
        projects={
            project_id: state.projects[project_id]
            for project_id in project_ids
            if project_id in state.projects
        },
        roles=roles,
        agents=({agent.id: agent} if agent is not None else {}),
        project_assignments=assignments,
        conversations=dict(state.conversations),
        conversation_messages=messages,
        chat_attachments={
            attachment_id: state.chat_attachments[attachment_id]
            for attachment_id in attachment_ids
            if attachment_id in state.chat_attachments
        },
    )


def _conversation_snapshot_payload_from_state(
    state: TeamState,
    conversation_id: str,
    *,
    auth: Any | None = None,
    request: Request | None = None,
    context: AddonContext | None = None,
) -> dict[str, Any]:
    conversation = state.conversations.get(conversation_id)
    if conversation is None:
        raise ValueError("conversation not found")
    scope = None
    current_operator_user_id = None
    if auth is not None and request is not None:
        scope = _require_conversation_chat_scope(
            auth,
            request,
            state,
            conversation,
            write=False,
        )
        current_operator_user_id = str(scope["current_operator"]["user_id"])
    return {
        "revision": state.revision,
        "conversation": _conversation_payload(
            state,
            conversation,
            include_messages=True,
            current_operator_user_id=current_operator_user_id,
            mutation_mode=(
                _team_chat_mutation_authorization_for_request(
                    context,
                    request,
                    state,
                    conversation,
                    principal_id=current_operator_user_id,
                ).mode
                if context is not None
                and request is not None
                and current_operator_user_id == conversation.operator_user_id
                else "reply_only"
            ),
        ),
        **(_chat_scope_payload(state, scope) if scope is not None else {}),
    }


async def _conversation_snapshot_events(
    repository: TeamStateRepository,
    request: Request,
    conversation_id: str,
    *,
    auth: Any | None = None,
    context: AddonContext | None = None,
    initial_snapshot: tuple[TeamState, dict[str, Any]] | None = None,
    poll_interval_seconds: float = 1.0,
) -> AsyncIterator[str]:
    if initial_snapshot is None:
        try:
            state, snapshot = await run_in_threadpool(
                _conversation_snapshot_state_and_payload,
                repository,
                conversation_id,
                auth=auth,
                request=request,
                context=context,
            )
        except (ValueError, PermissionError):
            return
    else:
        state, snapshot = initial_snapshot
    last_serialized = json.dumps(snapshot, sort_keys=True)
    yield f"event: snapshot\ndata: {last_serialized}\n\n"
    while True:
        if await request.is_disconnected():
            break
        await asyncio.sleep(max(0.0, poll_interval_seconds))
        try:
            state = await run_in_threadpool(_reconciled_chat_state, repository, state=state)
            revision = await run_in_threadpool(repository.read_revision)
            if revision != state.revision:
                state, snapshot = await run_in_threadpool(
                    _conversation_snapshot_state_and_payload,
                    repository,
                    conversation_id,
                    auth=auth,
                    request=request,
                    context=context,
                )
            else:
                snapshot = await run_in_threadpool(
                    _conversation_snapshot_payload_from_state,
                    state,
                    conversation_id,
                    auth=auth,
                    request=request,
                    context=context,
                )
        except (ValueError, PermissionError):
            break
        serialized = json.dumps(snapshot, sort_keys=True)
        if serialized == last_serialized:
            yield ": keepalive\n\n"
            continue
        last_serialized = serialized
        yield f"event: snapshot\ndata: {serialized}\n\n"


def _reconciled_chat_state(
    repository: TeamStateRepository,
    *,
    state: TeamState | None = None,
) -> TeamState:
    try:
        return reconcile_operator_chat_steering_deliveries(repository, state=state)
    except Exception:
        return repository.read()


async def _state_snapshot_events(
    snapshot_factory: Any,
    request: Request,
    *,
    poll_interval_seconds: float = 1.0,
) -> AsyncIterator[str]:
    last_serialized = json.dumps(await run_in_threadpool(snapshot_factory), sort_keys=True)
    yield f"event: snapshot\ndata: {last_serialized}\n\n"
    while True:
        if await request.is_disconnected():
            break
        await asyncio.sleep(max(0.0, poll_interval_seconds))
        serialized = json.dumps(await run_in_threadpool(snapshot_factory), sort_keys=True)
        if serialized == last_serialized:
            yield ": keepalive\n\n"
            continue
        last_serialized = serialized
        yield f"event: snapshot\ndata: {serialized}\n\n"


def _conversation_message_payload(message: Any, state: TeamState | None = None) -> dict[str, Any]:
    attachments = []
    if state is not None:
        attachments = [
            _chat_attachment_payload(state.chat_attachments[attachment_id])
            for attachment_id in message.attachment_ids
            if attachment_id in state.chat_attachments
        ]
    authorization = (message.metadata or {}).get("team_mutation_authorization")
    mutation_mode = (
        authorization.get("mode")
        if message.sender == "operator"
        and isinstance(authorization, Mapping)
        and authorization.get("mode") in {"reply_only", "project_scoped"}
        else None
    )
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender": message.sender,
        "message_type": message.message_type,
        "body": message.body,
        "status": message.status,
        "dispatch_id": message.dispatch_id,
        "voice_session_id": message.voice_session_id,
        "source_message_id": message.source_message_id,
        "source_message_ids": list(message.source_message_ids or []),
        "seen_at": message.seen_at.isoformat() if message.seen_at else None,
        "seen_dispatch_id": message.seen_dispatch_id,
        "error": message.error,
        "attachments": attachments,
        "metadata": dict(message.metadata or {}),
        "mutation_mode": mutation_mode,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "updated_at": message.updated_at.isoformat() if message.updated_at else None,
    }


def _chat_attachment_payload(attachment: Any) -> dict[str, Any]:
    return {
        "id": attachment.id,
        "message_id": attachment.message_id,
        "conversation_id": attachment.conversation_id,
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "byte_count": attachment.byte_count,
        "sha256": attachment.sha256,
        "sender": attachment.sender,
        "external_source": (
            attachment.external_source.model_dump(mode="json")
            if attachment.external_source is not None
            else None
        ),
        "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
        "download_url": (
            f"/api/addons/team-addon/conversations/{attachment.conversation_id}/"
            f"attachments/{attachment.id}"
        ),
    }


def _chat_agent_payload(agent: Any | None) -> dict[str, Any] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "display_name": _agent_display_name(agent),
        "team_id": agent.team_id,
        "project_ids": [],
        "role_id": agent.role_id,
        "role_name": None,
        "status": _enum_value(agent.status),
        "session_id": agent.session_id,
        "avatar": (
            agent.avatar.model_dump(mode="json")
            if getattr(agent, "avatar", None) is not None
            else None
        ),
    }


def _chat_agent_is_eligible(agent: Any) -> bool:
    return _enum_value(getattr(agent, "status", None)) != AgentStatus.ARCHIVED.value


def _record_chat_voice_call_status(
    chat_service: TeamChatService,
    record: Any,
) -> None:
    metadata = getattr(record, "caller_metadata", None)
    if not isinstance(metadata, Mapping) or not _optional(metadata, "conversation_id"):
        return
    try:
        chat_service.update_conversation_voice_call_status(
            voice_session_id=record.voice_session_id,
            status=str(getattr(record, "status", "") or "ended"),
            summary=str(getattr(record, "summary", "") or "").strip() or None,
            error=str(getattr(record, "error", "") or "").strip() or None,
        )
    except ValueError:
        return


def _session_is_expired(session: Any) -> bool:
    expires_at = getattr(session, "expires_at", None)
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _build_agent_voice_projection(
    *,
    state: TeamState,
    agent: Any,
    session: Any,
    tool_summary: Any | None,
) -> dict[str, Any]:
    team = state.teams.get(agent.team_id)
    assigned_project_ids = agent_project_ids(state, agent.id)
    project = state.projects.get(assigned_project_ids[0]) if assigned_project_ids else None
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    runtime = state.agent_runtimes.get(agent.id)
    subordinates = [
        state.agents[subordinate_id]
        for subordinate_id in agent.subordinate_agent_ids
        if subordinate_id in state.agents
    ]
    open_tasks = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent.id
        and _enum_value(task.state)
        not in {
            TeamTaskState.FINISHED.value,
            TeamTaskState.ACCEPTED.value,
            TeamTaskState.REJECTED.value,
            TeamTaskState.CANCELLED.value,
        }
    ]
    open_tasks.sort(
        key=lambda task: (
            int(task.priority),
            task.created_at.isoformat() if task.created_at else "",
            task.id,
        )
    )
    pending_inbox = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent.id and _enum_value(item.state) != "handled"
    ]
    pending_inbox.sort(key=lambda item: item.created_at.isoformat())
    pending_reviews = [
        review
        for review in state.review_requests.values()
        if review.reviewer_agent_id == agent.id
        and _enum_value(review.state)
        not in {TeamRecordState.ACCEPTED.value, TeamRecordState.REJECTED.value}
    ]
    pending_reviews.sort(key=lambda review: (review.created_at or datetime.min).isoformat())
    recent_deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if deliverable.producer_agent_id == agent.id
    ]
    recent_deliverables.sort(
        key=lambda deliverable: deliverable.created_at.isoformat(),
        reverse=True,
    )
    active_stand_downs = [
        stand_down
        for stand_down in state.stand_downs.values()
        if stand_down.agent_id == agent.id and not stand_down.cleared_by
    ]
    context_payload = {
        "team_state_revision": state.revision,
        "agent": {
            "id": agent.id,
            "display_name": _agent_display_name(agent),
            "status": _enum_value(agent.status),
            "session_id": agent.session_id,
            "reports_to_agent_id": agent.reports_to_agent_id,
            "subordinates": [
                {
                    "id": subordinate.id,
                    "display_name": _agent_display_name(subordinate),
                    "status": _enum_value(subordinate.status),
                }
                for subordinate in subordinates
            ],
        },
        "team": {
            "id": team.id if team else agent.team_id,
            "name": team.name if team else agent.team_id,
            "archived": bool(team.archived) if team else False,
        },
        "project": {
            "id": project.id if project else None,
            "name": project.name if project else None,
            "status": _enum_value(project.status) if project else None,
            "assignments": agent_project_assignment_payloads(state, agent.id),
            "goals": [
                {
                    "id": goal.id,
                    "description": goal.description,
                    "state": _enum_value(goal.state),
                }
                for goal in (project.goals if project else [])
            ],
        },
        "role": {
            "id": role.id if role else agent.role_id,
            "name": role.name if role else agent.role_id,
            "description": role.description if role else "",
            "permissions": [
                _enum_value(permission) for permission in effective_role_permissions(state, agent)
            ],
        },
        "runtime": {
            "wake_reason": getattr(runtime, "wake_reason", None),
            "active_dispatch_id": getattr(runtime, "active_dispatch_id", None),
            "wake_leased_until": _datetime_text(getattr(runtime, "wake_leased_until", None)),
            "last_wake_at": _datetime_text(getattr(runtime, "last_wake_at", None)),
            "next_wake_at": _datetime_text(getattr(runtime, "next_wake_at", None)),
            "no_progress_count": getattr(runtime, "no_progress_count", None),
            "last_progress_at": _datetime_text(getattr(runtime, "last_progress_at", None)),
            "context_clearing_policy": _enum_value(
                getattr(runtime, "context_clearing_policy", "off")
            ),
            "context_clearing_idle_grace_seconds": getattr(
                runtime,
                "context_clearing_idle_grace_seconds",
                0,
            ),
            "context_idle_rotation_due_at": _datetime_text(
                getattr(runtime, "context_idle_rotation_due_at", None)
            ),
            "auto_context_clearing_enabled": getattr(
                runtime,
                "auto_context_clearing_enabled",
                False,
            ),
            "context_rotation_pending": getattr(runtime, "context_rotation_pending", False),
            "last_handoff_summary": getattr(runtime, "last_handoff_summary", None),
            "context_handoff_preparation_pending": getattr(
                runtime, "context_handoff_preparation_pending", False
            ),
            "context_handoff_preparation_status": getattr(
                runtime, "context_handoff_preparation_status", None
            ),
            "last_agent_handoff_summary": getattr(runtime, "last_agent_handoff_summary", None),
            "last_agent_handoff_at": _datetime_text(
                getattr(runtime, "last_agent_handoff_at", None)
            ),
            "last_thread_id": getattr(runtime, "last_thread_id", None),
        },
        "open_tasks": [
            {
                "id": task.id,
                "priority": task.priority,
                "state": _enum_value(task.state),
                "title": task.title,
                "description": _limit_text(task.description, 700),
                "parent_task_id": task.parent_task_id,
                "dependency_task_ids": list(task.dependency_task_ids),
                "deliverable_ids": list(task.deliverable_ids),
            }
            for task in open_tasks[:10]
        ],
        "pending_inbox": [
            {
                "id": item.id,
                "item_type": _enum_value(item.item_type),
                "sender_agent_id": item.sender_agent_id,
                "task_id": item.task_id,
                "priority": _enum_value(item.priority),
                "response_requested": item.response_requested,
                "payload": sanitize_voice_metadata(item.payload),
            }
            for item in pending_inbox[:10]
        ],
        "pending_reviews": [
            {
                "id": review.id,
                "deliverable_id": review.deliverable_id,
                "state": _enum_value(review.state),
                "instructions": _limit_text(review.instructions, 500),
                "findings": list(review.findings),
            }
            for review in pending_reviews[:6]
        ],
        "recent_deliverables": [
            {
                "id": deliverable.id,
                "task_id": deliverable.task_id,
                "state": _enum_value(deliverable.state),
                "summary": _limit_text(deliverable.summary, 500),
                "artifact_refs": list(deliverable.artifact_refs),
            }
            for deliverable in recent_deliverables[:6]
        ],
        "active_stand_downs": [
            {
                "id": stand_down.id,
                "scope": stand_down.scope,
                "source": _enum_value(stand_down.source),
                "reason": stand_down.reason,
                "set_by": stand_down.set_by,
                "set_at": _datetime_text(stand_down.set_at),
            }
            for stand_down in active_stand_downs[:5]
        ],
        "origin_session": {
            "session_id": session.session_id,
            "directory_path": session.directory_path,
            "model": session.model,
            "expires_at": _datetime_text(session.expires_at),
        },
        "tool_summary": sanitize_voice_metadata(tool_summary or {}),
    }
    instructions = (
        "You are the browser voice projection of the selected persistent Team Add-on agent. "
        "The operator clicked Call in the Agent Addon People tab, so speak as a live helper "
        "for this agent and team status, not as a phone bridge.\n\n"
        "Use the projected team context below to answer questions about the agent, team, "
        "tasks, inbox, reviews, deliverables, runtime state, and visible tool capabilities. "
        "Keep answers concise and conversational. Do not claim to have changed files, tasks, "
        "or runtime state unless the origin agent confirms it. If a question needs files, "
        "tools, repository state, or live session details not present in the projection, call "
        "query_origin_agent with a concise question and continue once the answer arrives.\n\n"
        f"Selected agent: {_agent_display_name(agent)} ({agent.id})\n"
        f"Team: {context_payload['team']['name']} ({context_payload['team']['id']})\n"
        f"Primary project: {context_payload['project']['name'] or 'None'} "
        f"({context_payload['project']['id'] or 'none'})\n"
        f"Role: {context_payload['role']['name']} ({context_payload['role']['id']})\n\n"
        "Projected context JSON:\n"
        f"{json.dumps(context_payload, indent=2, sort_keys=True, default=str)}"
    )
    return {
        "prompt": instructions,
        "metadata": {
            "call_surface": "team_people_tab",
            "agent_id": agent.id,
            "team_id": agent.team_id,
            "project_ids": agent_project_ids(state, agent.id),
            "team_state_revision": state.revision,
        },
        "context": context_payload,
    }


def _agent_voice_prompt_metadata(
    *,
    state: TeamState,
    agent: Any,
    session: Any,
    prompt: str,
    effective_prompt: str | None = None,
    voice_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = str(prompt or "").strip()
    effective = str(effective_prompt or normalized).strip()
    voice_profile_payload = sanitize_voice_metadata(voice_profile or {})
    return {
        "sha256": hashlib.sha256(effective.encode("utf-8")).hexdigest(),
        "length": len(effective),
        "base_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "base_length": len(normalized),
        "team_state_revision": state.revision,
        "agent_id": agent.id,
        "team_id": agent.team_id,
        "project_ids": agent_project_ids(state, agent.id),
        "role_id": agent.role_id,
        "session_id": session.session_id,
        "call_surface": "team_people_tab",
        "voice_profile": voice_profile_payload,
    }


def _agent_voice_profile_payload(agent: Any) -> dict[str, Any]:
    voice_profile = getattr(agent, "voice_profile", None)
    if voice_profile is None:
        return {}
    model_dump = getattr(voice_profile, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json", exclude_none=True)
    elif isinstance(voice_profile, Mapping):
        payload = {key: value for key, value in voice_profile.items() if value is not None}
    else:
        payload = {}
    return sanitize_voice_metadata(payload) if isinstance(payload, dict) else {}


def _agent_voice_effective_prompt(
    prompt: str,
    *,
    voice_profile: Mapping[str, Any],
) -> str:
    normalized = str(prompt or "").strip()
    speaking_style = str(voice_profile.get("speaking_style") or "").strip()
    if not speaking_style:
        return normalized
    return (
        f"{normalized}\n\n"
        "Voice delivery style for this agent:\n"
        f"{speaking_style}\n\n"
        "Apply this as spoken delivery guidance only. Do not treat it as task, "
        "team, tool, repository, or runtime state."
    )


def _require_browser_voice_session_operator(
    auth: Any | None,
    request: Request,
    record: Any,
) -> dict[str, str]:
    """Bind browser-side call events and completion to the caller that opened it."""

    identity = _operator_identity_from_request(auth, request)
    caller_metadata = getattr(record, "caller_metadata", {})
    if not isinstance(caller_metadata, Mapping):
        return identity
    operator_user_id = _optional(caller_metadata, "operator_user_id")
    if operator_user_id is not None and operator_user_id != identity["user_id"]:
        raise PermissionError("voice session belongs to a different operator")
    return identity


def _browser_voice_transcript_speaker_label(record: Any, speaker_role: str | None) -> str | None:
    """Replace browser-supplied roles with durable, human-readable participants."""

    normalized_role = str(speaker_role or "").strip().casefold()
    caller_metadata = getattr(record, "caller_metadata", {})
    if not isinstance(caller_metadata, Mapping):
        caller_metadata = {}
    if normalized_role in {"operator", "caller", "human", "user"}:
        return _voice_participant_label(
            display_name=_optional(caller_metadata, "operator_display_name"),
            user_id=_optional(caller_metadata, "operator_user_id"),
            fallback="operator",
        )
    if normalized_role in {"agent", "assistant", "model"}:
        return _voice_participant_label(
            display_name=_voice_participant_text(getattr(record, "contact_display_name", None)),
            user_id=_voice_participant_text(getattr(record, "contact_id", None)),
            fallback="agent",
        )
    return _voice_participant_text(speaker_role)


def _browser_voice_reconciliation_transcript(record: Any, transcript: str | None) -> str | None:
    """Canonicalize the role prefixes in the browser's final transcript excerpt."""

    normalized = _voice_participant_text(transcript)
    if normalized is None:
        return None
    lines: list[str] = []
    for line in normalized.splitlines():
        role, separator, body = line.partition(":")
        normalized_role = role.strip().casefold()
        if separator and normalized_role in {
            "operator",
            "caller",
            "human",
            "user",
            "agent",
            "assistant",
            "model",
        }:
            label = _browser_voice_transcript_speaker_label(record, role) or role
            lines.append(f"{label}: {body.lstrip()}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _voice_participant_label(
    *,
    display_name: str | None,
    user_id: str | None,
    fallback: str,
) -> str:
    display = _voice_participant_text(display_name)
    user = _voice_participant_text(user_id)
    if display is not None and user is not None and display != user:
        return f"{display} ({user})"
    return display or user or fallback


def _voice_participant_text(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _browser_voice_reconciliation_context(
    *,
    state: TeamState,
    runtime: Any,
    record: Any,
) -> dict[str, Any]:
    """Snapshot the same compact Team working set used by ordinary fresh wakes."""

    agent_id = _voice_participant_text(getattr(record, "contact_id", None))
    if agent_id is None:
        return {}
    return build_voice_reconciliation_context(
        state,
        agent_id=agent_id,
        session_records=_session_records_by_id(getattr(runtime, "session_repository", None)),
    )


def _team_voice_session_payload(record: Any) -> dict[str, Any]:
    return {
        "voice_session_id": record.voice_session_id,
        "request_id": record.request_id,
        "origin_session_id": record.origin_session_id,
        "directory_path": record.directory_path,
        "channel": record.channel,
        "status": record.status,
        "contact_id": record.contact_id,
        "contact_display_name": record.contact_display_name,
        "transcript": record.transcript,
        "transcript_events": [event.model_dump(mode="json") for event in record.transcript_events],
        "reconciliation_status": record.reconciliation_status,
        "reconciliation_request_id": record.reconciliation_request_id,
        "error": record.error,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
    }


def _realtime_calls_url(config: Any) -> str:
    base_url = (
        str(
            getattr(config, "openai_realtime_api_base_url", "https://api.openai.com/v1")
            or "https://api.openai.com/v1"
        )
        .strip()
        .rstrip("/")
    )
    return f"{base_url}/realtime/calls"


def _agent_display_name(agent: Any) -> str:
    return str(getattr(agent, "display_name", None) or getattr(agent, "id", "")).strip()


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _datetime_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _limit_text(value: Any, max_length: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def _publish_team_events(context: AddonContext, events: object) -> list[str]:
    if not isinstance(events, list):
        return []
    bus = build_internal_event_bus_for_runtime(
        context.runtime_dir,
        project_dir=context.project_dir,
        base_config=context.config,
    )
    scheduled_store = build_scheduled_internal_event_store_for_runtime(
        context.runtime_dir,
        project_dir=context.project_dir,
        base_config=context.config,
    )
    published_ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        run_at = _optional_event_datetime(event.get("run_at"))
        delay_seconds = _optional_event_float(event.get("delay_seconds"))
        if run_at is not None or (delay_seconds is not None and delay_seconds > 0):
            scheduled, _created = scheduled_store.schedule_event(
                event_type=_required_event_text(event.get("event_type"), "event_type"),
                payload=dict(payload),
                correlation_key=_optional_event_text(event.get("correlation_key")),
                run_at=(
                    run_at
                    if run_at is not None
                    else datetime.now(UTC) + timedelta(seconds=delay_seconds or 0.0)
                ),
                idempotency_key=_optional_event_text(event.get("idempotency_key")),
                source=InternalEventSource(
                    source_type="team-addon",
                    source_name="team-addon",
                ),
            )
            published_ids.append(scheduled.schedule_id)
            continue
        published = bus.publish(
            event_type=_required_event_text(event.get("event_type"), "event_type"),
            payload=dict(payload),
            correlation_key=_optional_event_text(event.get("correlation_key")),
            idempotency_key=_optional_event_text(event.get("idempotency_key")),
            source=InternalEventSource(source_type="team-addon", source_name="team-addon"),
        )
        published_ids.append(published.event_id)
    return published_ids


def _idle_context_rotation_outbox_publication_extra(
    context: AddonContext,
    *,
    state: TeamState,
    agent_id: str,
) -> dict[str, object] | None:
    runtime = state.agent_runtimes.get(agent_id)
    if runtime is None or runtime.context_idle_rotation_due_at is None:
        return None
    due_at = runtime.context_idle_rotation_due_at
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    due_key = int(due_at.astimezone(UTC).timestamp() * 1_000_000)
    return {
        "outbox_publication_wake": _request_team_outbox_publication(
            context,
            correlation_key=f"idle-context-rotation:{agent_id}:{due_key}",
        )
    }


def _required_event_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_event_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_event_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("number value is required")
    return float(value)


def _optional_event_datetime(value: object) -> datetime | None:
    text = _optional_event_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("run_at must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _required_iso_datetime(
    payload: Mapping[str, Any],
    field_name: str,
) -> datetime:
    value = _optional_iso_datetime(payload, field_name)
    if value is None:
        raise ValueError(f"{field_name} is required")
    return value


def _optional_iso_datetime(
    payload: Mapping[str, Any],
    field_name: str,
) -> datetime | None:
    raw_value = payload.get(field_name)
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _read_payload(request: Request) -> Mapping[str, Any]:
    payload = await request.json()
    if not isinstance(payload, Mapping):
        raise ValueError("request body must be a JSON object")
    return payload


async def _read_optional_payload(request: Request) -> Mapping[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("request body must be a JSON object") from exc
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ValueError("request body must be a JSON object")
    return payload


def _required(payload: Mapping[str, Any], *field_names: str) -> str:
    for field_name in field_names:
        value = payload.get(field_name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    joined = " or ".join(field_names)
    raise ValueError(f"{joined} is required")


def _mutation_request_id(
    request: Request,
    payload: Mapping[str, Any],
) -> str:
    header_value = str(request.headers.get("Idempotency-Key") or "").strip()
    body_values = {
        str(payload[field_name]).strip()
        for field_name in ("request_id", "idempotency_key")
        if payload.get(field_name) is not None and str(payload[field_name]).strip()
    }
    values = body_values | ({header_value} if header_value else set())
    if not values:
        raise ValueError("request_id or Idempotency-Key is required")
    if len(values) != 1:
        raise ValueError("request_id and Idempotency-Key must match")
    return next(iter(values))


def _optional_bool(payload: Mapping[str, Any], field_name: str) -> bool | None:
    if field_name not in payload:
        return None
    value = payload.get(field_name)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _optional(payload: Mapping[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _operator_actor_from_request(auth: Any | None, request: Request) -> str:
    return _operator_identity_from_request(auth, request)["actor"]


def _optional_present_text(payload: Mapping[str, Any], field_name: str) -> str | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    return str(payload[field_name]).strip()


def _query_text(request: Request, field_name: str) -> str | None:
    query_params = getattr(request, "query_params", None)
    if query_params is None:
        return None
    value = query_params.get(field_name)
    if value is None:
        return None
    text = value.strip()
    return text or None


def _query_values(request: Request, field_name: str) -> list[str] | None:
    values = request.query_params.getlist(field_name)
    normalized: list[str] = []
    for value in values:
        for item in value.split(","):
            text = item.strip()
            if text:
                normalized.append(text)
    if not normalized:
        return None
    return list(dict.fromkeys(normalized))


def _query_bool(request: Request, field_name: str) -> bool | None:
    value = request.query_params.get(field_name)
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be true or false")


def _query_limited_int(
    request: Request,
    field_name: str,
    *,
    default: int,
    max_value: int,
) -> int:
    value = _query_text(request, field_name)
    if value is None:
        return default
    result = _coerce_int(value, field_name)
    if result < 1:
        raise ValueError(f"{field_name} must be greater than zero")
    return min(result, max_value)


def _query_non_negative_int(request: Request, field_name: str, *, default: int) -> int:
    value = _query_text(request, field_name)
    if value is None:
        return default
    result = _coerce_int(value, field_name)
    if result < 0:
        raise ValueError(f"{field_name} must be greater than or equal to zero")
    return result


def _query_state_history_limit(request: Request) -> int:
    return _query_limited_int(
        request,
        "history_limit",
        default=DEFAULT_STATE_HISTORY_LIMIT,
        max_value=MAX_STATE_HISTORY_LIMIT,
    )


def _query_chat_message_limit(request: Request) -> int:
    value = _query_text(request, "limit")
    if value is None:
        return DEFAULT_CHAT_MESSAGE_LIMIT
    limit = _coerce_int(value, "limit")
    if limit < 1:
        raise ValueError("limit must be greater than zero")
    return min(limit, MAX_CHAT_MESSAGE_LIMIT)


def _optional_git_config(payload: Mapping[str, Any]) -> ProjectGitConfig | None:
    git_payload = payload.get("git")
    if git_payload is None:
        return None
    if not isinstance(git_payload, Mapping):
        raise ValueError("git must be a JSON object")
    return ProjectGitConfig.model_validate(dict(git_payload))


def _optional_release_policy(
    payload: Mapping[str, Any],
) -> ProjectReleasePolicy | None:
    raw = payload.get("release_policy")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("release_policy must be a JSON object")
    return ProjectReleasePolicy.model_validate(dict(raw))


def _resolved_release_policy_for_admin(
    auth: Any | None,
    payload: Mapping[str, Any],
    *,
    directory_path: str,
) -> ProjectReleasePolicy | None:
    policy = _optional_release_policy(payload)
    if policy is None or policy.human_review_requirement is None:
        return policy
    requirement = policy.human_review_requirement
    normalized_directory = normalize_directory_path(directory_path)
    group_ids: list[str] = []
    for raw_group_id in requirement.reviewer_group_ids:
        group_id = normalize_admin_web_group_id(raw_group_id)
        if group_id is None:
            raise ValueError(f"invalid human reviewer group {raw_group_id!r}")
        if group_id not in group_ids:
            group_ids.append(group_id)

    user_store = getattr(auth, "user_store", None)
    get_group = getattr(user_store, "get_group", None)
    list_assignments = getattr(user_store, "list_user_group_assignments", None)
    get_user = getattr(user_store, "get_user", None)
    can = getattr(user_store, "can", None)
    if not all(callable(method) for method in (get_group, list_assignments, get_user, can)):
        raise ValueError(
            "Admin Web user directory is unavailable; human review policy cannot be configured"
        )
    missing_group_ids = [group_id for group_id in group_ids if get_group(group_id) is None]
    if missing_group_ids:
        raise ValueError(
            "human review policy references missing Admin groups: " + ", ".join(missing_group_ids)
        )
    try:
        assignments = list_assignments()
    except Exception as exc:
        raise ValueError("Admin Web user directory is unavailable") from exc
    eligible_user_ids: list[str] = []
    for assignment in assignments:
        assignment_group_id = normalize_admin_web_group_id(getattr(assignment, "group_id", None))
        if assignment_group_id not in group_ids or not scope_matches(
            getattr(assignment, "directory_path", None),
            normalized_directory,
        ):
            continue
        user_id = normalize_admin_web_login(getattr(assignment, "login", None))
        if user_id is None or user_id in eligible_user_ids:
            continue
        try:
            user = get_user(user_id)
            has_permission = can(
                user_id,
                TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
                directory_path=normalized_directory,
            )
        except Exception as exc:
            raise ValueError("Admin Web user directory is unavailable") from exc
        if user is not None and has_permission:
            eligible_user_ids.append(user_id)
    if not eligible_user_ids:
        raise ValueError(
            "human review policy has no active reviewer with "
            f"{TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION} in {normalized_directory}"
        )
    requirement.reviewer_group_ids = group_ids
    requirement.directory_path = normalized_directory
    requirement.resolved_reviewer_user_ids = sorted(eligible_user_ids)
    return policy


def _current_agent_avatar_asset_id(state: Any, agent_id: str) -> str | None:
    agent = state.agents.get(agent_id)
    if agent is None:
        return None
    avatar = getattr(agent, "avatar", None)
    return getattr(avatar, "asset_id", None)


def _expected_revision(payload: Mapping[str, Any]) -> int:
    if "expected_revision" not in payload or payload.get("expected_revision") is None:
        raise ValueError("expected_revision is required")
    return _coerce_int(payload["expected_revision"], "expected_revision")


def _required_int(payload: Mapping[str, Any], field_name: str) -> int:
    if field_name not in payload:
        raise ValueError(f"{field_name} is required")
    return _coerce_int(payload[field_name], field_name)


def _optional_int(payload: Mapping[str, Any], field_name: str) -> int | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    return _coerce_int(payload[field_name], field_name)


def _coerce_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _optional_list(payload: Mapping[str, Any], field_name: str) -> list[Any]:
    value = payload.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return value


def _optional_object(payload: Mapping[str, Any], field_name: str) -> dict[str, Any] | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    value = payload[field_name]
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _optional_string_list(payload: Mapping[str, Any], field_name: str) -> list[str] | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    value = payload[field_name]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    normalized = [str(item).strip() for item in value if str(item).strip()]
    return normalized


def _optional_mapping_list(
    payload: Mapping[str, Any],
    field_name: str,
) -> list[dict[str, Any]]:
    values = _optional_list(payload, field_name)
    normalized: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name}[{index}] must be a JSON object")
        normalized.append(dict(value))
    return normalized


def _memory_artifact_refs(payload: Mapping[str, Any]) -> list[str]:
    refs = _optional_string_list(payload, "artifact_refs") or []
    artifact_space_ref = _optional(payload, "artifact_space_ref")
    if artifact_space_ref is not None and artifact_space_ref not in refs:
        refs.append(artifact_space_ref)
    return refs
