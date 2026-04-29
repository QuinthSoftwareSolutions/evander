from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent_sandbox.models import DiscordCommandEnvelope, EmailMessage, TeamsMessageEnvelope
from agent_sandbox.storage import JsonStore


class EmailRecordsRepository(Protocol):
    def get(self, message_id: str) -> EmailMessage | None: ...

    def put(self, message_id: str, message: EmailMessage) -> None: ...

    def read_all(self) -> dict[str, EmailMessage]: ...


class DiscordRecordsRepository(Protocol):
    def get(self, command_id: str) -> DiscordCommandEnvelope | None: ...

    def put(self, command_id: str, envelope: DiscordCommandEnvelope) -> None: ...

    def read_all(self) -> dict[str, DiscordCommandEnvelope]: ...


class TeamsRecordsRepository(Protocol):
    def get(self, cursor: str) -> TeamsMessageEnvelope | None: ...

    def put(self, cursor: str, envelope: TeamsMessageEnvelope) -> None: ...

    def read_all(self) -> dict[str, TeamsMessageEnvelope]: ...


class JsonEmailRecordsRepository:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, EmailMessage)

    @property
    def model(self) -> type[EmailMessage]:
        return self._store.model

    def get(self, message_id: str) -> EmailMessage | None:
        return self._store.get(message_id)

    def put(self, message_id: str, message: EmailMessage) -> None:
        self._store.put(message_id, message)

    def read_all(self) -> dict[str, EmailMessage]:
        return self._store.read_all()


class JsonDiscordRecordsRepository:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, DiscordCommandEnvelope)

    @property
    def model(self) -> type[DiscordCommandEnvelope]:
        return self._store.model

    def get(self, command_id: str) -> DiscordCommandEnvelope | None:
        return self._store.get(command_id)

    def put(self, command_id: str, envelope: DiscordCommandEnvelope) -> None:
        self._store.put(command_id, envelope)

    def read_all(self) -> dict[str, DiscordCommandEnvelope]:
        return self._store.read_all()


class JsonTeamsRecordsRepository:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, TeamsMessageEnvelope)

    @property
    def model(self) -> type[TeamsMessageEnvelope]:
        return self._store.model

    def get(self, cursor: str) -> TeamsMessageEnvelope | None:
        return self._store.get(cursor)

    def put(self, cursor: str, envelope: TeamsMessageEnvelope) -> None:
        self._store.put(cursor, envelope)

    def read_all(self) -> dict[str, TeamsMessageEnvelope]:
        return self._store.read_all()
