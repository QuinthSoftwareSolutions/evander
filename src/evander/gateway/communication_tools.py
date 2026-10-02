"""Ordered registration for the gateway communication capability groups."""

from __future__ import annotations

from typing import Any
from .communication_support import (
    CommunicationToolHelpers as CommunicationToolHelpers,
    TeamsMentionRefsArgument as TeamsMentionRefsArgument,
    _EmailListCursorCodec as _EmailListCursorCodec,
)
from .communication_discord_tools import create_discord_tools
from .communication_teams_tools import create_teams_tools
from .communication_voice_tools import create_voice_tools
from .communication_email_read_tools import create_email_read_tools
from .communication_email_write_tools import create_email_write_tools
from .communication_attachments_tools import create_attachments_tools
from .communication_artifacts_tools import create_artifacts_tools


def register_communication_tools(
    server: Any,
    context: Any,
    deps: Any,
    helpers: CommunicationToolHelpers | None = None,
    **legacy_helpers: Any,
) -> None:
    """Register all communication capabilities in their established public order."""
    if helpers is None:
        helpers = CommunicationToolHelpers(**legacy_helpers)
    transport_session_map = helpers.transport_session_map
    if transport_session_map is None:
        transport_session_map = getattr(server, "_evander_transport_session_map", {})
    email_list_cursor_codec = _EmailListCursorCodec()
    handlers = {}
    handlers.update(create_discord_tools(context, helpers))
    handlers.update(create_teams_tools(context, helpers))
    handlers.update(
        create_voice_tools(context, helpers, transport_session_map=transport_session_map)
    )
    handlers.update(
        create_email_read_tools(context, helpers, email_list_cursor_codec=email_list_cursor_codec)
    )
    handlers.update(create_email_write_tools(context, helpers))
    handlers.update(create_attachments_tools(context, helpers))
    handlers.update(create_artifacts_tools(context, helpers))
    for name in (
        "discord_get_commands",
        "discord_post_status",
        "teams_get_messages",
        "has_pending_discord_messages",
        "list_unread_discord_messages",
        "list_all_discord_messages",
        "add_message_reaction",
        "list_contacts",
        "make_phone_call",
        "get_phone_call_result",
        "submit_voice_query_response",
        "get_voice_query_visual_context",
        "teams_post_status",
        "teams_meeting_get_events",
        "teams_meeting_speak",
        "teams_meeting_stop",
        "email_list_mailboxes",
        "email_list_unread",
        "email_list_messages",
        "email_get_message",
        "email_create_draft",
        "list_inline_media",
        "download_inline_media",
        "communication_list_attachments",
        "communication_download_attachment",
        "stage_artifact",
        "communication_copy_artifact_to_workspace",
        "communication_stage_workspace_file",
        "discord_post_file",
        "teams_post_file",
        "email_create_draft_with_attachments",
        "email_send",
        "email_send_with_attachments",
        "email_send_approved_with_attachments",
        "email_send_approved",
    ):
        server.tool()(handlers[name])
