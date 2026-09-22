"""Api chat."""

from __future__ import annotations

import asyncio
import json
from collections.abc import (
    AsyncIterator,
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from starlette.concurrency import (
    run_in_threadpool,
)
from starlette.requests import (
    Request,
)

from evander.addons import (
    AddonContext,
)

from .api_authorization import (
    _chat_scope_payload,
    _require_conversation_chat_scope,
    _team_chat_mutation_authorization_for_request,
)
from .api_constants import (
    DEFAULT_CHAT_MESSAGE_LIMIT,
)
from .api_requests import (
    _agent_display_name,
    _enum_value,
)
from .models import (
    AgentStatus,
    TeamState,
)
from .operator_chat_steering import (
    reconcile_operator_chat_steering_deliveries,
)
from .repository import (
    TeamStateRepository,
)


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
