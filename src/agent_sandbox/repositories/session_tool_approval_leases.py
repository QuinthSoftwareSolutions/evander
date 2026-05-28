from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from agent_sandbox.models import (
    ApprovalGrantSettings,
    SessionToolApprovalLease,
    ToolName,
    digest_payload,
    normalize_tool_names,
)
from agent_sandbox.storage import JsonStore


DEFAULT_SESSION_TOOL_APPROVAL_LEASES_FILENAME = "session-tool-approval-leases.json"


class SessionToolApprovalLeaseRepository(Protocol):
    def upsert_lease(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        tools: list[str | ToolName],
        expires_at: datetime,
        effect_execution_id: str | None = None,
        approval_settings: dict[str, ApprovalGrantSettings] | None = None,
    ) -> SessionToolApprovalLease: ...

    def get_lease(self, lease_id: str) -> SessionToolApprovalLease | None: ...

    def is_tool_approved(
        self,
        *,
        lease_id: str,
        session_id: str,
        workflow_run_id: str,
        tool_name: ToolName,
        now: datetime | None = None,
    ) -> bool: ...

    def revoke_lease(self, lease_id: str, *, revoked_at: datetime | None = None) -> bool: ...

    def consume_tool_use(
        self,
        *,
        lease_id: str,
        session_id: str,
        workflow_run_id: str,
        tool_name: ToolName,
        now: datetime | None = None,
    ) -> bool: ...


class JsonSessionToolApprovalLeaseRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._store = JsonStore(path, SessionToolApprovalLease)

    @classmethod
    def from_runtime_dir(cls, runtime_dir: Path) -> "JsonSessionToolApprovalLeaseRepository":
        return cls(runtime_dir / DEFAULT_SESSION_TOOL_APPROVAL_LEASES_FILENAME)

    def read_all(self) -> dict[str, SessionToolApprovalLease]:
        return self._store.read_all()

    def upsert_lease(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        tools: list[str | ToolName],
        expires_at: datetime,
        effect_execution_id: str | None = None,
        approval_settings: dict[str, ApprovalGrantSettings] | None = None,
    ) -> SessionToolApprovalLease:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_workflow_run_id = _required_text(workflow_run_id, "workflow_run_id")
        normalized_tools = normalize_tool_names(tools, field_name="tools")
        if not normalized_tools:
            raise ValueError("tools must include at least one MCP tool")
        effective_expires_at = _as_utc(expires_at)
        now = datetime.now(UTC)
        lease_id = _lease_id(
            session_id=normalized_session_id,
            workflow_run_id=normalized_workflow_run_id,
            tools=normalized_tools,
            effect_execution_id=effect_execution_id,
        )
        lease = SessionToolApprovalLease(
            lease_id=lease_id,
            session_id=normalized_session_id,
            workflow_run_id=normalized_workflow_run_id,
            tools=normalized_tools,
            effect_execution_id=str(effect_execution_id or "").strip() or None,
            approval_settings=_normalize_approval_settings(
                approval_settings or {}, normalized_tools
            ),
            created_at=now,
            expires_at=effective_expires_at,
        )
        self._store.put(lease_id, lease)
        return lease

    def get_lease(self, lease_id: str) -> SessionToolApprovalLease | None:
        normalized_lease_id = str(lease_id or "").strip()
        if not normalized_lease_id:
            return None
        return self._store.get(normalized_lease_id)

    def is_tool_approved(
        self,
        *,
        lease_id: str,
        session_id: str,
        workflow_run_id: str,
        tool_name: ToolName,
        now: datetime | None = None,
    ) -> bool:
        lease = self.get_lease(lease_id)
        if lease is None:
            return False
        effective_now = now or datetime.now(UTC)
        if lease.revoked_at is not None:
            return False
        if _as_utc(lease.expires_at) <= effective_now:
            return False
        if lease.session_id != str(session_id or "").strip():
            return False
        if lease.workflow_run_id != str(workflow_run_id or "").strip():
            return False
        return tool_name in set(lease.tools)

    def consume_tool_use(
        self,
        *,
        lease_id: str,
        session_id: str,
        workflow_run_id: str,
        tool_name: ToolName,
        now: datetime | None = None,
    ) -> bool:
        normalized_lease_id = str(lease_id or "").strip()
        if not normalized_lease_id:
            return False
        normalized_session_id = str(session_id or "").strip()
        normalized_workflow_run_id = str(workflow_run_id or "").strip()
        effective_now = now or datetime.now(UTC)

        def _consume(items: dict[str, SessionToolApprovalLease]) -> bool:
            lease = items.get(normalized_lease_id)
            if lease is None:
                return False
            if lease.revoked_at is not None:
                return False
            if _as_utc(lease.expires_at) <= effective_now:
                return False
            if lease.session_id != normalized_session_id:
                return False
            if lease.workflow_run_id != normalized_workflow_run_id:
                return False
            if tool_name not in set(lease.tools):
                return False
            settings = lease.approval_settings.get(tool_name.value)
            if settings is None or settings.max_uses is None:
                return True
            used_counts = dict(lease.used_counts)
            used_count = int(used_counts.get(tool_name.value, 0))
            if used_count >= settings.max_uses:
                return False
            used_counts[tool_name.value] = used_count + 1
            items[normalized_lease_id] = lease.model_copy(update={"used_counts": used_counts})
            return True

        return bool(self._store.update_all(_consume))

    def revoke_lease(self, lease_id: str, *, revoked_at: datetime | None = None) -> bool:
        normalized_lease_id = str(lease_id or "").strip()
        if not normalized_lease_id:
            return False

        def _revoke(items: dict[str, SessionToolApprovalLease]) -> bool:
            lease = items.get(normalized_lease_id)
            if lease is None:
                return False
            if lease.revoked_at is not None:
                return True
            items[normalized_lease_id] = lease.model_copy(
                update={"revoked_at": revoked_at or datetime.now(UTC)}
            )
            return True

        return bool(self._store.update_all(_revoke))

    def delete_expired(self, *, now: datetime | None = None, grace_period: timedelta | None = None) -> int:
        effective_now = now or datetime.now(UTC)
        cutoff = effective_now - (grace_period or timedelta())

        def _delete(items: dict[str, SessionToolApprovalLease]) -> int:
            removed = 0
            for lease_id, lease in list(items.items()):
                if _as_utc(lease.expires_at) > cutoff and lease.revoked_at is None:
                    continue
                items.pop(lease_id, None)
                removed += 1
            return removed

        return int(self._store.update_all(_delete) or 0)


def _lease_id(
    *,
    session_id: str,
    workflow_run_id: str,
    tools: list[ToolName],
    effect_execution_id: str | None,
) -> str:
    digest = digest_payload(
        {
            "session_id": session_id,
            "workflow_run_id": workflow_run_id,
            "tools": sorted(tool.value for tool in tools),
            "effect_execution_id": str(effect_execution_id or "").strip() or None,
        }
    )
    return f"mcp_tool_lease_{digest[:20]}"


def _normalize_approval_settings(
    settings: dict[str, ApprovalGrantSettings], tools: list[ToolName]
) -> dict[str, ApprovalGrantSettings]:
    allowed_tools = {tool.value for tool in tools}
    normalized: dict[str, ApprovalGrantSettings] = {}
    for tool_name, value in settings.items():
        normalized_tool = ToolName(str(tool_name).strip()).value
        if normalized_tool not in allowed_tools:
            continue
        normalized[normalized_tool] = ApprovalGrantSettings.model_validate(value)
    return normalized


def _required_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
