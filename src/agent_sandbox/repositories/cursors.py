from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from agent_sandbox.messaging.discord_inbox_store import DiscordInboxStore
from agent_sandbox.email.cursor_store import DurableCursorStore, EmailSyncCursor
from agent_sandbox.models import DiscordMessageEnvelope
from agent_sandbox.messaging.teams_inbox_store import (
    TeamsInboxCursorState,
    TeamsInboxMessage,
    TeamsInboxStore,
)


class EmailCursorRepository(Protocol):
    def get(self, mailbox_id: str, folder: str) -> EmailSyncCursor | None: ...

    def update(
        self,
        mailbox_id: str,
        folder: str,
        updater: Callable[[EmailSyncCursor | None], EmailSyncCursor],
    ) -> EmailSyncCursor: ...


class DiscordInboxCursorRepository(Protocol):
    def get_cursor(self, channel_id: str) -> str | None: ...

    def set_cursor(self, channel_id: str, cursor: str | None) -> None: ...

    def add_message_if_new(self, envelope: DiscordMessageEnvelope) -> bool: ...

    def get_session_cursor(self, session_id: str, channel_id: str) -> str | None: ...

    def advance_session_cursor(
        self, session_id: str, channel_id: str, cursor: str | None
    ) -> str | None: ...

    def read_raw(self) -> dict[str, Any]: ...


class TeamsInboxCursorRepository(Protocol):
    def get_cursor_state(self, channel_target: str) -> TeamsInboxCursorState | None: ...

    def set_cursor(
        self,
        channel_target: str,
        cursor: str | None,
        *,
        created_at: datetime | None = None,
    ) -> None: ...

    def add_message_if_new(self, message: TeamsInboxMessage) -> bool: ...

    def get_message(self, cursor: str) -> TeamsInboxMessage | None: ...

    def list_all_messages(
        self,
        *,
        channel_targets: list[str] | None = None,
        limit: int | None = None,
    ) -> list[TeamsInboxMessage]: ...


class JsonEmailCursorRepository(DurableCursorStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)


class JsonDiscordInboxCursorRepository(DiscordInboxStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)


class JsonTeamsInboxCursorRepository(TeamsInboxStore):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
