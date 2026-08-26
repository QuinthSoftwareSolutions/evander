from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Sequence
from uuid import uuid4

from pydantic import ValidationError

from .domain import common as _team_common
from ..repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from ..models import (
    AgentContextClearingPolicy,
    AgentRuntime,
    AgentStatus,
    ExternalChatDeliveryRecord,
    ExternalChatIngressRecord,
    PersistentAgent,
    TeamDispatch,
    TeamDispatchStatus,
    TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS,
    TeamConversation,
    TeamConversationMessage,
    TeamChatMutationAuthorization,
    TeamChatAttachment,
    TeamOutboxRecord,
    TeamState,
    _unique_text_list,
    agent_has_project_assignment,
)
from .domain.agent_access import (
    _agent_payload,
    _visible_project_ids_for_agent,
    agent_role_description_fingerprint,
)
from .domain.chat import (
    CHAT_MESSAGE_QUEUED_EVENT_TYPE,
    _chat_preview,
    _chat_return_path_key,
    _requeue_unacknowledged_in_dispatch_messages,
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
from .domain.context_rotation import _mark_context_rotation_pending
from .domain.dispatch_scope import external_source_evidence_snapshot_ids_for_dispatch
from .domain.stand_down import (
    _active_stand_downs_for_agent,
    _stand_down_error_detail,
)
from ..chat_attachments import PreparedChatAttachment


def utc_now() -> datetime:
    return _team_common.utc_now()


def _agent_is_chat_dispatch_eligible(
    state: TeamState,
    agent: PersistentAgent,
) -> bool:
    return agent.status == AgentStatus.ACTIVE and not _active_stand_downs_for_agent(state, agent.id)


def _runtime_has_active_chat_or_agent_dispatch(
    state: TeamState,
    runtime: AgentRuntime,
) -> bool:
    dispatch_id = _optional_text(runtime.active_dispatch_id)
    if dispatch_id is None:
        return False
    dispatch = state.dispatches.get(dispatch_id)
    return dispatch is not None and dispatch.status in {
        TeamDispatchStatus.PREPARING,
        TeamDispatchStatus.LEASED,
    }


def _reconcile_stale_chat_dispatch_preparations(
    state: TeamState,
    *,
    now: datetime,
) -> None:
    for dispatch in list(state.dispatches.values()):
        if (
            dispatch.cause_kind != "team_chat"
            or dispatch.status != TeamDispatchStatus.PREPARING
            or dispatch.preparation_expires_at is None
            or _ensure_utc(dispatch.preparation_expires_at) > now
        ):
            continue
        error = "chat dispatch preparation reservation expired before durable prompt delivery"
        retry_dispatch_id = f"team-chat:{dispatch.cause_id or dispatch.id}:retry:{uuid4().hex}"
        dispatch.status = TeamDispatchStatus.FAILED
        dispatch.completed_at = now
        dispatch.error = error
        runtime = state.agent_runtimes.get(dispatch.agent_id)
        if runtime is not None and runtime.active_dispatch_id == dispatch.id:
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.wake_reason = None
            runtime.next_wake_at = None
            runtime.updated_at = now
        stale_message_ids = list(dispatch.chat_message_ids)
        for message_id in stale_message_ids:
            message = state.conversation_messages.get(message_id)
            if message is None or message.status != "dispatching":
                continue
            message.status = "queued"
            message.dispatch_id = retry_dispatch_id
            message.error = error
            message.updated_at = now
            for ingress in state.external_chat_ingress.values():
                if ingress.message_id == message.id:
                    ingress.dispatch_id = retry_dispatch_id
                    ingress.updated_at = now
        # A retried chat message can belong to only one active dispatch. The
        # stale dispatch retains its cause id and terminal diagnostic while the
        # replacement owns the durable message scope.
        dispatch.chat_message_ids = []


def _require_agent_chat_dispatch_eligibility(
    state: TeamState,
    agent: PersistentAgent,
) -> None:
    if agent.status == AgentStatus.ARCHIVED:
        raise ValueError(f"agent {agent.id} is archived")
    if agent.status != AgentStatus.ACTIVE:
        raise ValueError(f"agent {agent.id} is not active")
    if active_stand_downs := _active_stand_downs_for_agent(state, agent.id):
        raise ValueError(
            f"agent {agent.id} is stood down: {_stand_down_error_detail(active_stand_downs)}"
        )


def _attachment_preview(count: int) -> str:
    return f"Sent {count} attachment{'s' if count != 1 else ''}"


TEAM_CHAT_MUTATION_AUTHORIZATION_METADATA_KEY = "team_mutation_authorization"


def _record_operator_chat_message_in_state(
    state: TeamState,
    *,
    conversation_id: str,
    body: str | None,
    attachments: Sequence[PreparedChatAttachment],
    created_by: str | None,
    mutation_authorization: TeamChatMutationAuthorization | None,
) -> TeamConversationMessage:
    conversation = _require_present(
        state.conversations,
        conversation_id,
        "conversation",
    )
    agent = _require_present(state.agents, conversation.agent_id, "agent")
    _require_agent_chat_dispatch_eligibility(state, agent)
    now = utc_now()
    message_id = f"chatmsg-{uuid4().hex}"
    metadata = {
        key: value
        for key, value in {
            "sender_kind": "human_operator",
            "operator_user_id": conversation.operator_user_id,
            "operator_display_name": conversation.operator_display_name,
            "created_by": created_by,
        }.items()
        if value
    }
    effective_authorization = mutation_authorization or reply_only_team_chat_authorization(
        source="internal",
        principal_id=conversation.operator_user_id,
        reason="mutation_authorization_missing",
    )
    metadata[TEAM_CHAT_MUTATION_AUTHORIZATION_METADATA_KEY] = (
        effective_authorization.model_dump(mode="json")
    )
    metadata["allow_team_mutations"] = effective_authorization.mode == "project_scoped"
    message = TeamConversationMessage(
        id=message_id,
        conversation_id=conversation.id,
        sender="operator",
        message_type="text",
        body=body,
        attachment_ids=[attachment.id for attachment in attachments],
        status="created",
        metadata=metadata,
        created_at=now,
        updated_at=now,
    )
    state.conversation_messages[message_id] = message
    for attachment in attachments:
        state.chat_attachments[attachment.id] = TeamChatAttachment(
            **attachment.payload(),
            message_id=message_id,
            conversation_id=conversation.id,
            sender="operator",
            created_at=now,
        )
    _touch_conversation(
        conversation,
        now=now,
        preview=(
            _chat_preview(body)
            if body is not None
            else _attachment_preview(len(attachments))
        ),
    )
    return message


def _queue_operator_chat_message_in_state(
    state: TeamState,
    *,
    message_id: str,
    dispatch_id: str,
    reason: str | None,
) -> TeamConversationMessage:
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
    agent = _require_present(state.agents, conversation.agent_id, "agent")
    _require_agent_chat_dispatch_eligibility(state, agent)
    if message.sender != "operator":
        raise ValueError(f"message {message.id} is not an operator message")
    now = utc_now()
    metadata = dict(message.metadata)
    metadata["queued_at"] = now.isoformat()
    if reason is not None:
        metadata["queued_reason"] = reason
    message.status = "queued"
    message.dispatch_id = dispatch_id
    message.error = None
    message.metadata = metadata
    message.updated_at = now
    _touch_conversation(
        conversation,
        now=now,
        preview=f"Queued: {_chat_preview(message.body)}",
    )
    return message


def reply_only_team_chat_authorization(
    *,
    source: str,
    principal_id: str,
    reason: str,
    recipient_directory_path: str | None = None,
) -> TeamChatMutationAuthorization:
    return TeamChatMutationAuthorization(
        mode="reply_only",
        source=source,
        principal_id=principal_id,
        recipient_directory_path=recipient_directory_path,
        reason=reason,
    )


def project_scoped_team_chat_authorization(
    state: TeamState,
    *,
    agent: PersistentAgent,
    source: str,
    principal_id: str,
    recipient_directory_path: str,
    project_is_authorized: Callable[[str], bool],
) -> TeamChatMutationAuthorization:
    project_ids = sorted(
        project_id
        for project_id in _visible_project_ids_for_agent(state, agent)
        if project_id in state.projects
        and project_is_authorized(state.projects[project_id].directory_path)
    )
    if not project_ids:
        return reply_only_team_chat_authorization(
            source=source,
            principal_id=principal_id,
            recipient_directory_path=recipient_directory_path,
            reason="no_authorized_projects",
        )
    managed_repo_ids = sorted(
        {
            project.git.managed_repo_id
            for project_id in project_ids
            if (project := state.projects.get(project_id)) is not None and project.git is not None
        }
    )
    return TeamChatMutationAuthorization(
        mode="project_scoped",
        source=source,
        principal_id=principal_id,
        recipient_directory_path=recipient_directory_path,
        project_ids=project_ids,
        managed_repo_ids=managed_repo_ids,
    )


def _message_team_chat_mutation_authorization(
    message: TeamConversationMessage,
) -> TeamChatMutationAuthorization | None:
    raw = message.metadata.get(TEAM_CHAT_MUTATION_AUTHORIZATION_METADATA_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return TeamChatMutationAuthorization.model_validate(raw)
    except ValidationError:
        return None


def _matching_team_chat_dispatch(
    message: TeamConversationMessage,
    dispatch: TeamDispatch,
) -> bool:
    return dispatch.cause_kind == "team_chat" and (
        message.id in dispatch.chat_message_ids or dispatch.cause_id == message.id
    )


def _chat_mutation_scope_key(message: TeamConversationMessage) -> tuple[object, ...]:
    authorization = _message_team_chat_mutation_authorization(message)
    if authorization is None:
        return ("missing",)
    return (
        authorization.mode,
        authorization.source,
        authorization.principal_id,
        authorization.recipient_directory_path,
        tuple(authorization.project_ids),
        tuple(authorization.managed_repo_ids),
    )


def _chat_batch_key(message: TeamConversationMessage) -> tuple[object, ...]:
    return (
        message.conversation_id,
        *_chat_return_path_key(message),
        *_chat_mutation_scope_key(message),
    )


def team_chat_mutation_allowed(
    message: TeamConversationMessage,
    dispatch: TeamDispatch,
) -> bool:
    authorization = _message_team_chat_mutation_authorization(message)
    return (
        authorization is not None
        and authorization.mode == "project_scoped"
        and _matching_team_chat_dispatch(message, dispatch)
        and bool(dispatch.project_ids)
    )


def team_chat_mutation_scope_lost(
    message: TeamConversationMessage,
    dispatch: TeamDispatch,
) -> bool:
    authorization = _message_team_chat_mutation_authorization(message)
    return (
        authorization is not None
        and authorization.mode == "project_scoped"
        and _matching_team_chat_dispatch(message, dispatch)
        and not dispatch.project_ids
    )


def team_chat_in_dispatch_delivery_allowed(
    message: TeamConversationMessage,
    dispatch: TeamDispatch,
) -> bool:
    """Require the message's server-issued scope to contain the active dispatch."""

    authorization = _message_team_chat_mutation_authorization(message)
    if authorization is None:
        return False
    if (
        dispatch.cause_kind == "team_chat"
        and not dispatch.project_ids
        and not dispatch.managed_repo_ids
    ):
        return True
    if authorization.mode != "project_scoped" or not dispatch.project_ids:
        return False
    return set(dispatch.project_ids).issubset(authorization.project_ids) and set(
        dispatch.managed_repo_ids
    ).issubset(authorization.managed_repo_ids)


def _operator_chat_dispatch_scope(
    state: TeamState,
    *,
    agent: PersistentAgent,
    message: TeamConversationMessage,
) -> dict[str, list[str]]:
    authorization = _message_team_chat_mutation_authorization(message)
    if authorization is None or authorization.mode != "project_scoped":
        return {
            "project_ids": [],
            "managed_repo_ids": [],
            "source_evidence_snapshot_ids": [],
        }
    snapshotted_project_ids = set(authorization.project_ids)
    project_ids = sorted(
        project_id
        for project_id in _visible_project_ids_for_agent(state, agent)
        if project_id in snapshotted_project_ids and project_id in state.projects
    )
    snapshotted_managed_repo_ids = set(authorization.managed_repo_ids)
    managed_repo_ids = sorted(
        {
            project.git.managed_repo_id
            for project_id in project_ids
            if (project := state.projects.get(project_id)) is not None
            and project.git is not None
            and project.git.managed_repo_id in snapshotted_managed_repo_ids
        }
    )
    return {
        "project_ids": project_ids,
        "managed_repo_ids": managed_repo_ids,
        "source_evidence_snapshot_ids": list(
            external_source_evidence_snapshot_ids_for_dispatch(
                state,
                agent=agent,
                task_ids=(),
                project_ids=project_ids,
            )
        ),
    }


def _prepare_chat_context_rotation(
    state: TeamState,
    *,
    agent: PersistentAgent,
    runtime: AgentRuntime,
    now: datetime,
) -> None:
    if (
        runtime.context_clearing_policy == AgentContextClearingPolicy.EVERY_WAKE
        and not runtime.context_rotation_pending
    ):
        _mark_context_rotation_pending(
            state,
            agent=agent,
            runtime=runtime,
            reason="every_wake",
            requested_by=None,
            requested_at=now,
            request_note=None,
        )


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

    def enqueue_external_operator_chat_message(
        self,
        *,
        conversation_id: str | None,
        agent_id: str | None,
        operator_user_id: str,
        operator_display_name: str | None,
        body: str,
        provider: str,
        communication_target_id: str,
        source_message_id: str,
        source_thread_id: str | None,
        source_author_id: str,
        source_author_name: str | None,
        mutation_authorization: TeamChatMutationAuthorization | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        """Atomically persist and queue one externally-originated Team Chat message."""

        normalized_conversation_id = _optional_text(conversation_id)
        normalized_agent_id = _optional_text(agent_id)
        if (normalized_conversation_id is None) == (normalized_agent_id is None):
            raise ValueError("exactly one of conversation_id and agent_id is required")
        normalized_operator_user_id = _normalize_operator_user_id(operator_user_id)
        normalized_body = _required_text(body, "body")
        normalized_provider = _required_text(provider, "provider").lower()
        normalized_target_id = _required_text(
            communication_target_id,
            "communication_target_id",
        ).lower()
        normalized_source_message_id = _required_text(source_message_id, "source_message_id")
        normalized_source_author_id = _required_text(source_author_id, "source_author_id")
        normalized_operator_display_name = _optional_text(operator_display_name)
        normalized_source_thread_id = _optional_text(source_thread_id)
        normalized_source_author_name = _optional_text(source_author_name)

        def _existing_result(state: TeamState) -> dict[str, Any] | None:
            for record in state.external_chat_ingress.values():
                if (
                    record.provider == normalized_provider
                    and record.communication_target_id == normalized_target_id
                    and record.source_message_id == normalized_source_message_id
                ):
                    return {
                        "success": True,
                        "queued": True,
                        "conversation_id": record.conversation_id,
                        "message_id": record.message_id,
                        "dispatch_id": record.dispatch_id,
                        "duplicate": True,
                    }
            return None

        existing = _existing_result(self._repository.read())
        if existing is not None:
            return self._repository.read(), existing

        result: dict[str, Any] = {}
        outbox_records: list[TeamOutboxRecord] = []

        def _mutate(state: TeamState) -> TeamState:
            nonlocal result, outbox_records
            existing_inside = _existing_result(state)
            if existing_inside is not None:
                result = existing_inside
                return state
            now = utc_now()
            resolved_conversation_id = normalized_conversation_id
            if resolved_conversation_id is None:
                agent = _require_present(state.agents, normalized_agent_id, "agent")
                _require_agent_chat_dispatch_eligibility(state, agent)
                for candidate in state.conversations.values():
                    if (
                        candidate.agent_id == agent.id
                        and candidate.operator_user_id == normalized_operator_user_id
                    ):
                        resolved_conversation_id = candidate.id
                        break
                if resolved_conversation_id is None:
                    resolved_conversation_id = _generated_id(
                        prefix="conversation",
                        labels=[agent.id, normalized_operator_user_id],
                        existing_ids=state.conversations,
                    )
                    state.conversations[resolved_conversation_id] = TeamConversation(
                        id=resolved_conversation_id,
                        agent_id=agent.id,
                        operator_user_id=normalized_operator_user_id,
                        operator_display_name=normalized_operator_display_name,
                        created_by="external-chat-bridge",
                        last_message_preview="",
                        created_at=now,
                        updated_at=now,
                        last_interaction_at=now,
                    )
            conversation = _require_present(
                state.conversations,
                resolved_conversation_id,
                "conversation",
            )
            if conversation.operator_user_id != normalized_operator_user_id:
                raise ValueError(
                    f"conversation {conversation.id} does not belong to configured operator "
                    f"{normalized_operator_user_id}"
                )
            agent = _require_present(state.agents, conversation.agent_id, "agent")
            _require_agent_chat_dispatch_eligibility(state, agent)
            message_id = f"chatmsg-{uuid4().hex}"
            dispatch_id = f"team-chat:{message_id}"
            origin = {
                "provider": normalized_provider,
                "communication_target_id": normalized_target_id,
                "source_message_id": normalized_source_message_id,
                "source_thread_id": normalized_source_thread_id,
                "source_author_id": normalized_source_author_id,
                "source_author_name": normalized_source_author_name,
            }
            metadata: dict[str, Any] = {
                "sender_kind": "human_operator",
                "operator_user_id": conversation.operator_user_id,
                "operator_display_name": conversation.operator_display_name,
                "created_by": "external-chat-bridge",
                "external_origin": origin,
                "queued_at": now.isoformat(),
                "queued_reason": "queued by external communication bridge",
            }
            effective_authorization = mutation_authorization or reply_only_team_chat_authorization(
                source="external_bridge",
                principal_id=normalized_source_author_id,
                reason="mutation_authorization_missing",
            )
            metadata[TEAM_CHAT_MUTATION_AUTHORIZATION_METADATA_KEY] = (
                effective_authorization.model_dump(mode="json")
            )
            metadata["allow_team_mutations"] = effective_authorization.mode == "project_scoped"
            state.conversation_messages[message_id] = TeamConversationMessage(
                id=message_id,
                conversation_id=conversation.id,
                sender="operator",
                message_type="text",
                body=normalized_body,
                status="queued",
                dispatch_id=dispatch_id,
                metadata=metadata,
                created_at=now,
                updated_at=now,
            )
            _touch_conversation(
                conversation,
                now=now,
                preview=f"Queued: {_chat_preview(normalized_body)}",
            )
            ingress_id = f"external-chat-ingress-{uuid4().hex}"
            state.external_chat_ingress[ingress_id] = ExternalChatIngressRecord(
                id=ingress_id,
                provider=normalized_provider,
                communication_target_id=normalized_target_id,
                source_message_id=normalized_source_message_id,
                source_thread_id=normalized_source_thread_id,
                source_author_id=normalized_source_author_id,
                source_author_name=normalized_source_author_name,
                operator_user_id=normalized_operator_user_id,
                conversation_id=conversation.id,
                message_id=message_id,
                dispatch_id=dispatch_id,
                allow_team_mutations=effective_authorization.mode == "project_scoped",
                created_at=now,
                updated_at=now,
            )
            outbox_records = [
                TeamOutboxRecord(
                    id=f"outbox-{uuid4().hex}",
                    intent_type=CHAT_MESSAGE_QUEUED_EVENT_TYPE,
                    aggregate_type="team_chat_message",
                    aggregate_id=message_id,
                    idempotency_key=(
                        f"team-addon:{CHAT_MESSAGE_QUEUED_EVENT_TYPE}:"
                        f"{normalized_provider}:{normalized_target_id}:{normalized_source_message_id}"
                    ),
                    payload={
                        "event_type": CHAT_MESSAGE_QUEUED_EVENT_TYPE,
                        "correlation_key": dispatch_id,
                        "payload": {
                            "team_id": agent.team_id,
                            "agent_id": agent.id,
                            "session_id": agent.session_id,
                            "conversation_id": conversation.id,
                            "message_id": message_id,
                            "dispatch_id": dispatch_id,
                        },
                    },
                    created_at=now,
                    updated_at=now,
                )
            ]
            result = {
                "success": True,
                "queued": True,
                "conversation_id": conversation.id,
                "message_id": message_id,
                "dispatch_id": dispatch_id,
                "duplicate": False,
            }
            return state

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: outbox_records,
        )
        if not result:  # pragma: no cover - defensive guard
            raise RuntimeError("external chat message was not resolved")
        return state, result

    def record_external_chat_delivery(
        self,
        *,
        delivery_id: str,
        success: bool,
        provider_message_id: str | None = None,
        error: str | None = None,
    ) -> tuple[TeamState, ExternalChatDeliveryRecord]:
        normalized_delivery_id = _required_text(delivery_id, "delivery_id")
        normalized_provider_message_id = _optional_text(provider_message_id)
        normalized_error = _optional_text(error)
        resolved_delivery_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal resolved_delivery_id
            record = _require_present(
                state.external_chat_deliveries,
                normalized_delivery_id,
                "external chat delivery",
            )
            if record.status == "sent":
                resolved_delivery_id = record.id
                return state
            now = utc_now()
            record.status = "sent" if success else "failed"
            record.provider_message_id = normalized_provider_message_id
            record.last_error = None if success else (normalized_error or "external send failed")
            record.attempt_count += 1
            record.attempted_at = now
            record.delivered_at = now if success else None
            record.updated_at = now
            reply = state.conversation_messages.get(record.agent_reply_message_id)
            if reply is not None:
                metadata = dict(reply.metadata)
                metadata["external_delivery"] = {
                    "delivery_id": record.id,
                    "provider": record.provider,
                    "communication_target_id": record.communication_target_id,
                    "state": record.status,
                    "provider_message_id": record.provider_message_id,
                    "attempt_count": record.attempt_count,
                    "last_error": record.last_error,
                    "updated_at": now.isoformat(),
                }
                reply.metadata = metadata
                reply.updated_at = now
            resolved_delivery_id = record.id
            return state

        state = self._repository.update(_mutate)
        if resolved_delivery_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("external chat delivery was not recorded")
        return state, state.external_chat_deliveries[resolved_delivery_id]

    def record_operator_chat_message(
        self,
        *,
        conversation_id: str,
        body: str | None,
        attachments: Sequence[PreparedChatAttachment] = (),
        created_by: str | None = None,
        mutation_authorization: TeamChatMutationAuthorization | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, TeamConversationMessage]:
        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        normalized_body = _optional_text(body)
        prepared_attachments = list(attachments)
        if normalized_body is None and not prepared_attachments:
            raise ValueError("body or at least one attachment is required")
        normalized_created_by = _optional_text(created_by)
        message_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal message_id
            message = _record_operator_chat_message_in_state(
                state,
                conversation_id=normalized_conversation_id,
                body=normalized_body,
                attachments=prepared_attachments,
                created_by=normalized_created_by,
                mutation_authorization=mutation_authorization,
            )
            message_id = message.id
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message was not created")
        return state, state.conversation_messages[message_id]

    def record_and_queue_operator_chat_message(
        self,
        *,
        conversation_id: str,
        body: str | None,
        attachments: Sequence[PreparedChatAttachment] = (),
        created_by: str | None = None,
        mutation_authorization: TeamChatMutationAuthorization | None = None,
        reason: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, TeamConversationMessage, str]:
        """Persist an operator message and its asynchronous dispatch in one update."""

        normalized_conversation_id = _required_text(conversation_id, "conversation_id")
        normalized_body = _optional_text(body)
        prepared_attachments = list(attachments)
        if normalized_body is None and not prepared_attachments:
            raise ValueError("body or at least one attachment is required")
        normalized_created_by = _optional_text(created_by)
        normalized_reason = _optional_text(reason)
        message_id: str | None = None
        dispatch_id: str | None = None

        def _mutate(state: TeamState) -> TeamState:
            nonlocal message_id, dispatch_id
            message = _record_operator_chat_message_in_state(
                state,
                conversation_id=normalized_conversation_id,
                body=normalized_body,
                attachments=prepared_attachments,
                created_by=normalized_created_by,
                mutation_authorization=mutation_authorization,
            )
            message_id = message.id
            dispatch_id = f"team-chat:{message.id}"
            _queue_operator_chat_message_in_state(
                state,
                message_id=message.id,
                dispatch_id=dispatch_id,
                reason=normalized_reason,
            )
            return state

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        if message_id is None or dispatch_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message dispatch was not queued")
        return state, state.conversation_messages[message_id], dispatch_id

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
            _require_agent_chat_dispatch_eligibility(state, agent)
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
            _prepare_chat_context_rotation(
                state,
                agent=agent,
                runtime=runtime,
                now=now,
            )
            lease_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
            existing_dispatch = state.dispatches.get(normalized_dispatch_id)
            if existing_dispatch is None:
                chat_scope = _operator_chat_dispatch_scope(
                    state,
                    agent=agent,
                    message=message,
                )
                state.dispatches[normalized_dispatch_id] = TeamDispatch(
                    id=normalized_dispatch_id,
                    agent_id=agent.id,
                    session_id=agent.session_id,
                    cause_kind="team_chat",
                    cause_id=message.id,
                    chat_message_ids=[message.id],
                    **chat_scope,
                    status=TeamDispatchStatus.LEASED,
                    created_at=now,
                    requested_lease_seconds=max(1, int(lease_seconds)),
                    prepared_at=now,
                    leased_at=now,
                    lease_expires_at=lease_expires_at,
                )
            elif (
                existing_dispatch.agent_id != agent.id
                or existing_dispatch.session_id != agent.session_id
                or existing_dispatch.status != TeamDispatchStatus.LEASED
            ):
                raise ValueError(
                    f"chat dispatch {normalized_dispatch_id} is not a current "
                    f"lease for agent {agent.id}"
                )
            runtime.active_dispatch_id = normalized_dispatch_id
            runtime.wake_leased_until = lease_expires_at
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
            message = _queue_operator_chat_message_in_state(
                state,
                message_id=normalized_message_id,
                dispatch_id=normalized_dispatch_id,
                reason=normalized_reason,
            )
            resolved_message_id = message.id
            return state

        state = self._repository.update(_mutate)
        if resolved_message_id is None:  # pragma: no cover - defensive guard
            raise RuntimeError("conversation message dispatch was not queued")
        return state, state.conversation_messages[resolved_message_id]

    def list_pending_operator_attention_messages(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        limit: int = 5,
        now: datetime | None = None,
    ) -> tuple[TeamState, list[TeamConversationMessage]]:
        """Surface text-only operator messages without consuming their durable state."""

        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_limit = max(0, int(limit))
        effective_now = _ensure_utc(now or utc_now())
        if normalized_limit == 0:
            return self._repository.read(), []
        state = self._repository.read()
        agent = _require_present(state.agents, normalized_agent_id, "agent")
        runtime = state.agent_runtimes.get(agent.id)
        dispatch = state.dispatches.get(normalized_dispatch_id)
        if (
            runtime is None
            or runtime.active_dispatch_id != normalized_dispatch_id
            or runtime.wake_leased_until is None
            or _ensure_utc(runtime.wake_leased_until) <= effective_now
            or dispatch is None
            or dispatch.agent_id != agent.id
            or dispatch.session_id != agent.session_id
            or dispatch.status != TeamDispatchStatus.LEASED
            or dispatch.lease_expires_at is None
            or _ensure_utc(dispatch.lease_expires_at) <= effective_now
        ):
            return state, []
        messages: list[TeamConversationMessage] = []
        for message in sorted(
            state.conversation_messages.values(),
            key=lambda item: (item.created_at, item.id),
        ):
            if len(messages) >= normalized_limit:
                break
            if (
                message.sender != "operator"
                or message.message_type != "text"
                or message.status not in {"queued", "delivered_in_dispatch"}
                or message.attachment_ids
            ):
                continue
            conversation = state.conversations.get(message.conversation_id)
            if (
                conversation is not None
                and conversation.agent_id == agent.id
                and team_chat_in_dispatch_delivery_allowed(message, dispatch)
            ):
                messages.append(message)
        return state, messages

    def mark_operator_attention_messages_delivered(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        message_ids: Sequence[str],
        now: datetime | None = None,
    ) -> tuple[TeamState, list[TeamConversationMessage]]:
        """Record actual Team-tool-result delivery without consuming the messages."""

        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        normalized_message_ids = _unique_text_list(message_ids, "message_ids")
        effective_now = _ensure_utc(now or utc_now())

        def _mutate(state: TeamState) -> TeamState:
            agent = _require_present(state.agents, normalized_agent_id, "agent")
            runtime = state.agent_runtimes.get(agent.id)
            dispatch = state.dispatches.get(normalized_dispatch_id)
            if (
                runtime is None
                or runtime.active_dispatch_id != normalized_dispatch_id
                or runtime.wake_leased_until is None
                or _ensure_utc(runtime.wake_leased_until) <= effective_now
                or dispatch is None
                or dispatch.agent_id != agent.id
                or dispatch.session_id != agent.session_id
                or dispatch.status != TeamDispatchStatus.LEASED
                or dispatch.lease_expires_at is None
                or _ensure_utc(dispatch.lease_expires_at) <= effective_now
            ):
                return state
            for message_id in normalized_message_ids:
                message = state.conversation_messages.get(message_id)
                if (
                    message is None
                    or message.sender != "operator"
                    or message.message_type != "text"
                    or message.status not in {"queued", "delivered_in_dispatch"}
                    or message.attachment_ids
                ):
                    continue
                conversation = state.conversations.get(message.conversation_id)
                if conversation is None or conversation.agent_id != agent.id:
                    continue
                if not team_chat_in_dispatch_delivery_allowed(message, dispatch):
                    continue
                if message.status == "delivered_in_dispatch":
                    continue
                metadata = dict(message.metadata)
                raw_delivery = metadata.get("operator_steering")
                delivery = dict(raw_delivery) if isinstance(raw_delivery, dict) else {}
                delivery.update(
                    {
                        "dispatch_id": normalized_dispatch_id,
                        "session_id": agent.session_id,
                        "state": "delivered",
                        "delivery_channel": "team_mcp_result",
                        "delivered_at": effective_now.isoformat(),
                    }
                )
                metadata["operator_steering"] = delivery
                message.status = "delivered_in_dispatch"
                message.seen_at = effective_now
                message.seen_dispatch_id = normalized_dispatch_id
                message.error = None
                message.metadata = metadata
                message.updated_at = effective_now
            return state

        state = self._repository.update(_mutate)
        return state, [
            state.conversation_messages[message_id]
            for message_id in normalized_message_ids
            if message_id in state.conversation_messages
            and state.conversation_messages[message_id].status == "delivered_in_dispatch"
        ]

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
        session_is_busy: Callable[[str], bool] | None = None,
        defer_activation: bool = False,
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
            _reconcile_stale_chat_dispatch_preparations(
                state,
                now=effective_now,
            )
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
                if agent is None or not _agent_is_chat_dispatch_eligible(state, agent):
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
                if session_is_busy is not None and session_is_busy(agent.session_id):
                    continue
                runtime = state.agent_runtimes.setdefault(
                    agent.id,
                    AgentRuntime(agent_id=agent.id),
                )
                if _runtime_has_active_chat_or_agent_dispatch(state, runtime) or (
                    runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    continue
                batch_key = _chat_batch_key(message)
                batch_messages = [
                    candidate
                    for candidate in messages
                    if (
                        candidate.sender == "operator"
                        and candidate.message_type == "text"
                        and candidate.status == "queued"
                        and candidate.conversation_id == conversation.id
                        and _chat_batch_key(candidate) == batch_key
                    )
                ]
                if not batch_messages:
                    continue
                _prepare_chat_context_rotation(
                    state,
                    agent=agent,
                    runtime=runtime,
                    now=effective_now,
                )
                dispatch_id = message.dispatch_id or f"team-chat:{message.id}"
                lease_expires_at = effective_now + timedelta(seconds=max(1, int(lease_seconds)))
                existing_dispatch = state.dispatches.get(dispatch_id)
                if existing_dispatch is None:
                    chat_scope = _operator_chat_dispatch_scope(
                        state,
                        agent=agent,
                        message=message,
                    )
                    state.dispatches[dispatch_id] = TeamDispatch(
                        id=dispatch_id,
                        agent_id=agent.id,
                        session_id=agent.session_id,
                        cause_kind="team_chat",
                        cause_id=message.id,
                        chat_message_ids=[candidate.id for candidate in batch_messages],
                        **chat_scope,
                        status=(
                            TeamDispatchStatus.PREPARING
                            if defer_activation
                            else TeamDispatchStatus.LEASED
                        ),
                        created_at=effective_now,
                        preparation_expires_at=(
                            effective_now
                            + timedelta(seconds=TEAM_DISPATCH_PREPARATION_TIMEOUT_SECONDS)
                            if defer_activation
                            else None
                        ),
                        requested_lease_seconds=max(1, int(lease_seconds)),
                        role_description_fingerprint=agent_role_description_fingerprint(
                            _agent_payload(state, agent)
                        ),
                        prepared_at=(None if defer_activation else effective_now),
                        leased_at=(None if defer_activation else effective_now),
                        lease_expires_at=(None if defer_activation else lease_expires_at),
                    )
                elif (
                    existing_dispatch.status
                    != (
                        TeamDispatchStatus.PREPARING
                        if defer_activation
                        else TeamDispatchStatus.LEASED
                    )
                    or existing_dispatch.agent_id != agent.id
                    or existing_dispatch.session_id != agent.session_id
                ):
                    continue
                runtime.active_dispatch_id = dispatch_id
                runtime.wake_leased_until = None if defer_activation else lease_expires_at
                runtime.wake_reason = "team_chat"
                runtime.last_wake_at = effective_now
                runtime.next_wake_at = None
                runtime.updated_at = effective_now
                for candidate in batch_messages:
                    candidate.status = "dispatching" if defer_activation else "running"
                    candidate.dispatch_id = dispatch_id
                    candidate.error = None
                    candidate.updated_at = effective_now
                    for ingress in state.external_chat_ingress.values():
                        if ingress.message_id == candidate.id:
                            ingress.dispatch_id = dispatch_id
                            ingress.updated_at = effective_now
                conversation.updated_at = effective_now
                claims.append(
                    {
                        "agent_id": agent.id,
                        "session_id": agent.session_id,
                        "conversation_id": conversation.id,
                        "message_id": message.id,
                        "message_ids": [candidate.id for candidate in batch_messages],
                        "dispatch_id": dispatch_id,
                    }
                )
            return state

        state = self._repository.update(_mutate)
        return state, claims

    def activate_chat_message_dispatch(
        self,
        *,
        dispatch_id: str,
        lease_seconds: int | None = None,
        now: datetime | None = None,
    ) -> TeamState:
        normalized_dispatch_id = _required_text(dispatch_id, "dispatch_id")
        effective_now = _ensure_utc(now or utc_now())

        def _mutate(state: TeamState) -> TeamState:
            dispatch = _require_present(state.dispatches, normalized_dispatch_id, "dispatch")
            if dispatch.cause_kind != "team_chat":
                raise ValueError(f"dispatch {dispatch.id} is not a Team Chat dispatch")
            if dispatch.status == TeamDispatchStatus.LEASED:
                return state
            if dispatch.status != TeamDispatchStatus.PREPARING:
                raise ValueError(
                    f"chat dispatch {dispatch.id} is {dispatch.status.value}, not preparing"
                )
            runtime = _require_present(state.agent_runtimes, dispatch.agent_id, "agent runtime")
            if runtime.active_dispatch_id != dispatch.id:
                raise ValueError(
                    f"chat dispatch {dispatch.id} no longer owns the agent reservation"
                )
            normalized_lease_seconds = max(
                1,
                int(dispatch.requested_lease_seconds if lease_seconds is None else lease_seconds),
            )
            lease_expires_at = effective_now + timedelta(seconds=normalized_lease_seconds)
            dispatch.status = TeamDispatchStatus.LEASED
            dispatch.preparation_expires_at = None
            dispatch.prepared_at = effective_now
            dispatch.leased_at = effective_now
            dispatch.lease_expires_at = lease_expires_at
            runtime.wake_leased_until = lease_expires_at
            runtime.updated_at = effective_now
            for message_id in dispatch.chat_message_ids:
                message = state.conversation_messages.get(message_id)
                if message is not None and message.status == "dispatching":
                    message.status = "running"
                    message.updated_at = effective_now
            return state

        return self._repository.update(_mutate)

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
            dispatch = (
                state.dispatches.get(message.dispatch_id)
                if message.dispatch_id is not None
                else None
            )
            message_ids = (
                list(dispatch.chat_message_ids)
                if dispatch is not None and dispatch.chat_message_ids
                else [message.id]
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
            if dispatch is not None and dispatch.status in {
                TeamDispatchStatus.PREPARING,
                TeamDispatchStatus.LEASED,
            }:
                agent = state.agents.get(conversation.agent_id)
                if agent is not None:
                    _requeue_unacknowledged_in_dispatch_messages(
                        state,
                        agent=agent,
                        completed_dispatch_id=dispatch.id,
                    )
                dispatch.status = TeamDispatchStatus.FAILED
                dispatch.completed_at = now
                dispatch.error = normalized_error
            for candidate_id in message_ids:
                candidate = state.conversation_messages.get(candidate_id)
                if candidate is None or candidate.status == "replied":
                    continue
                candidate.status = "failed"
                candidate.error = normalized_error
                candidate.updated_at = now
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

    def mark_chat_message_ordinary_context_seeded(
        self,
        *,
        message_id: str,
    ) -> TeamState:
        normalized_message_id = _required_text(message_id, "message_id")

        def _mutate(state: TeamState) -> TeamState:
            message = _require_present(
                state.conversation_messages,
                normalized_message_id,
                "conversation message",
            )
            message.metadata["ordinary_context_seeded"] = True
            message.updated_at = utc_now()
            return state

        return self._repository.update(_mutate)

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
