"""Team chat endpoints."""

from __future__ import annotations

from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)
from typing import (
    Any,
)

from starlette.concurrency import (
    run_in_threadpool,
)
from starlette.datastructures import (
    UploadFile,
)
from starlette.requests import (
    Request,
)
from starlette.responses import (
    FileResponse,
    Response,
    StreamingResponse,
)

from .api_authorization import (
    _add_team_chat_mutation_modes,
    _chat_list_scope,
    _chat_scope_for_operator,
    _chat_scope_payload,
    _conversation_agent_directory_path,
    _require_conversation_chat_scope,
    _team_chat_mutation_authorization_for_request,
)
from .api_chat import (
    _conversation_collection_payload,
    _conversation_message_page_payload,
    _conversation_message_payload,
    _conversation_payload,
    _conversation_snapshot_events,
    _conversation_snapshot_state_and_payload,
    _reconciled_chat_state,
)
from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_requests import (
    _expected_revision,
    _operator_identity_from_request,
    _optional,
    _publish_team_events,
    _query_bool,
    _query_chat_message_limit,
    _query_text,
    _read_payload,
    _required,
    _session_repository_from_context,
)
from .chat_attachments import (
    normalize_directory_path,
    validate_attachment_count,
    validate_attachment_policy_content,
)
from .operator_chat_steering import (
    queue_operator_chat_steering,
)
from .services.domain.chat import (
    CHAT_MESSAGE_QUEUED_EVENT_TYPE,
)


def build_chat_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    chat_service = dependencies.chat_service
    chat_attachment_storage = dependencies.chat_attachment_storage
    chat_attachment_policy_service = dependencies.chat_attachment_policy_service

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

    return {
        "list_conversations": list_conversations,
        "create_conversation": create_conversation,
        "get_conversation": get_conversation,
        "list_conversation_messages": list_conversation_messages,
        "stream_conversation": stream_conversation,
        "send_conversation_message": send_conversation_message,
        "get_chat_attachment_policy": get_chat_attachment_policy,
        "put_chat_attachment_policy": put_chat_attachment_policy,
        "get_chat_attachment": get_chat_attachment,
    }
