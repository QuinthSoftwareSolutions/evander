from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.models import digest_payload
from agent_sandbox.storage import JsonStore


DEFAULT_MCP_TRANSPORT_BINDINGS_FILENAME = "mcp-transport-bindings.json"
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


class JsonMcpTransportBindingRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._store = JsonStore(path, McpTransportBinding)

    @classmethod
    def from_runtime_dir(cls, runtime_dir: Path) -> "JsonMcpTransportBindingRepository":
        return cls(runtime_dir / DEFAULT_MCP_TRANSPORT_BINDINGS_FILENAME)

    def read_all(self) -> dict[str, McpTransportBinding]:
        return self._store.read_all()

    def get_binding(self, transport_session_id: str) -> McpTransportBinding | None:
        normalized_transport_session_id = _optional_text(transport_session_id)
        if normalized_transport_session_id is None:
            return None
        return self._store.get(normalized_transport_session_id)

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

        def _update(items: dict[str, McpTransportBinding]) -> McpTransportBinding:
            existing = items.get(normalized_transport_session_id)
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
                items[normalized_transport_session_id] = McpTransportBinding(
                    transport_session_id=normalized_transport_session_id,
                    created_at=effective_now,
                    **updates,
                )
            else:
                items[normalized_transport_session_id] = existing.model_copy(update=updates)
            return items[normalized_transport_session_id]

        return self._store.update_all(_update)

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
