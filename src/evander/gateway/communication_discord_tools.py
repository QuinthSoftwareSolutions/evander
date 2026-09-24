"""MCP tools for discord."""

from __future__ import annotations
from collections.abc import Callable
from typing import Any
import httpx
from mcp.server.fastmcp.exceptions import ToolError
from evander.models import (
    CommunicationSendMessageWithAttachmentsRequest,
    HasPendingDiscordMessagesRequest,
    ListUnreadDiscordMessagesRequest,
    CommunicationSendMessageWithAttachmentsResponse,
    ToolName,
)
from evander.policy import PolicyError

from .communication_support import CommunicationToolHelpers


def create_discord_tools(
    context: Any, helpers: CommunicationToolHelpers
) -> dict[str, Callable[..., Any]]:
    _artifact_audit_summaries = helpers.artifact_audit_summaries
    _artifact_payloads = helpers.artifact_payloads
    _communication_target_audit_fields = helpers.communication_target_audit_fields
    _filter_discord_commands_for_session = helpers.filter_discord_commands_for_session
    audit_failure = helpers.audit_failure
    audit_success = helpers.audit_success

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
                webhook_channel_id = context.policy.discord_webhook_channel_id(resolved_channel_id)
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
                webhook_channel_id = context.policy.discord_webhook_channel_id(resolved_channel_id)
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
                result = service.post_file_with_identity(
                    message,
                    resolved_channel_id,
                    session_id,
                    artifacts,
                    **identity_kwargs,
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

    return {
        "discord_get_commands": discord_get_commands,
        "discord_post_status": discord_post_status,
        "has_pending_discord_messages": has_pending_discord_messages,
        "list_unread_discord_messages": list_unread_discord_messages,
        "list_all_discord_messages": list_all_discord_messages,
        "add_message_reaction": add_message_reaction,
        "discord_post_file": discord_post_file,
    }
