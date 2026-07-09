from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from .domain import common as _team_common
from ..repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from ..models import (
    AgentRuntime,
    AgentStatus,
    TeamConversation,
    TeamConversationMessage,
    TeamState,
    agent_has_project_assignment,
)
from .domain.chat import (
    _chat_preview,
    _touch_conversation,
)
from .domain.common import (
    _ensure_utc,
    _generated_id,
    _normalize_operator_user_id,
    _optional_text,
    _require_present,
    _required_text,
)


def utc_now() -> datetime:
    return _team_common.utc_now()


class TeamChatService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    def create_or_get_conversation(
        self,
        *,
        agent_id: str,
        operator_user_id: str | None = None,
        operator_display_name: str | None = None,
        created_by: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, TeamConversation, bool]:
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_operator_user_id = _normalize_operator_user_id(
            operator_user_id,
            created_by=created_by,
        )
        normalized_operator_display_name = _optional_text(operator_display_name)
        normalized_created_by = _optional_text(created_by)

        current_state = self._repository.read()
        if expected_revision is not None and current_state.revision != expected_revision:
            raise TeamStateConflictError(
                expected_revision=expected_revision,
                current_revision=current_state.revision,
            )
        current_agent = _require_present(current_state.agents, normalized_agent_id, "agent")
        if current_agent.status == AgentStatus.ARCHIVED:
            raise ValueError(f"agent {current_agent.id} is archived")
        for conversation in current_state.conversations.values():
            if (
                conversation.agent_id == current_agent.id
                and conversation.operator_user_id == normalized_operator_user_id
            ):
                return current_state, conversation, False

        conversation_id: str | None = None
        created = False

        def _mutate(state: TeamState) -> TeamState:
            nonlocal conversation_id, created
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            for conversation in state.conversations.values():
                if (
                    conversation.agent_id == agent.id
                    and conversation.operator_user_id == normalized_operator_user_id
                ):
                    conversation_id = conversation.id
                    return state
            now = utc_now()
            conversation_id = _generated_id(
                prefix="conversation",
                labels=[agent.id, normalized_operator_user_id],
                existing_ids=state.conversations,
            )
            state.conversations[conversation_id] = TeamConversation(
                id=conversation_id,
                agent_id=agent.id,
                operator_user_id=normalized_operator_user_id,
                operator_display_name=normalized_operator_display_name,
                created_by=normalized_created_by,
                last_message_preview="",
                created_at=now,
                updated_at=now,
                last_interaction_at=now,
            )
            created = True
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if conversation_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation was not resolved")
        return state, state.conversations[conversation_id], created

    def record_operator_chat_message(
        self,
        *,
        conversation_id: str,
        body: str,
        created_by: str | None = None,
        allow_team_mutations: bool = True,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        normalized_body = _required_text(body, "body")
        normalized_created_by = _optional_text(created_by)
        message_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal message_id
            conversation = _require_present(
                state.conversations,
                normalized_conversation_id,
                "conversation",
            )
            agent = _require_present(state.agents, conversation.agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            now = utc_now()
            message_id = f"chatmsg-{uuid4().hex}"
            metadata = {
                key: value
                for key, value in {
                    "sender_kind": "human_operator",
                    "operator_user_id": conversation.operator_user_id,
                    "operator_display_name": conversation.operator_display_name,
                    "created_by": normalized_created_by,
                }.items()
                if value
            }
            if not allow_team_mutations:
                metadata["allow_team_mutations"] = False
            state.conversation_messages[message_id] = TeamConversationMessage(
                id=message_id,
                conversation_id=conversation.id,
                sender="operator",
                message_type="text",
                body=normalized_body,
                status="created",
                metadata=metadata,
                created_at=now,
                updated_at=now,
            )
            _touch_conversation(
                conversation,
                now=now,
                preview=_chat_preview(normalized_body),
            )
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message was not created")
        return state, state.conversation_messages[message_id]

    def begin_chat_message_dispatch(
        self,
        *,
        message_id: str,
        dispatch_id: str,
        lease_seconds: int = 172800,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_message_id = _required_text(message_id, "message_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        resolved_message_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal resolved_message_id
            message = _require_present(
                state.conversation_messages,
                normalized_message_id,
                "conversation message",
            )
            conversation = _require_present(
                state.conversations,
                message.conversation_id,
                "conversation",
            )
            agent = _require_present(state.agents, conversation.agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            runtime = state.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            if (
                runtime.active_dispatch_id is not None
                and runtime.active_dispatch_id != normalized_dispatch_id
            ):
                raise ValueError(
                    f"agent {agent.id} already has active Team dispatch "
                    f"{runtime.active_dispatch_id}"
                )
            now = utc_now()
            runtime.active_dispatch_id = normalized_dispatch_id
            runtime.wake_leased_until = now + timedelta(seconds=max(1, int(lease_seconds)))
            runtime.wake_reason = "team_chat"
            runtime.last_wake_at = now
            runtime.next_wake_at = None
            runtime.updated_at = now
            message.status = "running"
            message.dispatch_id = normalized_dispatch_id
            message.error = None
            message.updated_at = now
            conversation.updated_at = now
            resolved_message_id = message.id
            return state

        state = self._repository.update(_mutate)
        if resolved_message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message dispatch was not started")
        return state, state.conversation_messages[resolved_message_id]

    def queue_chat_message_dispatch(
        self,
        *,
        message_id: str,
        dispatch_id: str,
        reason: str | None = None,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_message_id = _required_text(message_id, "message_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_reason = _optional_text(reason)
        resolved_message_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal resolved_message_id
            message = _require_present(
                state.conversation_messages,
                normalized_message_id,
                "conversation message",
            )
            conversation = _require_present(
                state.conversations,
                message.conversation_id,
                "conversation",
            )
            agent = _require_present(state.agents, conversation.agent_id, "agent")
            if agent.status == AgentStatus.ARCHIVED:
                raise ValueError(f"agent {agent.id} is archived")
            if message.sender != "operator":
                raise ValueError(f"message {message.id} is not an operator message")
            now = utc_now()
            metadata = dict(message.metadata)
            metadata["queued_at"] = now.isoformat()
            if normalized_reason is not None:
                metadata["queued_reason"] = normalized_reason
            message.status = "queued"
            message.dispatch_id = normalized_dispatch_id
            message.error = None
            message.metadata = metadata
            message.updated_at = now
            _touch_conversation(
                conversation,
                now=now,
                preview=f"Queued: {_chat_preview(message.body)}",
            )
            resolved_message_id = message.id
            return state

        state = self._repository.update(_mutate)
        if resolved_message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message dispatch was not queued")
        return state, state.conversation_messages[resolved_message_id]

    def claim_pending_chat_message_dispatches(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        lease_seconds: int = 172800,
        team_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
        blocked_team_ids: set[str] | None = None,
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_limit = max(0, int(limit))
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        blocked_teams = set(blocked_team_ids or set())
        claims: list[dict[str, Any]] = []
        if normalized_limit == 0:
            return self._repository.read(), claims

        def _mutate(state: TeamState) -> TeamState:
            messages = sorted(
                state.conversation_messages.values(),
                key=lambda item: (item.created_at, item.id),
            )
            for message in messages:
                if normalized_limit and len(claims) >= normalized_limit:
                    break
                if (
                    message.sender != "operator"
                    or message.message_type != "text"
                    or message.status != "queued"
                ):
                    continue
                conversation = state.conversations.get(message.conversation_id)
                if conversation is None:
                    continue
                agent = state.agents.get(conversation.agent_id)
                if agent is None or agent.status == AgentStatus.ARCHIVED:
                    continue
                if normalized_team_id is not None and agent.team_id != normalized_team_id:
                    continue
                if agent.team_id in blocked_teams:
                    continue
                if normalized_project_id is not None and not agent_has_project_assignment(
                    state,
                    agent.id,
                    normalized_project_id,
                ):
                    continue
                if normalized_agent_id is not None and agent.id != normalized_agent_id:
                    continue
                runtime = state.agent_runtimes.setdefault(
                    agent.id,
                    AgentRuntime(agent_id=agent.id),
                )
                if (
                    runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    continue
                dispatch_id = message.dispatch_id or f"team-chat:{message.id}"
                runtime.active_dispatch_id = dispatch_id
                runtime.wake_leased_until = effective_now + timedelta(
                    seconds=max(1, int(lease_seconds))
                )
                runtime.wake_reason = "team_chat"
                runtime.last_wake_at = effective_now
                runtime.next_wake_at = None
                runtime.updated_at = effective_now
                message.status = "running"
                message.dispatch_id = dispatch_id
                message.error = None
                message.updated_at = effective_now
                conversation.updated_at = effective_now
                claims.append(
                    {
                        "agent_id": agent.id,
                        "session_id": agent.session_id,
                        "conversation_id": conversation.id,
                        "message_id": message.id,
                        "dispatch_id": dispatch_id,
                    }
                )
            return state

        state = self._repository.update(_mutate)
        return state, claims

    def mark_chat_message_dispatch_failed(
        self,
        *,
        message_id: str,
        error: str,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_message_id = _required_text(message_id, "message_id")
        normalized_error = _required_text(error, "error")
        resolved_message_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal resolved_message_id
            message = _require_present(
                state.conversation_messages,
                normalized_message_id,
                "conversation message",
            )
            conversation = _require_present(
                state.conversations,
                message.conversation_id,
                "conversation",
            )
            runtime = state.agent_runtimes.get(conversation.agent_id)
            if (
                runtime is not None
                and message.dispatch_id is not None
                and runtime.active_dispatch_id == message.dispatch_id
            ):
                runtime.active_dispatch_id = None
                runtime.wake_leased_until = None
                runtime.wake_reason = None
                runtime.updated_at = utc_now()
            now = utc_now()
            message.status = "failed"
            message.error = normalized_error
            message.updated_at = now
            _touch_conversation(
                conversation,
                now=now,
                preview=f"Send failed: {_chat_preview(normalized_error)}",
            )
            resolved_message_id = message.id
            return state

        state = self._repository.update(_mutate)
        if resolved_message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message dispatch failure was not recorded")
        return state, state.conversation_messages[resolved_message_id]

    def record_conversation_voice_call(
        self,
        *,
        conversation_id: str,
        voice_session_id: str,
        status: str = "running",
        summary: str | None = None,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        normalized_voice_session_id = _required_text(voice_session_id, "voice_session_id")
        normalized_summary = _optional_text(summary)
        normalized_status = _optional_text(status) or "running"
        message_id: str | None = None

        current_state = self._repository.read()
        for message in current_state.conversation_messages.values():
            if message.voice_session_id == normalized_voice_session_id:
                return current_state, message

        def _mutate(state: TeamState) -> TeamState:
            nonlocal message_id
            for message in state.conversation_messages.values():
                if message.voice_session_id == normalized_voice_session_id:
                    message_id = message.id
                    return state
            conversation = _require_present(
                state.conversations,
                normalized_conversation_id,
                "conversation",
            )
            now = utc_now()
            message_id = f"chatmsg-{uuid4().hex}"
            body = normalized_summary or "Voice call"
            state.conversation_messages[message_id] = TeamConversationMessage(
                id=message_id,
                conversation_id=conversation.id,
                sender="system",
                message_type="voice_call",
                body=body,
                status="ended" if normalized_status == "ended" else "running",
                voice_session_id=normalized_voice_session_id,
                created_at=now,
                updated_at=now,
            )
            _touch_conversation(conversation, now=now, preview=body)
            return state

        state = self._repository.update(_mutate)
        if message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation voice call was not recorded")
        return state, state.conversation_messages[message_id]

    def update_conversation_voice_call_status(
        self,
        *,
        voice_session_id: str,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> tuple[TeamState, TeamConversationMessage] | None:
        normalized_voice_session_id = _required_text(voice_session_id, "voice_session_id")
        normalized_status = _optional_text(status) or "ended"
        normalized_summary = _optional_text(summary)
        normalized_error = _optional_text(error)
        current = self._repository.read()
        message_id = next(
            (
                message.id
                for message in current.conversation_messages.values()
                if message.voice_session_id == normalized_voice_session_id
            ),
            None,
        )
        if message_id is None:
            return None

        def _mutate(state: TeamState) -> TeamState:
            message = _require_present(
                state.conversation_messages,
                message_id,
                "conversation message",
            )
            conversation = _require_present(
                state.conversations,
                message.conversation_id,
                "conversation",
            )
            now = utc_now()
            message.status = "failed" if normalized_status == "failed" else "ended"
            if normalized_summary is not None:
                message.metadata = {**message.metadata, "summary": normalized_summary}
            if normalized_error is not None:
                message.error = normalized_error
            message.updated_at = now
            preview = "Voice call failed" if message.status == "failed" else "Voice call ended"
            _touch_conversation(conversation, now=now, preview=preview)
            return state

        state = self._repository.update(_mutate)
        return state, state.conversation_messages[message_id]
