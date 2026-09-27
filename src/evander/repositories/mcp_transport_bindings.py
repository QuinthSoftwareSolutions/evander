from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from evander.models import digest_payload


DEFAULT_MCP_TRANSPORT_BINDINGS_FILENAME = "mcp-transport-bindings.db"
LEGACY_MCP_TRANSPORT_BINDINGS_FILENAME = "mcp-transport-bindings.json"
_SQLITE_TIMEOUT_SECONDS = 30.0
_UNSET = object()


def _required_text(value: object, field_name: str) -> str:
    candidate = getattr(value, "value", value)
    text = str(candidate or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: object) -> str | None:
    candidate = getattr(value, "value", value)
    text = str(candidate or "").strip()
    return text or None


def _normalize_visible_tool_names(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("last_visible_tool_names must be a JSON array of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _optional_text(item)
        if text is None:
            continue
        key = text
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    normalized.sort()
    return normalized


def _visible_tool_name_fingerprint(tool_names: list[str]) -> str:
    return digest_payload({"visible_tool_names": tool_names})


class McpTransportBindingConflict(BaseModel):
    declared_session_id: str
    conflict_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str

    @field_validator("declared_session_id", "reason", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class McpTransportBinding(BaseModel):
    transport_session_id: str = Field(min_length=1)
    bound_session_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_protocol_version: str | None = None
    last_workflow_run_id: str | None = None
    last_mcp_tool_approval_lease_id: str | None = None
    last_tools_list_at: datetime | None = None
    last_visible_tool_names: list[str] = Field(default_factory=list)
    last_visible_tool_fingerprint: str | None = None
    latest_conflict: McpTransportBindingConflict | None = None

    @field_validator("transport_session_id", "bound_session_id", mode="before")
    @classmethod
    def _normalize_transport_ids(cls, value: object, info) -> str | None:
        if info.field_name == "bound_session_id" and value is None:
            return None
        normalized = _optional_text(value)
        if info.field_name == "bound_session_id":
            return normalized
        return _required_text(normalized, info.field_name)

    @field_validator(
        "last_protocol_version",
        "last_workflow_run_id",
        "last_mcp_tool_approval_lease_id",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text_fields(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("last_visible_tool_names", mode="before")
    @classmethod
    def _normalize_visible_tool_names_field(cls, value: object) -> list[str]:
        return _normalize_visible_tool_names(value)

    @field_validator("last_visible_tool_fingerprint", mode="before")
    @classmethod
    def _normalize_visible_tool_fingerprint(cls, value: object) -> str | None:
        return _optional_text(value)


class McpTransportBindingRepository(Protocol):
    def read_all(self) -> dict[str, McpTransportBinding]: ...

    def get_binding(self, transport_session_id: str) -> McpTransportBinding | None: ...

    def bind_transport_session(
        self,
        *,
        transport_session_id: str,
        bound_session_id: str,
        last_protocol_version: str | None = None,
        last_workflow_run_id: str | None = None,
        last_mcp_tool_approval_lease_id: str | None = None,
        now: datetime | None = None,
    ) -> McpTransportBinding: ...

    def record_visible_tools(
        self,
        *,
        transport_session_id: str,
        bound_session_id: str,
        visible_tool_names: list[str],
        last_protocol_version: str | None = None,
        last_workflow_run_id: str | None = None,
        last_mcp_tool_approval_lease_id: str | None = None,
        now: datetime | None = None,
    ) -> McpTransportBinding: ...

    def record_conflict(
        self,
        *,
        transport_session_id: str,
        declared_session_id: str,
        reason: str,
        bound_session_id: str | None = None,
        last_protocol_version: str | None = None,
        last_workflow_run_id: str | None = None,
        last_mcp_tool_approval_lease_id: str | None = None,
        now: datetime | None = None,
    ) -> McpTransportBinding: ...


class SqliteMcpTransportBindingRepository:
    """Persist each connection independently, importing the legacy snapshot once."""

    def __init__(self, path: Path, *, legacy_path: Path | None = None) -> None:
        path = Path(path)
        self.path = path.with_suffix(".db") if path.suffix.lower() == ".json" else path
        self.legacy_path = legacy_path or self.path.with_suffix(".json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def from_runtime_dir(cls, runtime_dir: Path) -> "SqliteMcpTransportBindingRepository":
        return cls(
            runtime_dir / DEFAULT_MCP_TRANSPORT_BINDINGS_FILENAME,
            legacy_path=runtime_dir / LEGACY_MCP_TRANSPORT_BINDINGS_FILENAME,
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=_SQLITE_TIMEOUT_SECONDS)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _initialized(connection: sqlite3.Connection) -> bool:
        try:
            return (
                connection.execute(
                    "SELECT 1 FROM mcp_transport_binding_metadata WHERE key = 'legacy_json_imported'"
                ).fetchone()
                is not None
            )
        except sqlite3.OperationalError as exc:
            if "no such table: mcp_transport_binding_metadata" not in str(exc):
                raise
            return False

    def _initialize(self) -> None:
        with self._connect() as connection:
            # Short-lived repository readers need neither a writer transaction
            # nor WAL renegotiation once another process has initialized the store.
            if self._initialized(connection):
                return
            deadline = time.monotonic() + _SQLITE_TIMEOUT_SECONDS
            while True:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                connection.execute(f"PRAGMA busy_timeout={remaining_ms}")
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    code = getattr(exc, "sqlite_errorcode", None)
                    if code is None or code & 0xFF != sqlite3.SQLITE_BUSY:
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    time.sleep(min(0.01, remaining))
            connection.execute(f"PRAGMA busy_timeout={int(_SQLITE_TIMEOUT_SECONDS * 1000)}")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS mcp_transport_bindings ("
                "transport_session_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS mcp_transport_binding_metadata ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            # Another process may have completed the import while this one
            # waited for the transaction. Never replay stale JSON over live rows.
            if self._initialized(connection):
                return
            lock_path = self.legacy_path.with_name(f".{self.legacy_path.name}.lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    self._import_legacy(connection)
                    connection.execute(
                        "INSERT INTO mcp_transport_binding_metadata(key, value) "
                        "VALUES ('legacy_json_imported', ?)",
                        (datetime.now(UTC).isoformat(),),
                    )
                    # Keep the JSON lock through the commit. Deployment must
                    # stop old writers before switching every reader to SQLite.
                    connection.commit()
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _import_legacy(self, connection: sqlite3.Connection) -> None:
        try:
            text = self.legacy_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        payload = json.loads(text) if text.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("Legacy MCP transport bindings must be a JSON object")
        for key, value in payload.items():
            binding = McpTransportBinding.model_validate(value)
            if key != binding.transport_session_id:
                raise ValueError("Legacy MCP transport binding key does not match its session ID")
            connection.execute(
                "INSERT INTO mcp_transport_bindings(transport_session_id, payload_json) "
                "VALUES (?, ?) ON CONFLICT(transport_session_id) DO NOTHING",
                (key, binding.model_dump_json()),
            )

    def read_all(self) -> dict[str, McpTransportBinding]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT transport_session_id, payload_json FROM mcp_transport_bindings "
                "ORDER BY transport_session_id"
            ).fetchall()
        return {key: McpTransportBinding.model_validate_json(payload) for key, payload in rows}

    def get_binding(self, transport_session_id: str) -> McpTransportBinding | None:
        normalized_transport_session_id = _optional_text(transport_session_id)
        if normalized_transport_session_id is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM mcp_transport_bindings WHERE transport_session_id = ?",
                (normalized_transport_session_id,),
            ).fetchone()
        return McpTransportBinding.model_validate_json(row[0]) if row is not None else None

    def _upsert_binding(
        self,
        *,
        transport_session_id: str,
        now: datetime | None = None,
        bound_session_id: str | None | object = _UNSET,
        last_protocol_version: str | None | object = _UNSET,
        last_workflow_run_id: str | None | object = _UNSET,
        last_mcp_tool_approval_lease_id: str | None | object = _UNSET,
        last_tools_list_at: datetime | None | object = _UNSET,
        last_visible_tool_names: list[str] | object = _UNSET,
        latest_conflict: McpTransportBindingConflict | None | object = _UNSET,
    ) -> McpTransportBinding:
        normalized_transport_session_id = _required_text(
            transport_session_id,
            "transport_session_id",
        )
        effective_now = now or datetime.now(UTC)

        with self._connect() as connection:
            # The read and partial update must share a writer transaction:
            # concurrent writers must preserve fields this operation does not replace.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM mcp_transport_bindings WHERE transport_session_id = ?",
                (normalized_transport_session_id,),
            ).fetchone()
            existing = McpTransportBinding.model_validate_json(row[0]) if row is not None else None
            updates: dict[str, Any] = {
                "updated_at": effective_now,
                "last_seen_at": effective_now,
            }
            if bound_session_id is not _UNSET:
                updates["bound_session_id"] = _optional_text(bound_session_id)
            if last_protocol_version is not _UNSET:
                updates["last_protocol_version"] = _optional_text(last_protocol_version)
            if last_workflow_run_id is not _UNSET:
                updates["last_workflow_run_id"] = _optional_text(last_workflow_run_id)
            if last_mcp_tool_approval_lease_id is not _UNSET:
                updates["last_mcp_tool_approval_lease_id"] = _optional_text(
                    last_mcp_tool_approval_lease_id
                )
            if last_tools_list_at is not _UNSET:
                updates["last_tools_list_at"] = last_tools_list_at
            if last_visible_tool_names is not _UNSET:
                normalized_visible_tool_names = _normalize_visible_tool_names(
                    last_visible_tool_names
                )
                updates["last_visible_tool_names"] = normalized_visible_tool_names
                updates["last_visible_tool_fingerprint"] = _visible_tool_name_fingerprint(
                    normalized_visible_tool_names
                )
            if latest_conflict is not _UNSET:
                updates["latest_conflict"] = latest_conflict
            if existing is None:
                binding = McpTransportBinding(
                    transport_session_id=normalized_transport_session_id,
                    created_at=effective_now,
                    **updates,
                )
            else:
                binding = existing.model_copy(update=updates)
            connection.execute(
                "INSERT INTO mcp_transport_bindings(transport_session_id, payload_json) "
                "VALUES (?, ?) ON CONFLICT(transport_session_id) DO UPDATE "
                "SET payload_json = excluded.payload_json",
                (normalized_transport_session_id, binding.model_dump_json()),
            )
        return binding

    def bind_transport_session(
        self,
        *,
        transport_session_id: str,
        bound_session_id: str,
        last_protocol_version: str | None = None,
        last_workflow_run_id: str | None = None,
        last_mcp_tool_approval_lease_id: str | None = None,
        now: datetime | None = None,
    ) -> McpTransportBinding:
        return self._upsert_binding(
            transport_session_id=transport_session_id,
            now=now,
            bound_session_id=bound_session_id,
            last_protocol_version=last_protocol_version,
            last_workflow_run_id=last_workflow_run_id,
            last_mcp_tool_approval_lease_id=last_mcp_tool_approval_lease_id,
            latest_conflict=None,
        )

    def record_visible_tools(
        self,
        *,
        transport_session_id: str,
        bound_session_id: str,
        visible_tool_names: list[str],
        last_protocol_version: str | None = None,
        last_workflow_run_id: str | None = None,
        last_mcp_tool_approval_lease_id: str | None = None,
        now: datetime | None = None,
    ) -> McpTransportBinding:
        return self._upsert_binding(
            transport_session_id=transport_session_id,
            now=now,
            bound_session_id=bound_session_id,
            last_protocol_version=last_protocol_version,
            last_workflow_run_id=last_workflow_run_id,
            last_mcp_tool_approval_lease_id=last_mcp_tool_approval_lease_id,
            last_tools_list_at=now or datetime.now(UTC),
            last_visible_tool_names=visible_tool_names,
            latest_conflict=None,
        )

    def record_conflict(
        self,
        *,
        transport_session_id: str,
        declared_session_id: str,
        reason: str,
        bound_session_id: str | None = None,
        last_protocol_version: str | None = None,
        last_workflow_run_id: str | None = None,
        last_mcp_tool_approval_lease_id: str | None = None,
        now: datetime | None = None,
    ) -> McpTransportBinding:
        conflict = McpTransportBindingConflict(
            declared_session_id=declared_session_id,
            conflict_at=now or datetime.now(UTC),
            reason=reason,
        )
        return self._upsert_binding(
            transport_session_id=transport_session_id,
            now=now,
            bound_session_id=bound_session_id,
            last_protocol_version=last_protocol_version,
            last_workflow_run_id=last_workflow_run_id,
            last_mcp_tool_approval_lease_id=last_mcp_tool_approval_lease_id,
            latest_conflict=conflict,
        )


# Keep callers using the former repository name on the same authoritative store.
JsonMcpTransportBindingRepository = SqliteMcpTransportBindingRepository
