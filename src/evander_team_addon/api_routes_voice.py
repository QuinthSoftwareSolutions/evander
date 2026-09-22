"""Team voice endpoints."""

from __future__ import annotations

from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)

from starlette.requests import (
    Request,
)
from starlette.responses import Response

from evander.email.http_utils import (
    ProviderRequestError,
)
from evander.voice_sessions import (
    OpenAIRealtimeClientSecretService,
    VoiceQueryDelegator,
    normalize_voice_session_status,
    voice_query_payload,
)

from .api_authorization import (
    _require_conversation_chat_scope,
)
from .api_chat import (
    _conversation_message_payload,
    _conversation_payload,
)
from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_requests import (
    _agent_display_name,
    _enum_value,
    _operator_identity_from_request,
    _optional,
    _optional_int,
    _optional_string_list,
    _read_optional_payload,
    _read_payload,
    _required,
    _runtime_from_context,
)
from .api_voice import (
    _agent_voice_effective_prompt,
    _agent_voice_profile_payload,
    _agent_voice_prompt_metadata,
    _browser_voice_reconciliation_context,
    _browser_voice_reconciliation_transcript,
    _browser_voice_transcript_speaker_label,
    _build_agent_voice_projection,
    _realtime_calls_url,
    _record_chat_voice_call_status,
    _require_browser_voice_session_operator,
    _session_is_expired,
    _team_voice_session_payload,
)
from .models import (
    AgentStatus,
    agent_project_ids,
)


def build_voice_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    chat_service = dependencies.chat_service

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

    return {
        "create_agent_voice_session": create_agent_voice_session,
        "record_agent_voice_event": record_agent_voice_event,
        "end_agent_voice_session": end_agent_voice_session,
        "end_agent_voice_session_by_request": end_agent_voice_session_by_request,
        "create_agent_voice_query": create_agent_voice_query,
        "get_agent_voice_query": get_agent_voice_query,
        "timeout_agent_voice_query": timeout_agent_voice_query,
    }
