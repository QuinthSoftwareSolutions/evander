from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from evander.models import utc_now
from evander.storage import JsonStore

DEFAULT_AGENT_PROMPT_LOCK_LEASE_SECONDS = 3600


class AgentPromptLockRecord(BaseModel):
    lock_key: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    vm_name: str = Field(min_length=1)
    session_id: str | None = None
    dispatch_id: str | None = None
    effect_id: str | None = None
    claimed_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime


class AgentPromptLockClaim(BaseModel):
    claimed: bool
    lock_key: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    record: AgentPromptLockRecord | None = None
    existing: AgentPromptLockRecord | None = None


def agent_prompt_lock_key(
    *,
    target: str,
    vm_name: str,
    session_id: str | None = None,
) -> str:
    normalized_session_id = str(session_id or "").strip()
    if normalized_session_id:
        return f"session:{normalized_session_id}"
    normalized_vm_name = str(vm_name or "").strip()
    if normalized_vm_name:
        return f"vm:{normalized_vm_name}"
    normalized_target = str(target or "").strip()
    return f"target:{normalized_target}"


class AgentPromptLockStore:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, AgentPromptLockRecord)

    def read_all(self) -> dict[str, AgentPromptLockRecord]:
        return self.store.read_all()

    def claim(
        self,
        *,
        lock_key: str,
        owner_id: str,
        target: str,
        vm_name: str,
        session_id: str | None = None,
        dispatch_id: str | None = None,
        effect_id: str | None = None,
        lease_seconds: int = DEFAULT_AGENT_PROMPT_LOCK_LEASE_SECONDS,
        owner_is_active: Callable[[AgentPromptLockRecord], bool] | None = None,
    ) -> AgentPromptLockClaim:
        normalized_lock_key = str(lock_key or "").strip()
        normalized_owner_id = str(owner_id or "").strip()
        if not normalized_lock_key:
            raise ValueError("lock_key must be a non-empty string")
        if not normalized_owner_id:
            raise ValueError("owner_id must be a non-empty string")
        now = utc_now()
        expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))

        def updater(records: dict[str, AgentPromptLockRecord]) -> AgentPromptLockClaim:
            existing = records.get(normalized_lock_key)
            if existing is not None and existing.owner_id != normalized_owner_id:
                active = False
                if owner_is_active is not None:
                    try:
                        active = bool(owner_is_active(existing))
                    except Exception:
                        active = False
                if active or existing.expires_at > now:
                    return AgentPromptLockClaim(
                        claimed=False,
                        lock_key=normalized_lock_key,
                        owner_id=normalized_owner_id,
                        existing=existing,
                    )
            claimed_at = (
                existing.claimed_at
                if existing is not None and existing.owner_id == normalized_owner_id
                else now
            )
            record = AgentPromptLockRecord(
                lock_key=normalized_lock_key,
                owner_id=normalized_owner_id,
                target=target,
                vm_name=vm_name,
                session_id=session_id,
                dispatch_id=dispatch_id,
                effect_id=effect_id,
                claimed_at=claimed_at,
                updated_at=now,
                expires_at=expires_at,
            )
            records[normalized_lock_key] = record
            return AgentPromptLockClaim(
                claimed=True,
                lock_key=normalized_lock_key,
                owner_id=normalized_owner_id,
                record=record,
            )

        return self.store.update_all(updater)

    def release(self, *, lock_key: str, owner_id: str) -> bool:
        normalized_lock_key = str(lock_key or "").strip()
        normalized_owner_id = str(owner_id or "").strip()
        if not normalized_lock_key or not normalized_owner_id:
            return False

        def updater(records: dict[str, AgentPromptLockRecord]) -> bool:
            existing = records.get(normalized_lock_key)
            if existing is None or existing.owner_id != normalized_owner_id:
                return False
            records.pop(normalized_lock_key, None)
            return True

        return bool(self.store.update_all(updater))


def prompt_lock_metadata(record: AgentPromptLockRecord | None) -> dict[str, Any]:
    if record is None:
        return {}
    return {
        "prompt_lock_key": record.lock_key,
        "prompt_lock_owner_id": record.owner_id,
    }
