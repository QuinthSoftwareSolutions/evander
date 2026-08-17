"""Communication tool registration helpers for the MCP gateway."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field as PydanticField
from pydantic import ValidationError

from evander.email.http_utils import ProviderRequestError
from evander.email.providers import EmailDraftAttachment, EmailMessageSearchQuery
from evander.gateway.dependencies import _compat_gateway_attr
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import (
    CommunicationAttachmentRef,
    CommunicationCopyArtifactToWorkspaceResponse,
    CommunicationDownloadAttachmentResponse,
    CommunicationListAttachmentsResponse,
    CommunicationCopyArtifactToWorkspaceRequest,
    CommunicationDownloadAttachmentRequest,
    CommunicationListAttachmentsRequest,
    CommunicationSendMessageWithAttachmentsRequest,
    CommunicationStageWorkspaceFileRequest,
    DownloadInlineMediaRequest,
    GetPhoneCallResultRequest,
    HasPendingDiscordMessagesRequest,
    ListContactsRequest,
    ListInlineMediaRequest,
    ListUnreadDiscordMessagesRequest,
    MakePhoneCallRequest,
    StageArtifactRequest,
    CommunicationSendMessageWithAttachmentsResponse,
    CommunicationStageWorkspaceFileResponse,
    ContactSummary,
    DownloadInlineMediaResponse,
    ListContactsResponse,
    ListInlineMediaResponse,
    MakePhoneCallResponse,
    StageArtifactResponse,
    SubmitVoiceQueryResponseRequest,
    SubmitVoiceQueryResponseResponse,
    ToolName,
)
from evander.inline_media import normalize_inline_media_provider
from evander.policy import PolicyError
from evander.retell_phone import sanitize_retell_metadata
from evander.services import (
    normalize_teams_mention_refs,
    parse_teams_target,
    resolve_teams_send_target,
)
from evander.teams_meeting import voice_session_transcript_from_teams_events
from evander.voice_sessions import disable_pending_voice_query_wake
from evander.communication_artifacts import normalize_safe_filename
from evander.effects.providers.artifacts import ArtifactStageEffectProvider
from evander.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.schemas import (
    AgentCopyFilesEffectDefinition,
    ArtifactStageEffectDefinition,
    VmCopyFilesItemDefinition,
)

TeamsMentionRefsArgument = Annotated[
    list[str] | None,
    PydanticField(
        description=(
            "Optional Teams user refs to mention in a channel post, chat post, or channel thread reply. "
            "Use user:<graph-user-id-or-upn> or a bare Graph user ID, UPN, or mail address. "
            "Plain @name text in message does not notify users."
        )
    ),
]


@dataclass(frozen=True)
class CommunicationToolHelpers:
    audit_success: Callable[..., None]
    audit_failure: Callable[..., None]
    artifact_payloads: Callable[..., Any]
    artifact_audit_summaries: Callable[..., Any]
    communication_target_audit_fields: Callable[..., Any]
    record_email_draft_snapshot: Callable[..., Any]
    send_email_draft: Callable[..., Any]
    audit_inline_media_operation: Callable[..., Any]
    resolve_inline_media: Callable[..., Any]
    download_inline_media_bytes: Callable[..., Any]
    provider_download_attachment: Callable[..., Any]
    stage_inline_media_artifact: Callable[..., Any]
    inline_media_ref_audit_payload: Callable[..., Any]
    normalize_inline_media_source_kind: Callable[..., Any]
    resolve_communication_attachments: Callable[..., Any]
    stage_communication_artifact: Callable[..., Any]
    admin_service: Callable[..., Any]
    artifact_to_model: Callable[..., Any]
    communication_artifact_store: Callable[..., Any]
    safe_artifact_id: Callable[..., Any]
    normalize_workspace_source_path: Callable[..., Any]
    read_agent_workspace_file: Callable[..., Any]
    teams_artifacts_with_public_download_links: Callable[..., Any]
    draft_recipients_from_payload: Callable[..., Any]
    filter_discord_commands_for_session: Callable[..., Any]
    filter_teams_messages_for_session: Callable[..., Any]
    teams_cursor_for_target: Callable[..., Any]
    transport_session_map: dict[str, str] | None = None


class _EmailListCursorCodec:
    _ttl_seconds = 15 * 60

    def __init__(self) -> None:
        self._secret = secrets.token_bytes(32)

    @staticmethod
    def _query_digest(query: EmailMessageSearchQuery) -> str:
        serialized = json.dumps(
            query.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def issue(
        self,
        *,
        session_id: str,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
        provider_cursor: str,
    ) -> str:
        payload = {
            "version": 1,
            "expires_at": int(time.time()) + self._ttl_seconds,
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "folder": folder,
            "query_digest": self._query_digest(query),
            "provider_cursor": provider_cursor,
        }
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        signature = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def resolve(
        self,
        cursor: str,
        *,
        session_id: str,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
    ) -> str:
        try:
            encoded, signature = cursor.split(".", 1)
            expected_signature = hmac.new(
                self._secret, encoded.encode("ascii"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected_signature):
                raise ValueError("invalid email page cursor")
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid email page cursor") from exc
        if (
            payload.get("version") != 1
            or not isinstance(payload.get("provider_cursor"), str)
            or int(payload.get("expires_at", 0)) < int(time.time())
            or payload.get("session_id") != session_id
            or payload.get("mailbox_id") != mailbox_id
            or payload.get("folder") != folder
            or payload.get("query_digest") != self._query_digest(query)
        ):
            raise ValueError("invalid or expired email page cursor")
        return str(payload["provider_cursor"])


def register_communication_tools(
    server: Any,
    context: Any,
    deps: Any,
    helpers: CommunicationToolHelpers | None = None,
    **legacy_helpers: Any,
) -> None:
    """Register communication tools with the MCP gateway server."""

    if helpers is None:
        helpers = CommunicationToolHelpers(**legacy_helpers)

    audit_success = helpers.audit_success
    audit_failure = helpers.audit_failure
    email_list_cursor_codec = _EmailListCursorCodec()
    transport_session_map = helpers.transport_session_map
    if transport_session_map is None:
        transport_session_map = getattr(server, "_evander_transport_session_map", {})
    _artifact_payloads = helpers.artifact_payloads
    _artifact_audit_summaries = helpers.artifact_audit_summaries
    _communication_target_audit_fields = helpers.communication_target_audit_fields
    _record_email_draft_snapshot = helpers.record_email_draft_snapshot
    _send_email_draft = helpers.send_email_draft
    _audit_inline_media_operation = helpers.audit_inline_media_operation
    _resolve_inline_media = helpers.resolve_inline_media
    _download_inline_media_bytes = helpers.download_inline_media_bytes
    _provider_download_attachment = helpers.provider_download_attachment
    _stage_inline_media_artifact = helpers.stage_inline_media_artifact
    _inline_media_ref_audit_payload = helpers.inline_media_ref_audit_payload
    _normalize_inline_media_source_kind = helpers.normalize_inline_media_source_kind
    _resolve_communication_attachments = helpers.resolve_communication_attachments
    _stage_communication_artifact = helpers.stage_communication_artifact
    _admin_service = helpers.admin_service
    _artifact_to_model = helpers.artifact_to_model
    _communication_artifact_store = helpers.communication_artifact_store
    _safe_artifact_id = helpers.safe_artifact_id
    _normalize_workspace_source_path = helpers.normalize_workspace_source_path
    _read_agent_workspace_file = helpers.read_agent_workspace_file
    _teams_artifacts_with_public_download_links = helpers.teams_artifacts_with_public_download_links
    draft_recipients_from_payload = helpers.draft_recipients_from_payload
    _filter_discord_commands_for_session = helpers.filter_discord_commands_for_session
    _filter_teams_messages_for_session = helpers.filter_teams_messages_for_session
    _teams_cursor_for_target = helpers.teams_cursor_for_target

    @server.tool()
    def discord_get_commands(session_id: str, since_cursor: str | None = None) -> dict[str, Any]:
        args = {"session_id": session_id, "since_cursor": since_cursor}
        try:
            session = context.policy.require_tool(session_id, ToolName.DISCORD_GET_COMMANDS)
            raw_commands = context.discord_service_for_session(session_id).get_commands(
                since_cursor
            )
            commands, denied_count, next_cursor = _filter_discord_commands_for_session(
                context,
                session_id,
                session,
                raw_commands,
                args,
                audit_failure,
            )
            result = {"commands": commands, "next_cursor": next_cursor}
            audit_success(
                session_id,
                ToolName.DISCORD_GET_COMMANDS,
                args,
                {"count": len(commands), "denied_count": denied_count, "next_cursor": next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DISCORD_GET_COMMANDS, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_GET_COMMANDS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_GET_COMMANDS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def discord_post_status(session_id: str, channel_id: str, message: str) -> dict[str, Any]:
        args = {"session_id": session_id, "channel_id": channel_id, "message": message}
        try:
            session = context.policy.require_tool(session_id, ToolName.DISCORD_POST_STATUS)
            resolved_channel_id = context.policy.require_discord_channel(
                session,
                channel_id,
                capability="write",
            )
            use_resolved_identity = (
                context.policy.discord_sender_identity_mode(
                    session,
                    channel_id,
                    capability="write",
                )
                == "resolved"
            )
            service = context.discord_service_for_session(session_id)
            if use_resolved_identity and hasattr(service, "post_status_with_identity"):
                identity_resolution = context.resolve_outbound_identity(
                    session_id=session_id,
                    provider="discord",
                    destination=resolved_channel_id,
                )
                webhook_channel_id = context.policy.discord_webhook_channel_id(
                    resolved_channel_id
                )
                identity_kwargs: dict[str, Any] = {
                    "identity": identity_resolution.identity,
                    "identity_status": identity_resolution.status,
                }
                if webhook_channel_id != resolved_channel_id:
                    identity_kwargs.update(
                        {
                            "webhook_channel_id": webhook_channel_id,
                            "thread_id": resolved_channel_id,
                        }
                    )
                result = service.post_status_with_identity(
                    message,
                    resolved_channel_id,
                    session_id,
                    **identity_kwargs,
                )
            else:
                result = service.post_status(message, resolved_channel_id, session_id)
            audit_success(session_id, ToolName.DISCORD_POST_STATUS, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DISCORD_POST_STATUS, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_POST_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_POST_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_get_messages(
        session_id: str,
        channel_target: str,
        since_cursor: str | None = None,
        before_cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "since_cursor": since_cursor,
            "before_cursor": before_cursor,
            "limit": limit,
            "channel_target": channel_target,
        }
        try:
            if limit is not None and limit < 1:
                raise ToolError("limit must be >= 1 when provided")
            if since_cursor is not None and before_cursor is not None:
                raise ToolError("since_cursor and before_cursor are mutually exclusive")
            session = context.policy.require_tool(session_id, ToolName.TEAMS_GET_MESSAGES)
            normalized_target = context.policy.require_teams_channel_target(
                session,
                str(channel_target or "").strip(),
            )
            effective_since_cursor = _teams_cursor_for_target(
                since_cursor,
                channel_target=normalized_target,
                field_name="since_cursor",
            )
            effective_before_cursor = _teams_cursor_for_target(
                before_cursor,
                channel_target=normalized_target,
                field_name="before_cursor",
            )
            effective_limit = limit
            if (
                effective_since_cursor is None
                and effective_before_cursor is None
                and effective_limit is None
            ):
                effective_limit = context.config.teams_poll_limit
            raw_messages = context.teams_service_for_session(session_id).get_messages(
                since_cursor=effective_since_cursor,
                before_cursor=effective_before_cursor,
                limit=None,
                channel_target=normalized_target,
            )
            messages, denied_count, next_cursor = _filter_teams_messages_for_session(
                context,
                session_id,
                session,
                raw_messages,
                args,
                audit_failure,
            )
            if effective_limit is not None:
                messages = messages[-effective_limit:]
                fallback_cursor = (
                    effective_before_cursor
                    if effective_before_cursor is not None
                    else effective_since_cursor
                )
                next_cursor = messages[-1]["cursor"] if messages else fallback_cursor
            result = {"messages": messages, "next_cursor": next_cursor}
            audit_success(
                session_id,
                ToolName.TEAMS_GET_MESSAGES,
                args,
                {"count": len(messages), "denied_count": denied_count, "next_cursor": next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_GET_MESSAGES, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_GET_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise

    @server.tool()
    def has_pending_discord_messages(session_id: str) -> dict[str, Any]:
        args = HasPendingDiscordMessagesRequest(session_id=session_id).model_dump(mode="json")
        try:
            context.policy.require_tool(session_id, ToolName.HAS_PENDING_DISCORD_MESSAGES)
            session = context.policy.get_session(session_id)
            streams = [
                {"channel_id": channel_id}
                for channel_id in context.policy_config.resolved_discord_channel_ids_for_session(
                    session,
                    capability="read",
                )
            ]
            result = (
                context.discord_inbox_service_for_session(session_id)
                .has_pending_discord_messages(
                    streams=streams,
                    session_id=session_id,
                )
                .model_dump(mode="json")
            )
            audit_success(
                session_id,
                ToolName.HAS_PENDING_DISCORD_MESSAGES,
                args,
                {"pending": result["pending"]},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.HAS_PENDING_DISCORD_MESSAGES, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.HAS_PENDING_DISCORD_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_unread_discord_messages(
        session_id: str, cursor: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        args = ListUnreadDiscordMessagesRequest(
            session_id=session_id, cursor=cursor, limit=limit
        ).model_dump(mode="json")
        try:
            context.policy.require_tool(session_id, ToolName.LIST_UNREAD_DISCORD_MESSAGES)
            session = context.policy.get_session(session_id)
            streams = [
                {"channel_id": channel_id}
                for channel_id in context.policy_config.resolved_discord_channel_ids_for_session(
                    session,
                    capability="read",
                )
            ]
            response = context.discord_inbox_service_for_session(
                session_id
            ).list_unread_discord_messages(
                streams=streams,
                session_id=session_id,
                cursor=cursor,
                limit=limit,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_UNREAD_DISCORD_MESSAGES,
                args,
                {"count": len(response.messages), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_UNREAD_DISCORD_MESSAGES, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_UNREAD_DISCORD_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_all_discord_messages(session_id: str, limit: int | None = None) -> dict[str, Any]:
        args = {"session_id": session_id, "limit": limit}
        try:
            context.policy.require_tool(session_id, ToolName.LIST_ALL_DISCORD_MESSAGES)
            session = context.policy.get_session(session_id)
            streams = [
                {"channel_id": channel_id}
                for channel_id in context.policy_config.resolved_discord_channel_ids_for_session(
                    session,
                    capability="read",
                )
            ]
            response = context.discord_inbox_service_for_session(
                session_id
            ).list_all_discord_messages(
                streams=streams,
                limit=limit,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_ALL_DISCORD_MESSAGES,
                args,
                {"count": len(response.messages), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_ALL_DISCORD_MESSAGES, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_ALL_DISCORD_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_GET_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def add_message_reaction(
        session_id: str,
        provider: str,
        message_id: str,
        emoji: str,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "provider": provider,
            "message_id": message_id,
            "emoji": emoji,
            "channel_id": channel_id,
            "channel_target": channel_target,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.ADD_MESSAGE_REACTION)
            normalized_provider = str(provider or "").strip().lower()
            normalized_message_id = str(message_id or "").strip()
            normalized_emoji = str(emoji or "").strip()
            if not normalized_message_id:
                raise ToolError("message_id is required")
            if not normalized_emoji:
                raise ToolError("emoji is required")
            if normalized_provider == "discord":
                normalized_channel_id = str(channel_id or "").strip()
                if not normalized_channel_id:
                    raise ToolError("channel_id is required for Discord reactions")
                normalized_channel_id = context.policy.require_discord_channel(
                    session,
                    normalized_channel_id,
                    capability="reply",
                )
                result = context.discord_service_for_session(session_id).add_reaction(
                    channel_id=normalized_channel_id,
                    message_id=normalized_message_id,
                    emoji=normalized_emoji,
                    session_id=session_id,
                )
            elif normalized_provider == "teams":
                normalized_target = context.policy.require_teams_channel_target(
                    session,
                    str(channel_target or "").strip(),
                    capability="reply",
                )
                result = context.teams_service_for_session(session_id).add_reaction(
                    channel_target=normalized_target,
                    message_id=normalized_message_id,
                    reaction=normalized_emoji,
                    session_id=session_id,
                )
            else:
                raise ToolError("provider must be either 'discord' or 'teams'")
            audit_success(session_id, ToolName.ADD_MESSAGE_REACTION, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.ADD_MESSAGE_REACTION, args, str(exc))
            raise
        except ValueError as exc:
            message = str(exc)
            audit_failure(
                session_id,
                ToolName.ADD_MESSAGE_REACTION,
                args,
                message,
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(message) from exc
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.ADD_MESSAGE_REACTION,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.ADD_MESSAGE_REACTION,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_contacts(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List directory contacts visible to this session without exposing addresses or numbers."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": resolved_session_id}
        try:
            request = ListContactsRequest(session_id=resolved_session_id)
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(
                resolved_session_id,
                ToolName.LIST_CONTACTS,
            )
            contacts_config = context.provider_config.resolve_effective_contacts_for_session_id(
                resolved_session_id
            )
            contacts = []
            for contact_id, contact in sorted(contacts_config.contacts.items()):
                primary_phone_labels = [
                    phone.label for phone in contact.phone_numbers if phone.primary
                ]
                contacts.append(
                    ContactSummary(
                        id=contact_id,
                        name=contact.name,
                        description=contact.description,
                        email_address_count=len(contact.email_addresses),
                        phone_number_count=len(contact.phone_numbers),
                        primary_phone_label=(
                            primary_phone_labels[0] if len(primary_phone_labels) == 1 else None
                        ),
                        callable=len(primary_phone_labels) == 1,
                    )
                )
            result = ListContactsResponse(
                directory_path=session.directory_path,
                contacts=contacts,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.LIST_CONTACTS,
                args,
                {
                    "directory_path": result["directory_path"],
                    "contact_count": len(result["contacts"]),
                    "callable_contact_count": sum(
                        1 for contact in result["contacts"] if contact.get("callable")
                    ),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.LIST_CONTACTS, args, str(exc))
            raise
        except ValueError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.LIST_CONTACTS,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.LIST_CONTACTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def make_phone_call(
        contact: str,
        prompt: str,
        session_id: str | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Start a host-side Retell phone call to a directory contact id."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        safe_metadata = sanitize_retell_metadata(metadata or {})
        args = {
            "session_id": resolved_session_id,
            "contact": contact,
            "prompt": prompt,
            "request_id": request_id,
            "metadata": safe_metadata,
        }
        try:
            request = MakePhoneCallRequest(
                session_id=resolved_session_id,
                contact=contact,
                prompt=prompt,
                request_id=request_id.strip() if isinstance(request_id, str) else None,
                metadata=safe_metadata,
            )
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
            )
            result = context.phone_call_service_for_session(resolved_session_id).make_phone_call(
                session=session,
                contact=request.contact,
                prompt=request.prompt,
                request_id=request.request_id,
                metadata=request.metadata,
            )
            result = MakePhoneCallResponse.model_validate(result).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
                args,
                {
                    "request_id": result["request_id"],
                    "call_id": result.get("call_id"),
                    "contact": result["contact"],
                    "status": result["status"],
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.MAKE_PHONE_CALL, args, str(exc))
            raise
        except (ValueError, ProviderRequestError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.MAKE_PHONE_CALL,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_phone_call_result(
        session_id: str | None = None,
        request_id: str | None = None,
        call_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read a Retell phone call result owned by this session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "request_id": request_id,
            "call_id": call_id,
        }
        try:
            request = GetPhoneCallResultRequest(
                session_id=resolved_session_id,
                request_id=request_id,
                call_id=call_id,
            )
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
            )
            result = context.phone_call_service_for_session(
                resolved_session_id
            ).get_phone_call_result(
                session=session,
                request_id=request.request_id,
                call_id=request.call_id,
            )
            audit_success(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
                args,
                {
                    "request_id": result.get("request_id"),
                    "call_id": result.get("call_id"),
                    "contact": result.get("contact"),
                    "status": result.get("status"),
                    "has_transcript": bool(result.get("transcript")),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.GET_PHONE_CALL_RESULT, args, str(exc))
            raise
        except (ValueError, ProviderRequestError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.GET_PHONE_CALL_RESULT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def submit_voice_query_response(
        query_id: str,
        answer: str,
        session_id: str | None = None,
        status: str = "answered",
        citations: list[str] | None = None,
        confidence: str | None = None,
        metadata: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Answer a delegated live voice query for this originating session."""
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        safe_metadata = sanitize_retell_metadata(metadata or {})
        args = {
            "session_id": resolved_session_id,
            "query_id": query_id,
            "answer": answer,
            "status": status,
            "citations": citations or [],
            "confidence": confidence,
            "metadata": safe_metadata,
        }
        try:
            request = SubmitVoiceQueryResponseRequest(
                session_id=resolved_session_id,
                query_id=query_id,
                answer=answer,
                status=status,
                citations=citations or [],
                confidence=confidence,
                metadata=safe_metadata,
            )
            args = request.model_dump(mode="json")
            context.policy.require_tool(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
            )
            query = context.voice_session_store.complete_query(
                request.query_id,
                origin_session_id=resolved_session_id,
                answer=request.answer,
                status="failed" if request.status == "failed" else "answered",
                citations=request.citations,
                confidence=request.confidence,
                metadata=request.metadata,
            )
            wake_request_disabled = disable_pending_voice_query_wake(
                query=query,
                wake_store=context.wake_store,
            )
            result = SubmitVoiceQueryResponseResponse(
                query_id=query.query_id,
                voice_session_id=query.voice_session_id,
                status=query.status,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
                args,
                {
                    "query_id": result["query_id"],
                    "voice_session_id": result["voice_session_id"],
                    "status": result["status"],
                    "wake_request_disabled": wake_request_disabled,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
                args,
                str(exc),
            )
            raise
        except ValueError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_post_status(
        session_id: str,
        channel_target: str,
        message: str,
        thread_id: str | None = None,
        mentions: TeamsMentionRefsArgument = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "channel_target": channel_target,
            "message": message,
            "thread_id": thread_id,
            "mentions": mentions,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.TEAMS_POST_STATUS)
            try:
                normalized_mentions = normalize_teams_mention_refs(mentions)
            except ValueError as exc:
                raise PolicyError(str(exc)) from exc
            normalized_target = context.policy.require_teams_send_target(
                session,
                channel_target,
                capability="reply" if thread_id is not None else "write",
            )
            parsed_target = parse_teams_target(normalized_target)
            if parsed_target.kind == "user" and thread_id is not None:
                raise PolicyError("Teams user targets do not support thread replies")
            service = context.teams_service_for_session(session_id)
            send_target = resolve_teams_send_target(service, normalized_target)
            result = service.post_status(
                message,
                send_target,
                session_id,
                thread_id=thread_id,
                mentions=normalized_mentions,
            )
            audit_success(session_id, ToolName.TEAMS_POST_STATUS, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_POST_STATUS, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_POST_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_POST_STATUS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_meeting_get_events(
        session_id: str,
        since_sequence: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "since_sequence": since_sequence,
            "limit": limit,
        }
        try:
            context.policy.require_tool(session_id, ToolName.TEAMS_MEETING_GET_EVENTS)
            if since_sequence is not None and since_sequence < 0:
                raise ToolError("since_sequence must be >= 0 when provided")
            if limit < 1 or limit > 200:
                raise ToolError("limit must be between 1 and 200")
            events = context.teams_meeting_store.list_events_for_session(
                session_id,
                since_sequence=since_sequence,
                limit=limit,
            )
            next_sequence = events[-1].sequence if events else since_sequence
            result = {
                "events": [event.model_dump(mode="json") for event in events],
                "next_sequence": next_sequence,
            }
            audit_success(
                session_id,
                ToolName.TEAMS_MEETING_GET_EVENTS,
                args,
                {"count": len(events), "next_sequence": next_sequence},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_MEETING_GET_EVENTS, args, str(exc))
            raise
        except (ToolError, ValueError) as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_GET_EVENTS,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_GET_EVENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_meeting_speak(
        session_id: str,
        text: str,
        voice: str | None = None,
        interrupt: bool = False,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "text": text,
            "voice": voice,
            "interrupt": interrupt,
        }
        meeting_id_for_error: str | None = None
        try:
            context.policy.require_tool(session_id, ToolName.TEAMS_MEETING_SPEAK)
            normalized_text = str(text or "").strip()
            if not normalized_text:
                raise ToolError("text is required")
            binding = context.teams_meeting_store.get_binding_for_session(session_id)
            if binding is None:
                raise ToolError(f"Unknown Teams meeting session: {session_id}")
            meeting_id_for_error = binding.meeting_id
            if binding.status != "active":
                raise ToolError(f"Teams meeting {binding.meeting_id} is not active")
            resolved_voice = (
                str(voice or "").strip()
                or binding.default_voice
                or context.config.teams_meeting_default_voice
            )
            bridge_result = context.teams_meeting_bridge_client.speak(
                meeting_id=binding.meeting_id,
                text=normalized_text,
                voice=resolved_voice,
                interrupt=bool(interrupt),
            )
            operation_id = (
                str(
                    bridge_result.get("operation_id")
                    or bridge_result.get("speech_operation_id")
                    or ""
                ).strip()
                or None
            )
            speech_status = str(bridge_result.get("status") or "queued").strip() or "queued"
            event = context.teams_meeting_store.append_local_event(
                meeting_id=binding.meeting_id,
                event_type="speech_status",
                text=normalized_text,
                speech_operation_id=operation_id,
                speech_status=speech_status,
                metadata={"bridge": bridge_result, "voice": resolved_voice},
            )
            result = {
                "status": speech_status,
                "meeting_id": binding.meeting_id,
                "speech_operation_id": operation_id,
                "event": event.model_dump(mode="json"),
                "bridge": bridge_result,
            }
            audit_success(
                session_id,
                ToolName.TEAMS_MEETING_SPEAK,
                args,
                {
                    "status": speech_status,
                    "meeting_id": binding.meeting_id,
                    "speech_operation_id": operation_id,
                    "event_sequence": event.sequence,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_MEETING_SPEAK, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            if meeting_id_for_error is not None:
                context.teams_meeting_store.append_local_event(
                    meeting_id=meeting_id_for_error,
                    event_type="error",
                    text=str(exc),
                    metadata={"source": "teams_meeting_speak"},
                )
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_SPEAK,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except (ToolError, RuntimeError, ValueError) as exc:
            if meeting_id_for_error is not None and not isinstance(exc, ToolError):
                context.teams_meeting_store.append_local_event(
                    meeting_id=meeting_id_for_error,
                    event_type="error",
                    text=str(exc),
                    metadata={"source": "teams_meeting_speak"},
                )
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_SPEAK,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_SPEAK,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_meeting_stop(session_id: str) -> dict[str, Any]:
        args = {"session_id": session_id}
        meeting_id_for_error: str | None = None
        try:
            context.policy.require_tool(session_id, ToolName.TEAMS_MEETING_STOP)
            binding = context.teams_meeting_store.get_binding_for_session(session_id)
            if binding is None:
                raise ToolError(f"Unknown Teams meeting session: {session_id}")
            meeting_id_for_error = binding.meeting_id
            bridge_result = context.teams_meeting_bridge_client.leave(meeting_id=binding.meeting_id)
            context.teams_meeting_store.close_binding(binding.meeting_id, status="stopped")
            event = context.teams_meeting_store.append_local_event(
                meeting_id=binding.meeting_id,
                event_type="meeting_status",
                speech_status="stopped",
                metadata={"bridge": bridge_result},
            )
            voice_session_id = str(binding.metadata.get("voice_session_id") or "").strip()
            if voice_session_id:
                meeting_events = context.teams_meeting_store.list_events_for_session(
                    binding.session_id,
                    since_sequence=0,
                    limit=10_000,
                )
                transcript = voice_session_transcript_from_teams_events(meeting_events)
                context.voice_session_store.complete_session(
                    voice_session_id,
                    status="ended",
                    transcript=transcript,
                    summary=(
                        "Teams meeting ended; review the transcript for decisions and follow-up."
                    ),
                )
            manifest = context.session_repository.get_session(session_id)
            if manifest is not None:
                metadata = dict(manifest.metadata or {})
                teams_metadata = dict(metadata.get("teams_meeting") or {})
                teams_metadata["status"] = "stopped"
                teams_metadata["stopped_at"] = (
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                )
                metadata["teams_meeting"] = teams_metadata
                context.session_repository.upsert_session(
                    manifest.model_copy(update={"metadata": metadata})
                )
            result = {
                "status": "stopped",
                "meeting_id": binding.meeting_id,
                "event": event.model_dump(mode="json"),
                "bridge": bridge_result,
            }
            audit_success(
                session_id,
                ToolName.TEAMS_MEETING_STOP,
                args,
                {"status": "stopped", "meeting_id": binding.meeting_id},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_MEETING_STOP, args, str(exc))
            raise
        except httpx.HTTPError as exc:
            if meeting_id_for_error is not None:
                context.teams_meeting_store.append_local_event(
                    meeting_id=meeting_id_for_error,
                    event_type="error",
                    text=str(exc),
                    metadata={"source": "teams_meeting_stop"},
                )
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_STOP,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except (ToolError, RuntimeError, ValueError) as exc:
            if meeting_id_for_error is not None and not isinstance(exc, ToolError):
                context.teams_meeting_store.append_local_event(
                    meeting_id=meeting_id_for_error,
                    event_type="error",
                    text=str(exc),
                    metadata={"source": "teams_meeting_stop"},
                )
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_STOP,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_MEETING_STOP,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_list_unread(
        session_id: str, mailbox_id: str, folder: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        args = {
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "folder": folder,
            "limit": limit,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_LIST_UNREAD)
            context.policy.require_email_mailbox(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, folder)
            email_service = context.email_service_for_session(session_id)
            messages = [
                message.model_dump(mode="json")
                for message in email_service.list_unread(
                    mailbox_id=mailbox_id, folder=folder, limit=limit
                )
            ]
            audit_success(session_id, ToolName.EMAIL_LIST_UNREAD, args, {"count": len(messages)})
            return messages
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_LIST_UNREAD, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_LIST_UNREAD,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_LIST_UNREAD,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_list_messages(
        session_id: str,
        mailbox_id: str,
        folder: str,
        cursor: str | None = None,
        limit: Annotated[int, PydanticField(ge=1, le=100)] = 25,
        received_after: datetime | None = None,
        received_before: datetime | None = None,
        unread: bool | None = None,
        sender_contains: str | None = None,
        recipient_contains: str | None = None,
        subject_contains: str | None = None,
        text_contains: str | None = None,
        has_attachments: bool | None = None,
    ) -> dict[str, Any]:
        try:
            query = EmailMessageSearchQuery(
                received_after=received_after,
                received_before=received_before,
                unread=unread,
                sender_contains=sender_contains,
                recipient_contains=recipient_contains,
                subject_contains=subject_contains,
                text_contains=text_contains,
                has_attachments=has_attachments,
            )
            args = {
                "session_id": session_id,
                "mailbox_id": mailbox_id,
                "folder": folder,
                "cursor_present": cursor is not None,
                "limit": limit,
                **query.model_dump(mode="json", exclude_none=True),
            }
            session = context.policy.require_tool(session_id, ToolName.EMAIL_LIST_MESSAGES)
            context.policy.require_email_mailbox(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, folder)
            provider_cursor = (
                email_list_cursor_codec.resolve(
                    cursor,
                    session_id=session_id,
                    mailbox_id=mailbox_id,
                    folder=folder,
                    query=query,
                )
                if cursor is not None
                else None
            )
            page = context.email_service_for_session(session_id).list_messages(
                mailbox_id=mailbox_id,
                folder=folder,
                query=query,
                cursor=provider_cursor,
                limit=limit,
            )
            next_cursor = (
                email_list_cursor_codec.issue(
                    session_id=session_id,
                    mailbox_id=mailbox_id,
                    folder=folder,
                    query=query,
                    provider_cursor=page.next_cursor,
                )
                if page.next_cursor is not None
                else None
            )
            result = {
                "messages": [message.model_dump(mode="json") for message in page.messages],
                "next_cursor": next_cursor,
            }
            audit_success(
                session_id,
                ToolName.EMAIL_LIST_MESSAGES,
                args,
                {"count": len(page.messages), "has_next_cursor": next_cursor is not None},
            )
            return result
        except (ValueError, ValidationError) as exc:
            args = locals().get(
                "args", {"session_id": session_id, "mailbox_id": mailbox_id, "folder": folder}
            )
            audit_failure(
                session_id,
                ToolName.EMAIL_LIST_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except PolicyError as exc:
            args = locals().get(
                "args", {"session_id": session_id, "mailbox_id": mailbox_id, "folder": folder}
            )
            audit_failure(session_id, ToolName.EMAIL_LIST_MESSAGES, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            args = locals().get(
                "args", {"session_id": session_id, "mailbox_id": mailbox_id, "folder": folder}
            )
            audit_failure(
                session_id,
                ToolName.EMAIL_LIST_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            args = locals().get(
                "args", {"session_id": session_id, "mailbox_id": mailbox_id, "folder": folder}
            )
            audit_failure(
                session_id,
                ToolName.EMAIL_LIST_MESSAGES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_get_message(session_id: str, mailbox_id: str, message_id: str) -> dict[str, Any]:
        args = {"session_id": session_id, "mailbox_id": mailbox_id, "message_id": message_id}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_GET_MESSAGE)
            context.policy.require_email_mailbox(session, mailbox_id)
            message = context.email_service_for_session(session_id).get_message(
                mailbox_id=mailbox_id,
                message_id=message_id,
            )
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, message.folder)
            result = message.model_dump(mode="json")
            audit_success(session_id, ToolName.EMAIL_GET_MESSAGE, args, {"message_id": message_id})
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_GET_MESSAGE, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_GET_MESSAGE,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_GET_MESSAGE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_create_draft(
        session_id: str,
        body: str,
        mailbox_id: str,
        thread_or_message_id: str | None = None,
        subject: str | None = None,
        recipients: list[str] | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "thread_or_message_id": thread_or_message_id,
            "subject": subject,
            "recipients": recipients,
            "body": body,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_CREATE_DRAFT)
            context.policy.require_email_draft_enabled(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, "drafts")
            normalized_recipients = recipients or []
            context.policy.require_draft_recipients(session, normalized_recipients)
            draft = context.email_service_for_session(session_id).create_draft(
                mailbox_id, thread_or_message_id, subject, body, normalized_recipients
            )
            _record_email_draft_snapshot(
                session_id=session_id,
                draft=draft,
                body=body,
            )
            result = draft.model_dump(mode="json")
            audit_success(
                session_id, ToolName.EMAIL_CREATE_DRAFT, args, {"draft_id": draft.draft_id}
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_CREATE_DRAFT, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_inline_media(
        session_id: str,
        source_kind: str,
        provider: str,
        resource_id: str,
        comment_id: str | None = None,
        project: str | None = None,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = ListInlineMediaRequest(
            session_id=session_id,
            source_kind=source_kind,
            provider=provider,
            resource_id=resource_id,
            comment_id=comment_id,
            project=project,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            media = [ref for ref, _raw in _resolve_inline_media(request)]
            response = ListInlineMediaResponse(
                source_kind=_normalize_inline_media_source_kind(source_kind),
                provider=normalize_inline_media_provider(provider),
                resource_id=resource_id,
                media=media,
            )
            result = response.model_dump(mode="json")
            list_audit_result = {
                "source_kind": response.source_kind,
                "provider": response.provider,
                "resource_id": resource_id,
                "count": len(media),
                "comment_ids": sorted({item.comment_id for item in media if item.comment_id}),
                "mime_types": sorted({item.mime_type for item in media if item.mime_type}),
                "byte_count": sum(item.byte_count or 0 for item in media),
            }
            audit_success(
                session_id,
                ToolName.LIST_INLINE_MEDIA,
                args,
                list_audit_result,
            )
            _audit_inline_media_operation(
                event_type="inline_media_list",
                session_id=session_id,
                request=request,
                media=media,
                result=list_audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_INLINE_MEDIA, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.LIST_INLINE_MEDIA,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_INLINE_MEDIA,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def download_inline_media(
        session_id: str,
        source_kind: str,
        provider: str,
        resource_id: str,
        media_id: str,
        comment_id: str | None = None,
        project: str | None = None,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = DownloadInlineMediaRequest(
            session_id=session_id,
            source_kind=source_kind,
            provider=provider,
            resource_id=resource_id,
            media_id=media_id,
            comment_id=comment_id,
            project=project,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        audit_args = dict(args)
        try:
            list_request = ListInlineMediaRequest(
                session_id=session_id,
                source_kind=source_kind,
                provider=provider,
                resource_id=resource_id,
                comment_id=comment_id,
                project=project,
                mailbox_id=mailbox_id,
                channel_id=channel_id,
                channel_target=channel_target,
            )
            matches = _resolve_inline_media(list_request, tool_name=ToolName.DOWNLOAD_INLINE_MEDIA)
            selected = next((item for item in matches if item[0].media_id == media_id), None)
            if selected is None:
                raise ToolError(f"Unknown inline media_id for resource: {media_id}")
            ref, raw = selected
            audit_args.update(_inline_media_ref_audit_payload(ref))
            session = context.policy.get_session(session_id)
            context.policy.require_inline_media_receive_enabled(
                session, source_kind=ref.source_kind
            )
            if ref.source_kind == "communication" and ref.provider == "teams":
                content, filename, mime_type = _download_inline_media_bytes(request, ref, raw)
            elif ref.source_kind == "communication":
                attachment_request = CommunicationDownloadAttachmentRequest(
                    session_id=session_id,
                    provider=provider,
                    message_id=resource_id,
                    attachment_id=media_id,
                    mailbox_id=mailbox_id,
                    channel_id=channel_id,
                    channel_target=channel_target,
                )
                attachment_ref = CommunicationAttachmentRef(
                    provider=ref.provider,
                    message_id=ref.resource_id,
                    attachment_id=ref.media_id,
                    filename=ref.filename,
                    mime_type=ref.mime_type,
                    byte_count=ref.byte_count,
                    mailbox_id=mailbox_id,
                    channel_id=channel_id,
                    channel_target=channel_target,
                    metadata=ref.metadata,
                )
                content, filename, mime_type = _provider_download_attachment(
                    attachment_request, attachment_ref
                )
            else:
                content, filename, mime_type = _download_inline_media_bytes(request, ref, raw)
            audit_args.update(
                {
                    "filename": filename,
                    "mime_type": mime_type,
                    "byte_count": len(content),
                    "comment_id": ref.comment_id,
                }
            )
            context.policy.require_inline_media_content(
                session,
                source_kind=ref.source_kind,
                filename=filename,
                mime_type=mime_type,
                byte_count=len(content),
            )
            artifact = _stage_inline_media_artifact(
                request,
                ref,
                content,
                filename=filename,
                mime_type=mime_type,
            )
            response = DownloadInlineMediaResponse(artifact=artifact)
            result = response.model_dump(mode="json")
            download_audit_result = {
                "source_kind": ref.source_kind,
                "provider": ref.provider,
                "resource_id": ref.resource_id,
                "media_id": ref.media_id,
                "comment_id": ref.comment_id,
                "artifact_id": artifact.artifact_id,
                "byte_count": artifact.byte_count,
                "mime_type": artifact.mime_type,
            }
            audit_success(
                session_id,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                audit_args,
                download_audit_result,
            )
            _audit_inline_media_operation(
                event_type="inline_media_download",
                session_id=session_id,
                request=request,
                media=[
                    ref.model_copy(
                        update={
                            "filename": filename,
                            "mime_type": mime_type,
                            "byte_count": len(content),
                        }
                    )
                ],
                result=download_audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DOWNLOAD_INLINE_MEDIA, audit_args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                audit_args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except ValueError as exc:
            audit_failure(
                session_id,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                audit_args,
                str(exc),
                decision="error",
                error_class="invalid_inline_media",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                audit_args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def communication_list_attachments(
        session_id: str,
        provider: str,
        message_id: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationListAttachmentsRequest(
            session_id=session_id,
            provider=provider,
            message_id=message_id,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            attachments = _resolve_communication_attachments(request)
            response = CommunicationListAttachmentsResponse(
                provider=request.provider.strip().lower(),
                message_id=request.message_id,
                attachments=attachments,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider=response.provider,
                        mailbox_id=mailbox_id,
                        channel_id=channel_id,
                        channel_target=channel_target,
                    ),
                    "message_id": response.message_id,
                    "count": len(response.attachments),
                    "attachments": [
                        {
                            "attachment_id": item.attachment_id,
                            "filename": item.filename,
                            "mime_type": item.mime_type,
                            "byte_count": item.byte_count,
                            "size": item.byte_count,
                        }
                        for item in response.attachments
                    ],
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMMUNICATION_LIST_ATTACHMENTS, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def communication_download_attachment(
        session_id: str,
        provider: str,
        message_id: str,
        attachment_id: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationDownloadAttachmentRequest(
            session_id=session_id,
            provider=provider,
            message_id=message_id,
            attachment_id=attachment_id,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
        )
        args = request.model_dump(mode="json")
        try:
            list_request = CommunicationListAttachmentsRequest(
                session_id=session_id,
                provider=provider,
                message_id=message_id,
                mailbox_id=mailbox_id,
                channel_id=channel_id,
                channel_target=channel_target,
            )
            attachments = _resolve_communication_attachments(
                list_request,
                tool_name=ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
            )
            ref = next((item for item in attachments if item.attachment_id == attachment_id), None)
            if ref is None:
                raise ToolError(f"Unknown attachment_id for message: {attachment_id}")
            content, filename, mime_type = _provider_download_attachment(request, ref)
            args.update(
                {
                    "filename": filename,
                    "mime_type": mime_type,
                    "byte_count": len(content),
                    "size": len(content),
                }
            )
            context.policy.require_communication_file_content(
                context.policy.get_session(session_id),
                filename=filename,
                mime_type=mime_type,
                byte_count=len(content),
            )
            artifact = _stage_communication_artifact(
                request,
                ref,
                content,
                filename=filename,
                mime_type=mime_type,
            )
            response = CommunicationDownloadAttachmentResponse(artifact=artifact)
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                args,
                {
                    **_communication_target_audit_fields(
                        provider=artifact.provider,
                        mailbox_id=mailbox_id,
                        channel_id=channel_id,
                        channel_target=channel_target,
                    ),
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "artifact_id": artifact.artifact_id,
                    "byte_count": artifact.byte_count,
                    "size": artifact.byte_count,
                    "mime_type": artifact.mime_type,
                    "filename": artifact.filename,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def stage_artifact(
        session_id: str,
        path: str,
        destination_path: str | None = None,
    ) -> dict[str, Any]:
        """Copy a file or directory under /home/agent into the session /artifacts folder."""
        request = StageArtifactRequest(
            session_id=session_id,
            path=path,
            destination_path=destination_path,
        )
        args = request.model_dump(mode="json", exclude_none=True)
        try:
            session = context.policy.require_tool(session_id, ToolName.STAGE_ARTIFACT)
            effect = ArtifactStageEffectDefinition(
                id="stage-artifact",
                display_name="Stage artifact",
                type="effect:artifact:stage",
                session_id=session_id,
                path=request.path,
                destination_path=request.destination_path,
            )
            effect_context = EffectRuntimeContext(
                provider_config=context.resolve_provider_config_for_session(session_id).model_dump(
                    mode="json"
                ),
                admin_service=_admin_service(),
                metadata={"directory_path": session.directory_path or "root"},
            )
            provider_cls = _compat_gateway_attr(
                "ArtifactStageEffectProvider",
                ArtifactStageEffectProvider,
            )
            provider = provider_cls()
            provider.initialize(effect_definition=effect, runtime_context=effect_context)
            stage_result = provider.execute()
            response = StageArtifactResponse(
                success=bool(stage_result.get("success", True)),
                message=str(stage_result.get("message") or ""),
                session_id=str(stage_result.get("session_id") or session_id),
                vm_name=str(stage_result.get("vm_name") or "") or None,
                execution_backend=str(stage_result.get("execution_backend") or "") or None,
                source_path=str(stage_result.get("source_path") or ""),
                artifact_path=str(stage_result.get("artifact_path") or ""),
                artifacts_root=str(stage_result.get("artifacts_root") or ""),
                copied_count=int(stage_result.get("copied_count") or 0),
                byte_count=int(stage_result.get("byte_count") or 0),
                result=dict(stage_result),
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.STAGE_ARTIFACT,
                args,
                {
                    "decision": "allowed",
                    "source_path": response.source_path,
                    "artifact_path": response.artifact_path,
                    "artifacts_root": response.artifacts_root,
                    "copied_count": response.copied_count,
                    "byte_count": response.byte_count,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.STAGE_ARTIFACT, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.STAGE_ARTIFACT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def communication_copy_artifact_to_workspace(
        session_id: str,
        artifact_id: str,
        destination_filename: str | None = None,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        request = CommunicationCopyArtifactToWorkspaceRequest(
            session_id=session_id,
            artifact_id=artifact_id,
            destination_filename=destination_filename,
            overwrite=overwrite,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(
                session_id, ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE
            )
            context.policy.require_communication_file_receive_enabled(session)
            staged = _communication_artifact_store().get(_safe_artifact_id(artifact_id))
            artifact = _artifact_to_model(staged.metadata)
            if artifact.session_id != session_id:
                raise ToolError("artifact_id is not owned by this session")
            requested_name = (
                destination_filename or artifact.normalized_safe_filename or artifact.filename
            )
            safe_name = context.policy.require_safe_communication_filename(session, requested_name)
            # Provider filenames are never used as paths; the only destination is this
            # controlled workspace drop directory plus a policy-validated basename.
            safe_name = normalize_safe_filename(safe_name)
            destination_path = f"/home/agent/communication-artifacts/{safe_name}"

            effect = AgentCopyFilesEffectDefinition(
                id="communication-copy-artifact-to-workspace",
                display_name="Copy communication artifact to workspace",
                type="effect:agent:copy-files",
                session_id=session_id,
                files=[
                    VmCopyFilesItemDefinition(
                        source_path=str(staged.blob_path),
                        destination_path=destination_path,
                        overwrite=overwrite,
                    )
                ],
            )
            effect_context = EffectRuntimeContext(
                provider_config=context.resolve_provider_config_for_session(session_id).model_dump(
                    mode="json"
                ),
                admin_service=_admin_service(),
                metadata={"directory_path": session.directory_path or "root"},
            )
            provider_cls = _compat_gateway_attr(
                "AgentCopyFilesEffectProvider",
                AgentCopyFilesEffectProvider,
            )
            provider = provider_cls()
            provider.initialize(effect_definition=effect, runtime_context=effect_context)
            copy_result = provider.execute()
            response = CommunicationCopyArtifactToWorkspaceResponse(
                artifact=artifact,
                destination_path=destination_path,
                copied_count=int(copy_result.get("copied_count") or 0),
                copied_paths=list(copy_result.get("copied_paths") or []),
                result=copy_result,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
                args,
                {
                    "decision": "allowed",
                    "artifact_id": artifact.artifact_id,
                    "destination_path": destination_path,
                    "copied_count": response.copied_count,
                    "copied_paths": response.copied_paths,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(
                session_id, ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def communication_stage_workspace_file(
        session_id: str,
        source_path: str,
        filename: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Stage a regular file under /home/agent for outbound file-send tools."""
        request = CommunicationStageWorkspaceFileRequest(
            session_id=session_id,
            source_path=source_path,
            filename=filename,
            mime_type=mime_type,
        )
        args = request.model_dump(mode="json", exclude_none=True)
        try:
            session = context.policy.require_tool(
                session_id, ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE
            )
            context.policy.require_communication_file_send_enabled(session)
            normalized_source_path = _normalize_workspace_source_path(source_path)
            if filename is not None:
                context.policy.require_safe_communication_filename(session, filename)
            payload = _read_agent_workspace_file(
                session,
                source_path=normalized_source_path,
            )
            content_base64 = str(payload.get("content_base64") or "")
            if not content_base64:
                raise ToolError("Workspace file reader did not return file content")
            try:
                content = base64.b64decode(content_base64, validate=True)
            except Exception as exc:
                raise ToolError("Workspace file reader returned invalid base64 content") from exc
            resolved_source_path = str(payload.get("source_path") or normalized_source_path)
            resolved_filename = str(filename or payload.get("filename") or "attachment")
            resolved_mime_type = str(mime_type or payload.get("mime_type") or "").strip() or None
            context.policy.require_communication_file_content(
                session,
                filename=resolved_filename,
                mime_type=resolved_mime_type,
                byte_count=len(content),
            )
            record = _communication_artifact_store().stage(
                provider="workspace",
                session_id=session_id,
                content=content,
                original_filename=resolved_filename,
                mime_type=resolved_mime_type,
                metadata={
                    "source": "agent_workspace",
                    "source_path": resolved_source_path,
                    "requested_source_path": normalized_source_path,
                },
            )
            artifact = _artifact_to_model(record)
            response = CommunicationStageWorkspaceFileResponse(
                artifact=artifact,
                source_path=resolved_source_path,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE,
                args,
                {
                    "decision": "allowed",
                    "artifact_id": artifact.artifact_id,
                    "source_path": resolved_source_path,
                    "byte_count": artifact.byte_count,
                    "size": artifact.byte_count,
                    "mime_type": artifact.mime_type,
                    "filename": artifact.filename,
                    "sha256": artifact.sha256,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def discord_post_file(
        session_id: str,
        channel_id: str,
        message: str,
        artifact_ids: list[str],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationSendMessageWithAttachmentsRequest(
            session_id=session_id,
            provider="discord",
            channel_id=channel_id,
            message=message,
            artifact_ids=artifact_ids,
            request_id=request_id,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(session_id, ToolName.DISCORD_POST_FILE)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            resolved_channel_id = context.policy.require_communication_file_artifacts(
                session, artifacts, provider="discord", channel_id=channel_id
            )
            approval_payload = request.model_dump(
                mode="json", exclude={"request_id"}, exclude_none=True
            )
            approval = context.policy.require_communication_file_send_approval(
                session, request_id=request_id, payload=approval_payload
            )
            service = context.discord_service_for_session(session_id)
            if not hasattr(service, "post_file"):
                raise ToolError("Configured Discord adapter does not support file posts")
            use_resolved_identity = (
                context.policy.discord_sender_identity_mode(
                    session,
                    channel_id,
                    capability="write",
                )
                == "resolved"
            )
            if use_resolved_identity and hasattr(service, "post_file_with_identity"):
                identity_resolution = context.resolve_outbound_identity(
                    session_id=session_id,
                    provider="discord",
                    destination=resolved_channel_id,
                )
                result = service.post_file_with_identity(
                    message,
                    resolved_channel_id,
                    session_id,
                    artifacts,
                    identity=identity_resolution.identity,
                    identity_status=identity_resolution.status,
                )
            else:
                result = service.post_file(
                    message,
                    resolved_channel_id,
                    session_id,
                    artifacts,
                )
            response = CommunicationSendMessageWithAttachmentsResponse(
                provider="discord",
                status=str(result.get("status", "posted")),
                message_id=result.get("message_id"),
                artifact_ids=artifact_ids,
                result=result,
            )
            audit_success(
                session_id,
                ToolName.DISCORD_POST_FILE,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="discord",
                        channel_id=resolved_channel_id,
                        approval_request_id=request_id,
                        approval_decision=None if approval is None else approval.status.value,
                    ),
                    "message_id": response.message_id,
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                },
            )
            return response.model_dump(mode="json")
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DISCORD_POST_FILE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DISCORD_POST_FILE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def teams_post_file(
        session_id: str,
        channel_target: str,
        message: str,
        artifact_ids: list[str],
        thread_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = CommunicationSendMessageWithAttachmentsRequest(
            session_id=session_id,
            provider="teams",
            channel_target=channel_target,
            message=message,
            artifact_ids=artifact_ids,
            thread_id=thread_id,
            request_id=request_id,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(session_id, ToolName.TEAMS_POST_FILE)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            required_capability = "reply" if thread_id is not None else "write"
            context.policy.require_communication_file_artifacts(
                session,
                artifacts,
                provider="teams",
                channel_target=channel_target,
                allow_teams_user_target=True,
                capability=required_capability,
            )
            target = context.policy.require_teams_send_target(
                session,
                channel_target,
                capability=required_capability,
            )
            if parse_teams_target(target).kind == "user" and thread_id is not None:
                raise PolicyError("Teams user targets do not support thread replies")
            approval_payload = request.model_dump(
                mode="json", exclude={"request_id"}, exclude_none=True
            )
            approval = context.policy.require_communication_file_send_approval(
                session, request_id=request_id, payload=approval_payload
            )
            artifacts, published_links, published_link_audit = (
                _teams_artifacts_with_public_download_links(session_id, artifacts)
            )
            service = context.teams_service_for_session(session_id)
            if not hasattr(service, "post_file"):
                raise ToolError("Configured Teams adapter does not support file posts")
            send_target = resolve_teams_send_target(service, target)
            result = service.post_file(
                message, send_target, session_id, artifacts, thread_id=thread_id
            )
            if published_links:
                result["published_links"] = published_links
            response = CommunicationSendMessageWithAttachmentsResponse(
                provider="teams",
                status=str(result.get("status", "posted")),
                message_id=result.get("message_id"),
                artifact_ids=artifact_ids,
                published_links=published_links,
                result=result,
            )
            audit_success(
                session_id,
                ToolName.TEAMS_POST_FILE,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="teams",
                        channel_target=target,
                        approval_request_id=request_id,
                        approval_decision=None if approval is None else approval.status.value,
                    ),
                    "message_id": response.message_id,
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                    "published_links": published_link_audit,
                },
            )
            return response.model_dump(mode="json")
        except PolicyError as exc:
            audit_failure(session_id, ToolName.TEAMS_POST_FILE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.TEAMS_POST_FILE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_create_draft_with_attachments(
        session_id: str,
        body: str,
        mailbox_id: str,
        artifact_ids: list[str],
        thread_or_message_id: str | None = None,
        subject: str | None = None,
        recipients: list[str] | None = None,
    ) -> dict[str, Any]:
        request = CommunicationSendMessageWithAttachmentsRequest(
            session_id=session_id,
            provider="email",
            mailbox_id=mailbox_id,
            message=body,
            artifact_ids=artifact_ids,
            thread_id=thread_or_message_id,
            subject=subject,
            recipients=recipients or [],
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(
                session_id, ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS
            )
            context.policy.require_email_draft_enabled(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, "drafts")
            normalized_recipients = recipients or []
            context.policy.require_draft_recipients(session, normalized_recipients)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="email", mailbox_id=mailbox_id
            )
            service = context.email_service_for_session(session_id)
            if hasattr(service, "create_draft_with_attachments"):
                draft = service.create_draft_with_attachments(
                    mailbox_id,
                    thread_or_message_id,
                    subject,
                    body,
                    normalized_recipients,
                    artifacts,
                )
            else:
                draft_attachments = [
                    EmailDraftAttachment(
                        filename=str(artifact["filename"]),
                        content=Path(str(artifact["path"])).read_bytes(),
                        mime_type=artifact.get("mime_type"),
                    )
                    for artifact in artifacts
                ]
                draft = service.create_draft(
                    mailbox_id,
                    thread_or_message_id,
                    subject,
                    body,
                    normalized_recipients,
                    attachments=draft_attachments,
                )
            _record_email_draft_snapshot(
                session_id=session_id,
                draft=draft,
                body=body,
                artifact_ids=artifact_ids,
            )
            result = draft.model_dump(mode="json") if hasattr(draft, "model_dump") else dict(draft)
            result["artifact_ids"] = artifact_ids
            audit_success(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=mailbox_id,
                    ),
                    "draft_id": result.get("draft_id"),
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_send(
        session_id: str,
        mailbox_id: str,
        draft_id: str,
        recipients: list[str],
        subject: str | None = None,
        approval_request_id: str | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "draft_id": draft_id,
            "recipients": recipients,
            "subject": subject,
            "approval_request_id": approval_request_id,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_SEND)
            result = _send_email_draft(
                session_id=session_id,
                session=session,
                mailbox_id=mailbox_id,
                draft_id=draft_id,
                recipients=recipients,
                subject=subject,
            )
            audit_success(
                session_id,
                ToolName.EMAIL_SEND,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=mailbox_id,
                        approval_request_id=approval_request_id,
                    ),
                    **result,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_send_with_attachments(
        session_id: str,
        mailbox_id: str,
        draft_id: str,
        recipients: list[str],
        artifact_ids: list[str],
        subject: str | None = None,
        request_id: str | None = None,
        approval_request_id: str | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "draft_id": draft_id,
            "recipients": recipients,
            "artifact_ids": artifact_ids,
            "subject": subject,
            "request_id": request_id,
            "approval_request_id": approval_request_id,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_SEND_WITH_ATTACHMENTS)
            context.policy.require_email_send_enabled(session, mailbox_id)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="email", mailbox_id=mailbox_id
            )
            approval_payload: dict[str, Any] = {
                "session_id": session_id,
                "provider": "email",
                "mailbox_id": mailbox_id,
                "draft_id": draft_id,
                "recipients": recipients,
                "artifact_ids": artifact_ids,
            }
            if subject is not None:
                approval_payload["subject"] = subject
            approval = context.policy.require_communication_file_send_approval(
                session,
                request_id=request_id,
                payload=approval_payload,
            )
            result = _send_email_draft(
                session_id=session_id,
                session=session,
                mailbox_id=mailbox_id,
                draft_id=draft_id,
                recipients=recipients,
                subject=subject,
                artifact_ids=artifact_ids,
            )
            audit_success(
                session_id,
                ToolName.EMAIL_SEND_WITH_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=mailbox_id,
                        approval_request_id=request_id,
                        approval_decision=None if approval is None else approval.status.value,
                    ),
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                    **result,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND_WITH_ATTACHMENTS, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_send_approved_with_attachments(
        session_id: str,
        request_id: str,
        payload: dict[str, Any],
        artifact_ids: list[str],
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "request_id": request_id,
            "payload": payload,
            "artifact_ids": artifact_ids,
        }
        try:
            session = context.policy.require_tool(
                session_id, ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS
            )
            context.policy.require_email_send_enabled(session, str(payload.get("mailbox_id", "")))
            recipients = draft_recipients_from_payload(payload)
            context.policy.require_draft_recipients(session, recipients)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="email", mailbox_id=str(payload.get("mailbox_id", ""))
            )
            file_approval_payload = {**payload, "artifact_ids": artifact_ids}
            file_approval = context.policy.require_communication_file_send_approval(
                session, request_id=request_id, payload=file_approval_payload
            )
            approval = file_approval or context.policy.require_approved(request_id, payload)
            if approval.session_id != session_id:
                raise PolicyError(
                    f"Approval request {request_id} does not belong to session {session_id}"
                )
            mailbox_id = str(payload.get("mailbox_id", ""))
            draft_id = str(payload.get("draft_id") or payload.get("provider_draft_id") or "")
            if not recipients:
                snapshot = context.drafts.get(draft_id)
                if snapshot is not None and snapshot.mailbox_id == mailbox_id:
                    recipients = list(snapshot.recipients)
            subject = payload.get("subject") if isinstance(payload.get("subject"), str) else None
            result = _send_email_draft(
                session_id=session_id,
                session=session,
                mailbox_id=mailbox_id,
                draft_id=draft_id,
                recipients=recipients,
                subject=subject,
                artifact_ids=artifact_ids,
            )
            audit_success(
                session_id,
                ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=str(payload.get("mailbox_id", "")),
                        approval_request_id=request_id,
                        approval_decision=(approval.status.value),
                    ),
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                    **result,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def email_send_approved(
        session_id: str, request_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        args = {"session_id": session_id, "request_id": request_id, "payload": payload}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_SEND_APPROVED)
            context.policy.require_email_send_enabled(session, str(payload.get("mailbox_id", "")))
            recipients = draft_recipients_from_payload(payload)
            context.policy.require_draft_recipients(session, recipients)
            approval = context.policy.require_approved(request_id, payload)
            if approval.session_id != session_id:
                raise PolicyError(
                    f"Approval request {request_id} does not belong to session {session_id}"
                )
            mailbox_id = str(payload.get("mailbox_id", ""))
            draft_id = str(payload.get("draft_id") or payload.get("provider_draft_id") or "")
            if not recipients:
                snapshot = context.drafts.get(draft_id)
                if snapshot is not None and snapshot.mailbox_id == mailbox_id:
                    recipients = list(snapshot.recipients)
            subject = payload.get("subject") if isinstance(payload.get("subject"), str) else None
            result = _send_email_draft(
                session_id=session_id,
                session=session,
                mailbox_id=mailbox_id,
                draft_id=draft_id,
                recipients=recipients,
                subject=subject,
            )
            audit_success(session_id, ToolName.EMAIL_SEND_APPROVED, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND_APPROVED, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_APPROVED,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_APPROVED,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise
