from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
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


class DiscordService(Protocol):
    def get_commands(self, since_cursor: str | None = None) -> list["DiscordCommandEnvelope"]:
        ...

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, Any]:
        ...


class StubDiscordService:
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


class DiscordBotApiService:
    def __init__(
        self,
        command_store: JsonStore[DiscordCommandEnvelope],
        *,
        token: str,
        api_base_url: str,
        poll_limit: int,
        timeout_seconds: float,
    ) -> None:
        self.command_store = command_store
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.poll_limit = poll_limit
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }

    def _known_channel_ids(self) -> list[str]:
        channel_ids = {command.channel_id for command in self.command_store.read_all().values()}
        return sorted(channel_ids)

    def get_commands(self, since_cursor: str | None = None) -> list[DiscordCommandEnvelope]:
        envelopes: dict[str, DiscordCommandEnvelope] = dict(self.command_store.read_all())
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for channel_id in self._known_channel_ids():
                response = client.get(
                    f"{self.api_base_url}/channels/{channel_id}/messages",
                    headers=self._headers(),
                    params={"limit": self.poll_limit},
                )
                response.raise_for_status()
                for payload in response.json():
                    envelope = DiscordCommandEnvelope(
                        command_id=payload["id"],
                        author_id=payload["author"]["id"],
                        channel_id=channel_id,
                        content=payload.get("content", ""),
                        metadata={"source": "discord_bot_api", "message": payload},
                    )
                    envelopes[envelope.command_id] = envelope
                    self.command_store.put(envelope.command_id, envelope)

        commands = sorted(envelopes.values(), key=lambda item: item.created_at)
        if since_cursor is None:
            return commands
        return [command for command in commands if command.command_id > since_cursor]

    def post_status(self, message: str, channel_id: str, session_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.api_base_url}/channels/{channel_id}/messages",
                headers=self._headers(),
                json={"content": message},
            )
            response.raise_for_status()
            payload = response.json()

        envelope = DiscordCommandEnvelope(
            command_id=payload["id"],
            author_id=payload["author"]["id"],
            channel_id=channel_id,
            content=payload.get("content", ""),
            metadata={
                "source": "discord_bot_api_status",
                "session_id": session_id,
                "message": payload,
            },
        )
        self.command_store.put(envelope.command_id, envelope)
        return {
            "status": "posted",
            "channel_id": channel_id,
            "session_id": session_id,
            "message_id": payload["id"],
        }


from pydantic import BaseModel, Field  # noqa: E402


class DiscordCommandEnvelope(BaseModel):
    command_id: str
    author_id: str
    channel_id: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
