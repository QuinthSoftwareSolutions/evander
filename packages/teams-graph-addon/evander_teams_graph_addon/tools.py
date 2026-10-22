"""MCP tools for teams."""

from __future__ import annotations
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
import httpx
from mcp.server.mcpserver.exceptions import ToolError
from evander.models import (
    ToolName,
)
from evander.policy import PolicyError
from evander.services import (
    TeamsAuthenticationError,
    normalize_teams_mention_refs,
    parse_teams_target,
    resolve_teams_send_target,
)
from evander.teams_meeting import voice_session_transcript_from_teams_events

from evander.gateway.communication_support import CommunicationToolHelpers, TeamsMentionRefsArgument
from .commands import TeamsMessageCommands


def create_teams_tools(
    context: Any, helpers: CommunicationToolHelpers
) -> dict[str, Callable[..., Any]]:
    _communication_target_audit_fields = helpers.communication_target_audit_fields
    _filter_teams_messages_for_session = helpers.filter_teams_messages_for_session
    _teams_cursor_for_target = helpers.teams_cursor_for_target
    audit_failure = helpers.audit_failure
    audit_success = helpers.audit_success

    def teams_update_message(
        session_id: str,
        channel_target: str,
        message_id: str,
        message: str,
        thread_id: str | None = None,
        expected_revision: int | None = None,
        *,
        image_artifact_id: str | None = None,
        clear_image: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace text in a message sent by this session, preserving mentions and rich content.

        Use the post result's message_revision as expected_revision for guarded edits.
        image_artifact_id replaces the opt-in PNG and requires file-send authority/approval.
        clear_image removes only that PNG using edit authority; neither permits arbitrary HTML.
        User aliases and messages without an outbound ownership receipt cannot be edited.
        """
        return TeamsMessageCommands(
            context,
            audit_success=audit_success,
            audit_failure=audit_failure,
            artifact_helpers=helpers,
        ).update_message(
            session_id,
            channel_target,
            message_id,
            message,
            thread_id,
            expected_revision,
            image_artifact_id=image_artifact_id,
            clear_image=clear_image,
            request_id=request_id,
        )

    def teams_get_messages(
        session_id: str,
        channel_target: str,
        since_cursor: str | None = None,
        before_cursor: str | None = None,
        limit: int | None = None,
        include_metadata: bool = False,
    ) -> dict[str, Any]:
        """Read messages with compact sender, reply and attachment details.

        Set include_metadata only when the full provider payload is needed.
        """
        args = {
            "session_id": session_id,
            "since_cursor": since_cursor,
            "before_cursor": before_cursor,
            "limit": limit,
            "channel_target": channel_target,
            "include_metadata": include_metadata,
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
            if isinstance(exc, TeamsAuthenticationError):
                raise ToolError(
                    "Teams authentication is unavailable; ask an operator to check the configured credentials."
                ) from exc
            raise

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
            if isinstance(exc, TeamsAuthenticationError):
                raise ToolError(
                    "Teams authentication is unavailable; ask an operator to check the configured credentials."
                ) from exc
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
            if isinstance(exc, TeamsAuthenticationError):
                raise ToolError(
                    "Teams authentication is unavailable; ask an operator to check the configured credentials."
                ) from exc
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
            if isinstance(exc, TeamsAuthenticationError):
                raise ToolError(
                    "Teams authentication is unavailable; ask an operator to check the configured credentials."
                ) from exc
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

    def teams_post_file(
        session_id: str,
        channel_target: str,
        message: str,
        artifact_ids: list[str],
        thread_id: str | None = None,
        request_id: str | None = None,
        *,
        editable_image_artifact_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Post staged files; optionally designate one PNG for later guarded image edits."""
        return TeamsMessageCommands(
            context,
            audit_success=audit_success,
            audit_failure=audit_failure,
            artifact_helpers=helpers,
        ).post_file(
            session_id,
            channel_target,
            message,
            artifact_ids,
            thread_id,
            request_id,
            editable_image_artifact_id=editable_image_artifact_id,
            idempotency_key=idempotency_key,
        )

    return {
        "teams_get_messages": teams_get_messages,
        "teams_post_status": teams_post_status,
        "teams_update_message": teams_update_message,
        "teams_meeting_get_events": teams_meeting_get_events,
        "teams_meeting_speak": teams_meeting_speak,
        "teams_meeting_stop": teams_meeting_stop,
        "teams_post_file": teams_post_file,
    }
