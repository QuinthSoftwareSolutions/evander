from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from agent_sandbox.addons import AddonContext
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.internal import (
    InternalEventBus,
    InternalEventSource,
    internal_event_store_path,
)
from agent_sandbox.email.http_utils import ProviderRequestError
from agent_sandbox.voice_sessions import (
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
from .models import (
    AgentStatus,
    AgentVoiceProfile,
    EphemeralAgentPolicy,
    GovernanceAgendaItemType,
    GovernanceGoal,
    ProjectGitConfig,
    ProjectGoal,
    ProjectStatus,
    ReviewProfileCriterion,
    TeamRecordState,
    TeamInboxPriority,
    TeamTaskState,
    TeamState,
    team_state_payload,
)
from .costs import TeamCostService
from .templates import get_template, template_catalog
from .timeline import derive_team_timeline
from .repository import TeamStateConflictError, TeamStateRepository
from .providers import (
    DEFAULT_ACTION_ARTIFACT_PATH,
    _seed_action_contract_files,
    _seed_team_agent_guidance,
)

DEFAULT_CHAT_MESSAGE_LIMIT = 50
MAX_CHAT_MESSAGE_LIMIT = 100


def admin_api_routes(context: AddonContext) -> Sequence[Route]:
    repository = TeamStateRepository(context.runtime_dir)
    avatar_storage = AvatarStorage(context.runtime_dir)

    async def get_state(_request: Request) -> JSONResponse:
        return JSONResponse(team_state_payload(repository.read()))

    async def list_templates(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "templates": [
                    template.model_dump(mode="json") for template in template_catalog()
                ]
            }
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
        state, result = repository.materialize_template(
            template_id,
            inputs=payload,
            expected_revision=_expected_revision(payload),
        )
        response_payload = team_state_payload(state)
        response_payload["materialized"] = result
        return JSONResponse(response_payload, status_code=201)

    async def get_timeline(request: Request) -> JSONResponse:
        state = repository.read()
        team_id = _query_text(request, "team_id")
        project_id = _query_text(request, "project_id")
        agent_id = _query_text(request, "agent_id")
        types = _query_values(request, "type")
        statuses = _query_values(request, "status")
        attention_needed = _query_bool(request, "attention_needed")
        timeline = derive_team_timeline(
            state,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
            types=types,
            statuses=statuses,
            attention_needed=attention_needed,
        )
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
        payload = {
            **timeline,
            "source": "derived_from_current_team_state",
            "description": "Derived timeline from the current TeamState snapshot.",
            "filters": filters,
        }
        return JSONResponse(payload)

    async def get_costs(request: Request) -> JSONResponse:
        state = repository.read()
        backfill = _query_bool(request, "backfill")
        service = TeamCostService(
            runtime_dir=context.runtime_dir,
            state=state,
            session_repository=_session_repository_from_context(context),
        )
        return JSONResponse(service.costs_payload(backfill_transcripts=backfill is not False))

    async def create_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.create_team(
            team_id=_optional(payload, "id") or _optional(payload, "team_id"),
            name=_required(payload, "name"),
            manager_agent_id=_optional(payload, "manager_agent_id"),
            manager_session_id=_required(payload, "manager_session_id"),
            manager_display_name=_optional(payload, "manager_display_name"),
            manager_role_id=_optional(payload, "manager_role_id"),
            manager_role_name=_optional(payload, "manager_role_name"),
            operator_communication_target_id=_optional(
                payload, "operator_communication_target_id"
            ),
            max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_archived = payload.get("archived")
        state = repository.update_team(
            team_id=request.path_params["team_id"],
            name=_optional(payload, "name"),
            operator_communication_target_id=_optional(
                payload, "operator_communication_target_id"
            ),
            set_operator_communication_target_id="operator_communication_target_id" in payload,
            max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
            set_max_cost_usd="max_cost_usd" in payload,
            archived=raw_archived if isinstance(raw_archived, bool) else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_governance_group(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.create_governance_group(
            governance_group_id=_optional(payload, "id")
            or _optional(payload, "governance_group_id"),
            name=_required(payload, "name"),
            manager_agent_id=_required(payload, "manager_agent_id"),
            member_team_ids=list(_optional_list(payload, "member_team_ids")),
            goals=list(_optional_list(payload, "goals")),
            max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_governance_group(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_archived = payload.get("archived")
        goals = (
            [GovernanceGoal.model_validate(item) for item in _optional_list(payload, "goals")]
            if "goals" in payload
            else None
        )
        state = repository.update_governance_group(
            governance_group_id=request.path_params["governance_group_id"],
            name=_optional(payload, "name"),
            manager_agent_id=_optional(payload, "manager_agent_id"),
            member_team_ids=list(_optional_list(payload, "member_team_ids")),
            set_member_team_ids="member_team_ids" in payload,
            goals=goals,
            max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
            set_max_cost_usd="max_cost_usd" in payload,
            archived=raw_archived if isinstance(raw_archived, bool) else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_governance_agenda_item(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_priority = _optional(payload, "priority") or TeamInboxPriority.NORMAL.value
        state = repository.create_governance_agenda_item(
            agenda_item_id=_optional(payload, "id") or _optional(payload, "agenda_item_id"),
            governance_group_id=_required(payload, "governance_group_id"),
            item_type=GovernanceAgendaItemType(_required(payload, "item_type")),
            title=_required(payload, "title"),
            summary=_required(payload, "summary"),
            team_id=_optional(payload, "team_id"),
            project_id=_optional(payload, "project_id"),
            agent_id=_optional(payload, "agent_id"),
            payload=payload.get("payload") if isinstance(payload.get("payload"), Mapping) else None,
            priority=TeamInboxPriority(raw_priority),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def handle_governance_agenda_item(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.mark_governance_agenda_handled(
            agenda_item_id=request.path_params["agenda_item_id"],
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        goals = list(_optional_list(payload, "goals"))
        state = repository.create_project(
            project_id=_optional(payload, "id") or _optional(payload, "project_id"),
            team_id=_required(payload, "team_id"),
            name=_required(payload, "name"),
            lead_agent_id=_optional(payload, "lead_agent_id"),
            lead_session_id=_required(payload, "lead_session_id"),
            lead_display_name=_optional(payload, "lead_display_name"),
            lead_role_id=_optional(payload, "lead_role_id"),
            lead_role_name=_optional(payload, "lead_role_name"),
            goals=goals,
            git=_optional_git_config(payload),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        has_git = "git" in payload
        has_goals = "goals" in payload
        goals = (
            [ProjectGoal.model_validate(item) for item in _optional_list(payload, "goals")]
            if has_goals
            else None
        )
        raw_status = _optional(payload, "status")
        state = repository.update_project(
            project_id=request.path_params["project_id"],
            name=_optional(payload, "name"),
            git=_optional_git_config(payload),
            set_git=has_git,
            goals=goals,
            status=ProjectStatus(raw_status) if raw_status else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_role(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        state = repository.create_role(
            role_id=_optional(payload, "id") or _optional(payload, "role_id"),
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
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_role(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        raw_archived = payload.get("archived")
        state = repository.update_role(
            role_id=request.path_params["role_id"],
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
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

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
        state = repository.register_agent(
            agent_id=_optional(payload, "id") or _optional(payload, "agent_id"),
            team_id=_required(payload, "team_id"),
            project_id=_required(payload, "project_id"),
            role_id=_required(payload, "role_id"),
            session_id=_required(payload, "session_id"),
            reports_to_agent_id=_required(payload, "reports_to_agent_id"),
            display_name=_optional(payload, "display_name"),
            ephemeral_policy=(
                EphemeralAgentPolicy.model_validate(ephemeral_policy)
                if ephemeral_policy is not None
                else None
            ),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        raw_voice_profile = payload.get("voice_profile")
        raw_status = _optional(payload, "status")
        state = repository.update_agent(
            agent_id=request.path_params["agent_id"],
            project_id=_optional(payload, "project_id"),
            set_project_id="project_id" in payload,
            role_id=_optional(payload, "role_id"),
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
            auto_context_clearing_enabled=_optional_bool(
                payload,
                "auto_context_clearing_enabled",
            ),
            set_auto_context_clearing_enabled=(
                "auto_context_clearing_enabled" in payload
            ),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_avatar_preview(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.read()
        agent_id = request.path_params["agent_id"]
        agent = state.agents.get(agent_id)
        if agent is None:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        role = state.roles.get(agent.role_id)
        team = state.teams.get(agent.team_id)
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
            state = repository.set_agent_avatar(
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
        return JSONResponse(team_state_payload(state))

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
        state = repository.set_agent_avatar(
            agent_id=agent_id,
            avatar=avatar,
            expected_revision=expected_revision,
        )
        avatar_storage.promote_preview(preview_id=avatar.preview_id or avatar.asset_id)
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return JSONResponse(team_state_payload(state))

    async def delete_agent_avatar(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        old_asset_id = _current_agent_avatar_asset_id(
            repository.read(),
            request.path_params["agent_id"],
        )
        state = repository.clear_agent_avatar(
            agent_id=request.path_params["agent_id"],
            expected_revision=_expected_revision(payload),
        )
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return JSONResponse(team_state_payload(state))

    async def list_conversations(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(_conversation_collection_payload(state))

    async def create_conversation(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state, conversation, created = repository.create_or_get_conversation(
            agent_id=_required(payload, "agent_id"),
            created_by=_optional(payload, "created_by")
            or _operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        response_payload = _conversation_collection_payload(
            state,
            selected_conversation_id=conversation.id,
        )
        response_payload["created"] = created
        return JSONResponse(response_payload, status_code=201 if created else 200)

    async def get_conversation(request: Request) -> JSONResponse:
        state = repository.read()
        conversation_id = request.path_params["conversation_id"]
        conversation = state.conversations.get(conversation_id)
        if conversation is None:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        return JSONResponse(
            {
                "revision": state.revision,
                "conversation": _conversation_payload(
                    state,
                    conversation,
                    include_messages=True,
                ),
            }
        )

    async def list_conversation_messages(request: Request) -> JSONResponse:
        state = repository.read()
        conversation_id = request.path_params["conversation_id"]
        conversation = state.conversations.get(conversation_id)
        if conversation is None:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        return JSONResponse(
            _conversation_message_page_payload(
                state,
                conversation,
                before_message_id=_query_text(request, "before"),
                limit=_query_chat_message_limit(request),
            )
        )

    async def stream_conversation(request: Request) -> StreamingResponse | JSONResponse:
        conversation_id = request.path_params["conversation_id"]
        try:
            _conversation_snapshot_payload(repository, conversation_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return StreamingResponse(
            _conversation_snapshot_events(repository, request, conversation_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def send_conversation_message(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        conversation_id = request.path_params["conversation_id"]
        state, message = repository.record_operator_chat_message(
            conversation_id=conversation_id,
            body=_required(payload, "body"),
            created_by=_optional(payload, "created_by")
            or _operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        conversation = state.conversations[conversation_id]
        agent = state.agents[conversation.agent_id]
        dispatch_id = f"team-chat:{message.id}"
        dispatch_payload: dict[str, Any] = {
            "success": False,
            "dispatch_id": dispatch_id,
            "message": None,
        }
        try:
            state, message = repository.begin_chat_message_dispatch(
                message_id=message.id,
                dispatch_id=dispatch_id,
            )
            conversation = state.conversations[conversation_id]
            agent_setup = _seed_team_chat_agent_files(context, agent)
            prompt = _build_team_chat_prompt(
                state=state,
                conversation=conversation,
                message=message,
            )
            dispatch_result = _resume_team_chat_agent(
                context,
                agent=agent,
                prompt=prompt,
                dispatch_id=dispatch_id,
            )
            dispatch_payload = _dispatch_result_payload(dispatch_result)
            dispatch_payload["dispatch_id"] = dispatch_payload.get("dispatch_id") or dispatch_id
            dispatch_payload["agent_setup"] = agent_setup
            if not bool(dispatch_payload.get("success")):
                state, message = repository.mark_chat_message_dispatch_failed(
                    message_id=message.id,
                    error=str(dispatch_payload.get("message") or "agent dispatch failed"),
                )
        except Exception as exc:
            state, message = repository.mark_chat_message_dispatch_failed(
                message_id=message.id,
                error=str(exc),
            )
            dispatch_payload = {
                "success": False,
                "dispatch_id": dispatch_id,
                "message": str(exc),
            }
        conversation = state.conversations[conversation_id]
        return JSONResponse(
            {
                "revision": state.revision,
                "conversation": _conversation_payload(
                    state,
                    conversation,
                    include_messages=True,
                ),
                "message": _conversation_message_payload(message),
                "dispatch": dispatch_payload,
            },
            status_code=202,
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
        conversation_id = _optional(payload, "conversation_id")
        conversation = None
        if conversation_id is not None:
            conversation = state.conversations.get(conversation_id)
            if conversation is None:
                return JSONResponse({"error": "conversation not found"}, status_code=404)
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
                    {
                        "error": (
                            "Voice session request_id was already used with different inputs"
                        )
                    },
                    status_code=400,
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
                    include_query_origin_agent=payload.get("include_query_origin_agent") is not False,
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
                state, conversation_message = repository.record_conversation_voice_call(
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
                        )
                        if conversation is not None
                        else None
                    ),
                    "conversation_message": (
                        _conversation_message_payload(conversation_message)
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
                "project_id": agent.project_id,
                "conversation_id": conversation_id,
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
            state, conversation_message = repository.record_conversation_voice_call(
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
                    )
                    if conversation is not None
                    else None
                ),
                "conversation_message": (
                    _conversation_message_payload(conversation_message)
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
        payload = await _read_payload(request)
        updated = record
        event_type = _optional(payload, "event_type")
        event_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
        transcript_text = _optional(payload, "text")
        if transcript_text:
            updated = runtime.voice_session_store.append_transcript_event(
                voice_session_id,
                text=transcript_text,
                speaker=_optional(payload, "speaker"),
                metadata={
                    "event_type": event_type or "browser_call.transcript",
                    **dict(event_metadata),
                },
            ) or updated
        raw_status = _optional(payload, "status")
        if raw_status:
            status = normalize_voice_session_status(raw_status, default=updated.status)
            if status in {"ended", "failed"}:
                updated = runtime.voice_session_store.complete_session(
                    voice_session_id,
                    status=status,
                    transcript=_optional(payload, "transcript"),
                    summary=_optional(payload, "summary"),
                    decisions=_optional_string_list(payload, "decisions")
                    if "decisions" in payload
                    else None,
                    action_items=_optional_string_list(payload, "action_items")
                    if "action_items" in payload
                    else None,
                    error=_optional(payload, "error"),
                )
                _record_chat_voice_call_status(repository, updated)
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
        payload = await _read_optional_payload(request)
        status = normalize_voice_session_status(payload.get("status"), default="ended")
        if status not in {"ended", "failed"}:
            raise ValueError("status must be ended or failed")
        updated = runtime.voice_session_store.complete_session(
            voice_session_id,
            status=status,
            transcript=_optional(payload, "transcript"),
            summary=_optional(payload, "summary"),
            decisions=_optional_string_list(payload, "decisions") if "decisions" in payload else None,
            action_items=_optional_string_list(payload, "action_items")
            if "action_items" in payload
            else None,
            error=_optional(payload, "error"),
        )
        _record_chat_voice_call_status(repository, updated)
        reconciled = runtime.voice_session_reconciler.schedule_reconciliation(voice_session_id)
        return JSONResponse(_team_voice_session_payload(reconciled or updated))

    async def end_agent_voice_session_by_request(request: Request) -> JSONResponse:
        runtime = _runtime_from_context(context)
        request_id = request.path_params["request_id"]
        record = runtime.voice_session_store.get_by_request_id(request_id)
        if record is None or record.channel != "browser_agent_call":
            return JSONResponse({"error": "voice session not found"}, status_code=404)
        payload = await _read_optional_payload(request)
        status = normalize_voice_session_status(payload.get("status"), default="ended")
        if status not in {"ended", "failed"}:
            raise ValueError("status must be ended or failed")
        updated = runtime.voice_session_store.complete_session(
            record.voice_session_id,
            status=status,
            transcript=_optional(payload, "transcript"),
            summary=_optional(payload, "summary"),
            decisions=_optional_string_list(payload, "decisions") if "decisions" in payload else None,
            action_items=_optional_string_list(payload, "action_items")
            if "action_items" in payload
            else None,
            error=_optional(payload, "error"),
        )
        _record_chat_voice_call_status(repository, updated)
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
        payload = await _read_payload(request)
        query = VoiceQueryDelegator(
            store=runtime.voice_session_store,
            wake_store=runtime.wake_store,
        ).create_query_and_wake_origin(
            voice_session_id,
            question=_required(payload, "question"),
            transcript_excerpt=_optional(payload, "transcript_excerpt"),
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
        state = repository.request_agent_context_rotation(
            agent_id=request.path_params["agent_id"],
            requested_by=_optional(payload, "requested_by")
            or _operator_actor_from_request(context.auth, request),
            request_note=_optional(payload, "reason"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def stand_down_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.stand_down_agent(
            agent_id=request.path_params["agent_id"],
            reason=_required(payload, "reason"),
            set_by=_optional(payload, "set_by")
            or _operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def revoke_agent_stand_down(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.revoke_operator_stand_downs(
            agent_id=request.path_params["agent_id"],
            revoked_by=_optional(payload, "revoked_by")
            or _operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        created_by_agent_id = _optional(payload, "created_by_agent_id")
        state = repository.create_or_reprioritize_task(
            task_id=_optional(payload, "id") or _optional(payload, "task_id"),
            owner_agent_id=_required(payload, "owner_agent_id"),
            project_id=_required(payload, "project_id"),
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
            set_parent_task_id="parent_task_id" in payload,
            dependency_task_ids=_optional_string_list(payload, "dependency_task_ids"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        state = repository.update_task(
            task_id=request.path_params["task_id"],
            priority=_optional_int(payload, "priority"),
            state=TeamTaskState(raw_state) if raw_state else None,
            title=_optional(payload, "title"),
            description=_optional(payload, "description"),
            parent_task_id=_optional(payload, "parent_task_id"),
            set_parent_task_id="parent_task_id" in payload,
            dependency_task_ids=_optional_string_list(payload, "dependency_task_ids"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def record_deliverable(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        state = repository.record_deliverable(
            deliverable_id=_optional(payload, "id") or _optional(payload, "deliverable_id"),
            task_id=_required(payload, "task_id"),
            producer_agent_id=_required(payload, "producer_agent_id"),
            summary=_required(payload, "summary"),
            artifact_refs=_optional_string_list(payload, "artifact_refs"),
            state=TeamRecordState(raw_state) if raw_state else TeamRecordState.PENDING,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def record_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        raw_decision_at = _optional(payload, "decision_at")
        state = repository.record_review_request(
            review_request_id=_optional(payload, "id") or _optional(payload, "review_request_id"),
            deliverable_id=_required(payload, "deliverable_id"),
            reviewer_agent_id=_required(payload, "reviewer_agent_id"),
            state=TeamRecordState(raw_state) if raw_state else TeamRecordState.PENDING,
            findings=_optional_string_list(payload, "findings"),
            review_profile_id=_optional(payload, "review_profile_id"),
            instructions=_optional(payload, "instructions"),
            decision_at=datetime.fromisoformat(raw_decision_at) if raw_decision_at else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def decide_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        criteria = (
            _optional_list(payload, "criteria") if payload.get("criteria") is not None else None
        )
        state = repository.record_review_decision(
            review_request_id=request.path_params["review_request_id"],
            state=TeamRecordState(_required(payload, "state")),
            findings=_optional_string_list(payload, "findings"),
            criteria=criteria,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_operator_directive(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        created_by = _optional(payload, "created_by") or _operator_actor_from_request(
            context.auth,
            request,
        )
        state, item = repository.create_operator_directive(
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
        response_payload = team_state_payload(state)
        response_payload["operator_directive"] = item.model_dump(mode="json")
        return JSONResponse(response_payload, status_code=201)

    async def dismiss_action_record(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.dismiss_action_record_attention(
            action_record_id=request.path_params["action_record_id"],
            dismissed_by=_optional(payload, "dismissed_by"),
            dismissed_reason=_optional(payload, "dismissed_reason"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_review_profile(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        criteria = [
            ReviewProfileCriterion.model_validate(item)
            for item in _optional_list(payload, "criteria")
        ]
        state = repository.create_review_profile(
            profile_id=_optional(payload, "id") or _optional(payload, "review_profile_id"),
            team_id=_required(payload, "team_id"),
            project_id=_optional(payload, "project_id"),
            name=_required(payload, "name"),
            description=_optional(payload, "description") or "",
            applicable_role_ids=_optional_string_list(payload, "applicable_role_ids"),
            criteria=criteria,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

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
        state = repository.update_review_profile(
            profile_id=request.path_params["review_profile_id"],
            name=_optional(payload, "name"),
            description=_optional_present_text(payload, "description"),
            applicable_role_ids=(
                _optional_string_list(payload, "applicable_role_ids")
                if "applicable_role_ids" in payload
                else None
            ),
            criteria=criteria,
            archived=raw_archived if isinstance(raw_archived, bool) else None,
            project_id=_optional(payload, "project_id"),
            set_project_id=has_project,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def list_review_profiles(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "review_profiles": [
                    profile.model_dump(mode="json")
                    for profile in state.review_profiles.values()
                ],
            }
        )

    async def get_review_profile(request: Request) -> JSONResponse:
        state = repository.read()
        profile = state.review_profiles.get(request.path_params["review_profile_id"])
        if profile is None:
            return JSONResponse({"error": "review profile not found"}, status_code=404)
        return JSONResponse(profile.model_dump(mode="json"))

    async def create_release_package(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state, result = repository.create_release_package_with_result(
            project_id=_required(payload, "project_id"),
            deliverable_ids=_optional_string_list(payload, "deliverable_ids") or [],
            release_id=_optional(payload, "id") or _optional(payload, "release_package_id"),
            release_branch=_optional(payload, "release_branch"),
            canonical_branch=_optional(payload, "canonical_branch"),
            expected_revision=_expected_revision(payload),
        )
        response_payload = team_state_payload(state)
        published_event_ids = _publish_team_events(context, result.get("events"))
        if published_event_ids:
            response_payload["published_event_ids"] = published_event_ids
        return JSONResponse(response_payload, status_code=201)

    async def list_release_packages(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "release_packages": [
                    release.model_dump(mode="json")
                    for release in state.release_packages.values()
                ],
            }
        )

    async def get_release_package(request: Request) -> JSONResponse:
        state = repository.read()
        release = state.release_packages.get(request.path_params["release_package_id"])
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        return JSONResponse(release.model_dump(mode="json"))

    async def list_git_promotions(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "git_promotions": [
                    promotion.model_dump(mode="json")
                    for promotion in state.git_promotions.values()
                ],
            }
        )

    async def get_git_promotion(request: Request) -> JSONResponse:
        state = repository.read()
        promotion = state.git_promotions.get(request.path_params["promotion_id"])
        if promotion is None:
            return JSONResponse({"error": "git promotion not found"}, status_code=404)
        return JSONResponse(promotion.model_dump(mode="json"))

    return [
        Route("/state", _with_errors(get_state), methods=["GET"]),
        Route("/templates", _with_errors(list_templates), methods=["GET"]),
        Route(
            "/templates/{template_id}",
            _with_errors(get_template_details),
            methods=["GET"],
        ),
        Route(
            "/templates/{template_id}/materialize",
            _with_errors(materialize_template),
            methods=["POST"],
        ),
        Route("/timeline", _with_errors(get_timeline), methods=["GET"]),
        Route("/costs", _with_errors(get_costs), methods=["GET"]),
        Route("/teams", _with_errors(create_team), methods=["POST"]),
        Route("/teams/{team_id}", _with_errors(patch_team), methods=["PATCH"]),
        Route("/governance-groups", _with_errors(create_governance_group), methods=["POST"]),
        Route(
            "/governance-groups/{governance_group_id}",
            _with_errors(patch_governance_group),
            methods=["PATCH"],
        ),
        Route(
            "/governance-agenda",
            _with_errors(create_governance_agenda_item),
            methods=["POST"],
        ),
        Route(
            "/governance-agenda/{agenda_item_id}/handle",
            _with_errors(handle_governance_agenda_item),
            methods=["POST"],
        ),
        Route("/projects", _with_errors(create_project), methods=["POST"]),
        Route("/projects/{project_id}", _with_errors(patch_project), methods=["PATCH"]),
        Route("/roles", _with_errors(create_role), methods=["POST"]),
        Route("/roles", _with_errors(list_roles), methods=["GET"]),
        Route("/roles/{role_id}", _with_errors(get_role), methods=["GET"]),
        Route("/roles/{role_id}", _with_errors(patch_role), methods=["PATCH"]),
        Route("/conversations", _with_errors(list_conversations), methods=["GET"]),
        Route("/conversations", _with_errors(create_conversation), methods=["POST"]),
        Route(
            "/conversations/{conversation_id}",
            _with_errors(get_conversation),
            methods=["GET"],
        ),
        Route(
            "/conversations/{conversation_id}/stream",
            _with_errors(stream_conversation),
            methods=["GET"],
        ),
        Route(
            "/conversations/{conversation_id}/messages",
            _with_errors(list_conversation_messages),
            methods=["GET"],
        ),
        Route(
            "/conversations/{conversation_id}/messages",
            _with_errors(send_conversation_message),
            methods=["POST"],
        ),
        Route("/agents", _with_errors(register_agent), methods=["POST"]),
        Route("/agents/{agent_id}", _with_errors(patch_agent), methods=["PATCH"]),
        Route(
            "/agents/{agent_id}/voice-sessions",
            _with_errors(create_agent_voice_session),
            methods=["POST"],
        ),
        Route(
            "/voice-sessions/{voice_session_id}/events",
            _with_errors(record_agent_voice_event),
            methods=["POST"],
        ),
        Route(
            "/voice-sessions/{voice_session_id}/end",
            _with_errors(end_agent_voice_session),
            methods=["POST"],
        ),
        Route(
            "/voice-session-requests/{request_id}/end",
            _with_errors(end_agent_voice_session_by_request),
            methods=["POST"],
        ),
        Route(
            "/voice-sessions/{voice_session_id}/queries",
            _with_errors(create_agent_voice_query),
            methods=["POST"],
        ),
        Route(
            "/voice-sessions/{voice_session_id}/queries/{query_id}",
            _with_errors(get_agent_voice_query),
            methods=["GET"],
        ),
        Route(
            "/voice-sessions/{voice_session_id}/queries/{query_id}/timeout",
            _with_errors(timeout_agent_voice_query),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/avatar/previews",
            _with_errors(create_avatar_preview),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/avatar/upload",
            _with_errors(upload_agent_avatar),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/avatar/accept-preview",
            _with_errors(accept_agent_avatar_preview),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/avatar",
            _with_errors(delete_agent_avatar),
            methods=["DELETE"],
        ),
        Route(
            "/avatars/{asset_id}",
            _with_errors(get_avatar_asset),
            methods=["GET"],
        ),
        Route(
            "/avatar-previews/{preview_id}",
            _with_errors(get_avatar_preview),
            methods=["GET"],
        ),
        Route(
            "/agents/{agent_id}/context-rotation",
            _with_errors(request_agent_context_rotation),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/stand-down",
            _with_errors(stand_down_agent),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/stand-down/revoke",
            _with_errors(revoke_agent_stand_down),
            methods=["POST"],
        ),
        Route("/tasks", _with_errors(create_task), methods=["POST"]),
        Route("/tasks/{task_id}", _with_errors(patch_task), methods=["PATCH"]),
        Route("/deliverables", _with_errors(record_deliverable), methods=["POST"]),
        Route("/review-requests", _with_errors(record_review_request), methods=["POST"]),
        Route(
            "/review-requests/{review_request_id}/decision",
            _with_errors(decide_review_request),
            methods=["POST"],
        ),
        Route(
            "/operator-directives",
            _with_errors(create_operator_directive),
            methods=["POST"],
        ),
        Route(
            "/action-records/{action_record_id}/dismiss",
            _with_errors(dismiss_action_record),
            methods=["POST"],
        ),
        Route("/review-profiles", _with_errors(create_review_profile), methods=["POST"]),
        Route("/review-profiles", _with_errors(list_review_profiles), methods=["GET"]),
        Route(
            "/review-profiles/{review_profile_id}",
            _with_errors(get_review_profile),
            methods=["GET"],
        ),
        Route(
            "/review-profiles/{review_profile_id}",
            _with_errors(patch_review_profile),
            methods=["PATCH"],
        ),
        Route("/release-packages", _with_errors(create_release_package), methods=["POST"]),
        Route("/release-packages", _with_errors(list_release_packages), methods=["GET"]),
        Route(
            "/release-packages/{release_package_id}",
            _with_errors(get_release_package),
            methods=["GET"],
        ),
        Route("/git-promotions", _with_errors(list_git_promotions), methods=["GET"]),
        Route(
            "/git-promotions/{promotion_id}",
            _with_errors(get_git_promotion),
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
        except (ValueError, TypeError, ValidationError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return _wrapped


def _session_repository_from_context(context: AddonContext) -> Any | None:
    service = getattr(context, "service", None)
    runtime = getattr(service, "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    if repository is not None:
        return repository
    return getattr(service, "session_repository", None)


def _runtime_from_context(context: AddonContext) -> Any:
    service = getattr(context, "service", None)
    runtime = getattr(service, "runtime", None)
    if runtime is None and service is not None and hasattr(service, "voice_session_store"):
        runtime = service
    if runtime is None:
        raise RuntimeError("Admin runtime is unavailable")
    return runtime


def _conversation_collection_payload(
    state: TeamState,
    *,
    selected_conversation_id: str | None = None,
) -> dict[str, Any]:
    conversations = sorted(
        state.conversations.values(),
        key=lambda conversation: (
            conversation.last_interaction_at,
            conversation.id,
        ),
        reverse=True,
    )
    return {
        "revision": state.revision,
        "selected_conversation_id": selected_conversation_id,
        "eligible_agents": [
            _chat_agent_payload(agent)
            for agent in sorted(state.agents.values(), key=lambda item: _agent_display_name(item))
            if _chat_agent_is_eligible(agent)
        ],
        "conversations": [
            _conversation_payload(state, conversation, include_messages=False)
            for conversation in conversations
        ],
    }


def _conversation_payload(
    state: TeamState,
    conversation: Any,
    *,
    include_messages: bool,
    message_limit: int = DEFAULT_CHAT_MESSAGE_LIMIT,
) -> dict[str, Any]:
    messages = _conversation_messages(state, conversation.id)
    payload = {
        "id": conversation.id,
        "agent_id": conversation.agent_id,
        "agent": _chat_agent_payload(state.agents.get(conversation.agent_id)),
        "created_by": conversation.created_by,
        "last_message_preview": conversation.last_message_preview,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
        "last_interaction_at": (
            conversation.last_interaction_at.isoformat()
            if conversation.last_interaction_at
            else None
        ),
        "message_count": len(messages),
    }
    if include_messages:
        page = _bounded_conversation_message_page(messages, limit=message_limit)
        payload.update(_conversation_message_page_fields(page, message_count=len(messages)))
    return payload


def _conversation_messages(state: TeamState, conversation_id: str) -> list[Any]:
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
    page: tuple[list[Any], bool],
    *,
    message_count: int,
) -> dict[str, Any]:
    messages, has_older = page
    return {
        "messages": [_conversation_message_payload(message) for message in messages],
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
    before_message_id: str | None = None,
    limit: int = DEFAULT_CHAT_MESSAGE_LIMIT,
) -> dict[str, Any]:
    messages = _conversation_messages(state, conversation.id)
    page = _bounded_conversation_message_page(
        messages,
        limit=limit,
        before_message_id=before_message_id,
    )
    payload = _conversation_message_page_fields(page, message_count=len(messages))
    payload["messages_limit"] = limit
    return {
        "revision": state.revision,
        "conversation_id": conversation.id,
        "before_message_id": before_message_id,
        **payload,
    }


def _conversation_snapshot_payload(
    repository: TeamStateRepository,
    conversation_id: str,
) -> dict[str, Any]:
    state = repository.read()
    conversation = state.conversations.get(conversation_id)
    if conversation is None:
        raise ValueError("conversation not found")
    return {
        "revision": state.revision,
        "conversation": _conversation_payload(
            state,
            conversation,
            include_messages=True,
        ),
    }


async def _conversation_snapshot_events(
    repository: TeamStateRepository,
    request: Request,
    conversation_id: str,
    *,
    poll_interval_seconds: float = 1.0,
) -> AsyncIterator[str]:
    last_serialized = ""
    try:
        snapshot = _conversation_snapshot_payload(repository, conversation_id)
    except ValueError:
        return
    last_serialized = json.dumps(snapshot, sort_keys=True)
    yield f"event: snapshot\ndata: {last_serialized}\n\n"
    while True:
        if await request.is_disconnected():
            break
        await asyncio.sleep(max(0.0, poll_interval_seconds))
        try:
            snapshot = _conversation_snapshot_payload(repository, conversation_id)
        except ValueError:
            break
        serialized = json.dumps(snapshot, sort_keys=True)
        if serialized == last_serialized:
            yield ": keepalive\n\n"
            continue
        last_serialized = serialized
        yield f"event: snapshot\ndata: {serialized}\n\n"


def _conversation_message_payload(message: Any) -> dict[str, Any]:
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
        "error": message.error,
        "metadata": dict(message.metadata or {}),
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "updated_at": message.updated_at.isoformat() if message.updated_at else None,
    }


def _chat_agent_payload(agent: Any | None) -> dict[str, Any] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "display_name": _agent_display_name(agent),
        "team_id": agent.team_id,
        "project_id": agent.project_id,
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


def _build_team_chat_prompt(
    *,
    state: TeamState,
    conversation: Any,
    message: Any,
) -> str:
    agent = state.agents[conversation.agent_id]
    recent_messages = [
        item
        for item in state.conversation_messages.values()
        if item.conversation_id == conversation.id
    ]
    recent_messages.sort(key=lambda item: (item.created_at, item.id))
    transcript_lines = []
    for item in recent_messages[-12:]:
        body = str(item.body or "").strip()
        if not body:
            continue
        transcript_lines.append(f"{item.sender}: {body}")
    transcript = "\n".join(transcript_lines)
    return (
        "You have received an operator message in the Team Add-on Chat tab.\n"
        "Treat this as the current operator request. Reply to the operator by writing "
        "/artifacts/team-action.json with a chat_replies entry. Do not use questions "
        "or status_reports for this operator reply; those are for agent-to-agent inbox "
        "communication.\n\n"
        f"Current agent: {_agent_display_name(agent)} ({agent.id})\n"
        f"Conversation ID: {conversation.id}\n"
        f"Operator message ID: {message.id}\n"
        f"Dispatch ID: {message.dispatch_id or ''}\n\n"
        "Required reply shape:\n"
        "{\n"
        f'  "schema_version": 10,\n'
        f'  "agent_id": "{agent.id}",\n'
        f'  "dispatch_id": "{message.dispatch_id or ""}",\n'
        '  "chat_replies": [\n'
        "    {\n"
        f'      "conversation_id": "{conversation.id}",\n'
        f'      "message_id": "{message.id}",\n'
        '      "body": "Your reply to the operator."\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Recent conversation:\n"
        f"{transcript or '(no earlier messages)'}\n\n"
        "Operator message:\n"
        f"{message.body}"
    )


def _resume_team_chat_agent(
    context: AddonContext,
    *,
    agent: Any,
    prompt: str,
    dispatch_id: str,
) -> Any:
    service = getattr(context, "service", None)
    resume_agent = getattr(service, "resume_agent", None)
    if not callable(resume_agent):
        raise RuntimeError("AdminService resume_agent is unavailable")
    return resume_agent(
        agent.session_id,
        prompt,
        dispatch_id=dispatch_id,
        resume_mode="same_context",
    )


def _dispatch_result_payload(result: Any) -> dict[str, Any]:
    if result is None:
        return {"success": False, "dispatch_id": None, "message": "no dispatch result"}
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
    elif isinstance(result, Mapping):
        payload = dict(result)
    else:
        payload = {
            "success": bool(getattr(result, "success", False)),
            "message": str(getattr(result, "message", "") or ""),
        }
    return {
        "success": bool(payload.get("success")),
        "dispatch_id": payload.get("dispatch_id"),
        "message": payload.get("message"),
        "action": payload.get("action"),
        "vm_name": payload.get("vm_name"),
        "thread_id": payload.get("thread_id"),
    }


def _seed_team_chat_agent_files(context: AddonContext, agent: Any) -> dict[str, Any]:
    runtime_context = _team_effect_runtime_context(context, agent=agent)
    agent_payload = agent.model_dump(mode="json") if hasattr(agent, "model_dump") else dict(agent)
    return {
        "action_contract": _seed_action_contract_files(
            runtime_context,
            agent=agent_payload,
            action_artifact_path=DEFAULT_ACTION_ARTIFACT_PATH,
        ),
        "agent_guidance": _seed_team_agent_guidance(
            runtime_context,
            agent=agent_payload,
        ),
    }


def _team_effect_runtime_context(
    context: AddonContext,
    *,
    agent: Any,
) -> EffectRuntimeContext:
    directory_path = "root"
    try:
        runtime = _runtime_from_context(context)
        session = runtime.session_repository.get_session(agent.session_id)
        directory_path = str(getattr(session, "directory_path", None) or "root")
    except Exception:
        directory_path = "root"
    return EffectRuntimeContext(
        resolved_config=context.config,
        metadata={"directory_path": directory_path},
        admin_service=context.service,
    )


def _record_chat_voice_call_status(
    repository: TeamStateRepository,
    record: Any,
) -> None:
    metadata = getattr(record, "caller_metadata", None)
    if not isinstance(metadata, Mapping) or not _optional(metadata, "conversation_id"):
        return
    try:
        repository.update_conversation_voice_call_status(
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
    project = state.projects.get(agent.project_id) if agent.project_id else None
    role = state.roles.get(agent.role_id)
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
            "id": project.id if project else agent.project_id,
            "name": project.name if project else agent.project_id,
            "status": _enum_value(project.status) if project else None,
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
            "permissions": [_enum_value(permission) for permission in (role.permissions if role else [])],
        },
        "runtime": {
            "wake_reason": getattr(runtime, "wake_reason", None),
            "active_dispatch_id": getattr(runtime, "active_dispatch_id", None),
            "wake_leased_until": _datetime_text(getattr(runtime, "wake_leased_until", None)),
            "last_wake_at": _datetime_text(getattr(runtime, "last_wake_at", None)),
            "next_wake_at": _datetime_text(getattr(runtime, "next_wake_at", None)),
            "no_progress_count": getattr(runtime, "no_progress_count", None),
            "last_progress_at": _datetime_text(getattr(runtime, "last_progress_at", None)),
            "auto_context_clearing_enabled": getattr(
                runtime,
                "auto_context_clearing_enabled",
                False,
            ),
            "context_rotation_pending": getattr(runtime, "context_rotation_pending", False),
            "last_handoff_summary": getattr(runtime, "last_handoff_summary", None),
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
        f"Project: {context_payload['project']['name'] or 'None'} "
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
            "project_id": agent.project_id,
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
        "project_id": agent.project_id,
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
        "transcript_events": [
            event.model_dump(mode="json") for event in record.transcript_events
        ],
        "reconciliation_status": record.reconciliation_status,
        "reconciliation_request_id": record.reconciliation_request_id,
        "error": record.error,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
    }


def _realtime_calls_url(config: Any) -> str:
    base_url = str(
        getattr(config, "openai_realtime_api_base_url", "https://api.openai.com/v1")
        or "https://api.openai.com/v1"
    ).strip().rstrip("/")
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
    bus = InternalEventBus(internal_event_store_path(context.runtime_dir))
    published_ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        published = bus.publish(
            event_type=_required_event_text(event.get("event_type"), "event_type"),
            payload=dict(payload),
            correlation_key=_optional_event_text(event.get("correlation_key")),
            idempotency_key=_optional_event_text(event.get("idempotency_key")),
            source=InternalEventSource(source_type="team-addon", source_name="team-addon"),
        )
        published_ids.append(published.event_id)
    return published_ids


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


def _optional(payload: Mapping[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _operator_actor_from_request(auth: Any | None, request: Request) -> str:
    session: Mapping[str, Any] | None = None
    read_session = getattr(auth, "read_session", None)
    if callable(read_session):
        raw_session = read_session(request)
        if isinstance(raw_session, Mapping):
            session = raw_session
    if session is None:
        require = getattr(auth, "require", None)
        if callable(require):
            raw_session = require(request)
            if isinstance(raw_session, Mapping):
                session = raw_session
    for key in ("login", "preferred_username", "username", "name", "sub"):
        value = str((session or {}).get(key) or "").strip()
        if value:
            return f"operator:{value}"
    return "operator"


def _optional_present_text(payload: Mapping[str, Any], field_name: str) -> str | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    return str(payload[field_name]).strip()


def _query_text(request: Request, field_name: str) -> str | None:
    value = request.query_params.get(field_name)
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


def _query_chat_message_limit(request: Request) -> int:
    value = _query_text(request, "limit")
    if value is None:
        return DEFAULT_CHAT_MESSAGE_LIMIT
    limit = _coerce_int(value, "limit")
    if limit < 1:
        raise ValueError("limit must be greater than zero")
    return min(limit, MAX_CHAT_MESSAGE_LIMIT)


def _optional_bool(payload: Mapping[str, Any], field_name: str) -> bool | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    value = payload[field_name]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{field_name} must be true or false")


def _optional_git_config(payload: Mapping[str, Any]) -> ProjectGitConfig | None:
    git_payload = payload.get("git")
    if git_payload is None:
        return None
    if not isinstance(git_payload, Mapping):
        raise ValueError("git must be a JSON object")
    normalized = dict(git_payload)
    bare_repo = _optional(normalized, "bare_repo")
    normalized.pop("bare_repo", None)
    if bare_repo:
        if normalized.get("bare_repo_name") or normalized.get("bare_repo_path"):
            raise ValueError(
                "bare_repo cannot be combined with bare_repo_name or bare_repo_path"
            )
        if bare_repo.startswith("/"):
            normalized["bare_repo_path"] = bare_repo
        else:
            normalized["bare_repo_name"] = bare_repo
    return ProjectGitConfig.model_validate(normalized)


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
