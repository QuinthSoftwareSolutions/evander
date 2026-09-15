"""Read-only lifecycle projections; these are deliberately not partial TeamState."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class LifecycleCandidate:
    dispatch_id: str
    session_id: str
    agent_id: str | None
    status: str | None
    active: bool
    activation_completed_at: datetime | None
    native_thread_id: str | None
    native_thread_observed_at: datetime | None
    lineage_thread_id: str | None
    lineage_owner_id: str | None
    current_session_id: str | None
    mirror_due: bool
    queued_at: datetime | None
    governance_group_id: str | None = None
    recovery_priority: int = 0

    @property
    def cursor(self) -> str:
        return f"{self.recovery_priority}:{self.dispatch_id}"


@dataclass(frozen=True)
class LifecycleCandidatePage:
    revision: int
    candidates: tuple[LifecycleCandidate, ...]
    next_cursor: str | None
