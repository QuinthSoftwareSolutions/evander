from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .models import DraftMessage, EmailMessage
from .storage import JsonStore


class EmailService:
    def __init__(
        self,
        email_store: JsonStore[EmailMessage],
        draft_store: JsonStore[DraftMessage],
    ) -> None:
        self.email_store = email_store
        self.draft_store = draft_store

    def list_unread(self, folder: str, limit: int) -> list[EmailMessage]:
        messages = [
            message
            for message in self.email_store.read_all().values()
            if message.folder == folder and message.unread
        ]
        messages.sort(key=lambda item: item.received_at, reverse=True)
        return messages[:limit]

    def get_message(self, message_id: str) -> EmailMessage:
        message = self.email_store.get(message_id)
        if message is None:
            raise KeyError(f"Unknown message: {message_id}")
        return message

    def create_draft(
        self, thread_or_message_id: str | None, subject: str | None, body: str
    ) -> DraftMessage:
        now = datetime.now(UTC)
        draft = DraftMessage(
            draft_id=f"draft_{int(now.timestamp())}",
            thread_or_message_id=thread_or_message_id,
            subject=subject,
            body=body,
            created_at=now,
        )
        self.draft_store.put(draft.draft_id, draft)
        return draft

    def send_from_draft(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "stubbed",
            "request_id": request_id,
            "sent_payload": payload,
        }


class DiscordService:
    def __init__(self, command_store: JsonStore[DiscordCommandEnvelope]) -> None:
        self.command_store = command_store

    def get_commands(self, since_cursor: str | None = None) -> list[DiscordCommandEnvelope]:
        commands = list(self.command_store.read_all().values())
        commands.sort(key=lambda item: item.created_at)
        if since_cursor is None:
            return commands
        return [command for command in commands if command.command_id > since_cursor]

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, Any]:
        return {
            "status": "stubbed",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_preview": message[:120],
        }


from pydantic import BaseModel, Field  # noqa: E402


class DiscordCommandEnvelope(BaseModel):
    command_id: str
    author_id: str
    channel_id: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
