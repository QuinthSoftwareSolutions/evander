from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.models import LocalIssueRecord
from agent_sandbox.storage import JsonStore
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import TicketChangedTriggerDefinition
from agent_sandbox.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class TicketChangedTriggerProvider(TriggerProviderBase):
    _RECENT_ISSUE_LIMIT = 512

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        cursor = _parse_dt(state.get("last_updated_at"))
        recent_issue_ids = [
            value
            for value in state.get("recent_issue_ids", [])
            if isinstance(value, str) and value.strip()
        ]
        recent_issue_id_set = set(recent_issue_ids)

        issues_store = JsonStore(self._runtime_dir() / "local-issues.json", LocalIssueRecord)
        candidates = [
            issue
            for issue in issues_store.read_all().values()
            if _within_directories(issue.directory_path, trigger.directories)
        ]
        candidates.sort(key=lambda issue: (issue.updated_at, issue.issue_id))

        events: list[TriggerEvent] = []
        for issue in candidates:
            occurred_at = issue.updated_at.astimezone(UTC)
            if cursor is not None and occurred_at < cursor:
                continue
            if cursor is not None and occurred_at == cursor and issue.issue_id in recent_issue_id_set:
                continue
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{issue.issue_id}:{occurred_at.isoformat()}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload={"ticket": issue.model_dump(mode="json")},
                )
            )

        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload.get("ticket")
        if not isinstance(payload, dict):
            return
        issue_id = str(payload.get("issue_id") or "").strip()
        occurred_at = _parse_dt(payload.get("updated_at")) or event.occurred_at
        if not issue_id or occurred_at is None:
            return
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_issue(state, issue_id, occurred_at),
        )

    def _trigger(self) -> TicketChangedTriggerDefinition:
        if not isinstance(self.trigger_definition, TicketChangedTriggerDefinition):
            raise RuntimeError(
                "TicketChangedTriggerProvider initialized with a non-ticket-changed trigger definition"
            )
        return self.trigger_definition

    def _runtime_dir(self):
        if self.runtime_context is None:
            raise RuntimeError("TicketChangedTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / "trigger-cursors-ticket-changed.json")


def _within_directories(candidate: str, directories: list[str]) -> bool:
    if not directories:
        return True
    for directory in directories:
        normalized = directory.strip().strip("/")
        if candidate == normalized or candidate.startswith(f"{normalized}/"):
            return True
    return False


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _acknowledge_issue(
    state: dict[str, object],
    issue_id: str,
    occurred_at: datetime,
) -> dict[str, object]:
    state["last_updated_at"] = latest_cursor_timestamp_text(
        state.get("last_updated_at"),
        occurred_at,
    )
    state["recent_issue_ids"] = merge_cursor_ids(
        state.get("recent_issue_ids"),
        [issue_id],
        limit=TicketChangedTriggerProvider._RECENT_ISSUE_LIMIT,
    )
    return state
