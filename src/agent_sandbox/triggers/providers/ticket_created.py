from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.models import LocalIssueRecord
from agent_sandbox.storage import JsonStore
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import TicketCreatedTriggerDefinition
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class TicketCreatedTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        cursor = _parse_dt(state.get("last_created_at"))

        issues_store = JsonStore(self._runtime_dir() / "local-issues.json", LocalIssueRecord)
        candidates = [
            issue
            for issue in issues_store.read_all().values()
            if _within_directories(issue.directory_path, trigger.directories)
        ]
        candidates.sort(key=lambda issue: issue.created_at)

        events: list[TriggerEvent] = []
        max_created_at = cursor
        for issue in candidates:
            if cursor is not None and issue.created_at <= cursor:
                continue
            occurred_at = issue.created_at.astimezone(UTC)
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{issue.issue_id}:{occurred_at.isoformat()}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload={"ticket": issue.model_dump(mode="json")},
                )
            )
            if max_created_at is None or occurred_at > max_created_at:
                max_created_at = occurred_at

        if max_created_at is not None:
            cursor_store.put(
                trigger.id, {"last_created_at": max_created_at.isoformat().replace("+00:00", "Z")}
            )
        return events

    def _trigger(self) -> TicketCreatedTriggerDefinition:
        if not isinstance(self.trigger_definition, TicketCreatedTriggerDefinition):
            raise RuntimeError(
                "TicketCreatedTriggerProvider initialized with a non-ticket-created trigger definition"
            )
        return self.trigger_definition

    def _runtime_dir(self):
        if self.runtime_context is None:
            raise RuntimeError("TicketCreatedTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / "trigger-cursors-ticket-created.json")


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
