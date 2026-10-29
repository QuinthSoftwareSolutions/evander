"""MCP tools for email read."""

from __future__ import annotations
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
import httpx
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field as PydanticField
from pydantic import ValidationError
from evander.email.http_utils import ProviderRequestError
from evander.email.discovery import discover_email_mailboxes
from evander.email.providers import EmailMessageSearchQuery
from evander.models import (
    ToolName,
)
from evander.policy import PolicyError

from .communication_support import CommunicationToolHelpers, _EmailListCursorCodec


def create_email_read_tools(
    context: Any,
    helpers: CommunicationToolHelpers,
    *,
    email_list_cursor_codec: _EmailListCursorCodec,
) -> dict[str, Callable[..., Any]]:
    audit_failure = helpers.audit_failure
    audit_success = helpers.audit_success

    def email_list_mailboxes(session_id: str) -> dict[str, Any]:
        """Discover this session's mailbox IDs, accounts, access levels and allowed folders.

        Use the returned mailbox_id (not the account address) in email tools. This
        reads host configuration only; it does not access messages or verify provider
        credentials. Mailbox grants do not override tool, send or recipient policies.
        """
        args = {"session_id": session_id}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_LIST_MAILBOXES)
            mailboxes = discover_email_mailboxes(
                session,
                context.resolve_provider_config_for_session(session_id),
                context.policy,
            )
            audit_success(
                session_id, ToolName.EMAIL_LIST_MAILBOXES, args, {"count": len(mailboxes)}
            )
            return {"mailboxes": mailboxes}
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_LIST_MAILBOXES, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_LIST_MAILBOXES,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

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

    return {
        "email_list_mailboxes": email_list_mailboxes,
        "email_list_unread": email_list_unread,
        "email_list_messages": email_list_messages,
        "email_get_message": email_get_message,
    }
