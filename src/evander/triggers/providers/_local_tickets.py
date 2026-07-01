from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from evander.events.models import (
    TriggerDefinitionDiscriminated,
    materialize_node_definition,
)
from evander.models import LocalIssueRecord
from evander.storage import JsonObjectStore, JsonStore
from evander.triggers.providers.schemas import TicketCreatedTriggerDefinition

LOCAL_ISSUES_FILENAME = "local-issues.json"
TICKET_CREATED_CURSOR_FILENAME = "trigger-cursors-ticket-created.json"
TICKET_CHANGED_CURSOR_FILENAME = "trigger-cursors-ticket-changed.json"
TICKET_CREATED_CURSOR_FIELD = "last_created_at"
TICKET_CHANGED_CURSOR_FIELD = "last_updated_at"
RECENT_ISSUE_IDS_FIELD = "recent_issue_ids"
RECENT_ISSUE_LIMIT = 512

_TICKET_CURSOR_FILENAMES: dict[str, str] = {
    "created": TICKET_CREATED_CURSOR_FILENAME,
    "changed": TICKET_CHANGED_CURSOR_FILENAME,
}
_TRIGGER_DEFINITION_ADAPTER = TypeAdapter(TriggerDefinitionDiscriminated)
_TicketCursorKind = Literal["created", "changed"]
_TicketTimestampField = Literal["created_at", "updated_at"]


@dataclass(frozen=True)
class _TicketCreatedTrigger:
    id: str
    directories: list[str]


def ticket_cursor_path(runtime_dir: Path, kind: _TicketCursorKind) -> Path:
    return runtime_dir / _ticket_cursor_filename(kind)


def ticket_cursor_store(runtime_dir: Path, kind: _TicketCursorKind) -> JsonObjectStore[object]:
    return JsonObjectStore(ticket_cursor_path(runtime_dir, kind), {})


def parse_ticket_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _normalize_ticket_datetime(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    return _normalize_ticket_datetime(parsed)


def format_ticket_timestamp(value: datetime) -> str:
    return _normalize_ticket_datetime(value).isoformat().replace("+00:00", "Z")


def within_ticket_directories(candidate: str, directories: list[str]) -> bool:
    normalized_candidate = str(candidate).strip().strip("/")
    if not directories:
        return True
    for directory in directories:
        normalized = str(directory).strip().strip("/")
        if not normalized:
            continue
        if normalized_candidate == normalized or normalized_candidate.startswith(f"{normalized}/"):
            return True
    return False


def read_local_ticket_issues(runtime_dir: Path) -> dict[str, LocalIssueRecord]:
    return JsonStore(runtime_dir / LOCAL_ISSUES_FILENAME, LocalIssueRecord).read_all()


def local_ticket_candidates(
    runtime_dir: Path,
    directories: list[str],
    timestamp_field: _TicketTimestampField,
) -> list[LocalIssueRecord]:
    candidates = [
        issue
        for issue in read_local_ticket_issues(runtime_dir).values()
        if within_ticket_directories(issue.directory_path, directories)
    ]
    candidates.sort(key=lambda issue: (_issue_timestamp(issue, timestamp_field), issue.issue_id))
    return candidates


def max_local_ticket_timestamp(
    issues: Iterable[LocalIssueRecord],
    directories: list[str],
    timestamp_field: _TicketTimestampField,
) -> datetime | None:
    max_timestamp: datetime | None = None
    for issue in issues:
        if not within_ticket_directories(issue.directory_path, directories):
            continue
        timestamp = _issue_timestamp(issue, timestamp_field)
        if max_timestamp is None or timestamp > max_timestamp:
            max_timestamp = timestamp
    return max_timestamp


def seed_new_ticket_created_trigger_cursors(
    *,
    runtime_dir: Path,
    previous_config: dict[str, object],
    next_config: dict[str, object],
) -> None:
    previous_trigger_ids = _ticket_created_trigger_ids(previous_config)
    new_triggers = [
        trigger
        for trigger in _ticket_created_triggers(next_config)
        if trigger.id not in previous_trigger_ids
    ]
    if not new_triggers:
        return

    issues = read_local_ticket_issues(runtime_dir)
    if not issues:
        return

    cursor_path = ticket_cursor_path(runtime_dir, "created")
    cursor_payload = (
        ticket_cursor_store(runtime_dir, "created").read_object()
        if cursor_path.exists()
        else {}
    )
    changed = False
    for trigger in new_triggers:
        if isinstance(cursor_payload.get(trigger.id), dict):
            continue
        max_created_at = max_local_ticket_timestamp(
            issues.values(),
            trigger.directories,
            "created_at",
        )
        if max_created_at is None:
            continue
        cursor_payload[trigger.id] = {
            TICKET_CREATED_CURSOR_FIELD: format_ticket_timestamp(max_created_at)
        }
        changed = True
    if changed:
        ticket_cursor_store(runtime_dir, "created").write_object(cursor_payload)


def _ticket_created_trigger_ids(config: dict[str, object]) -> set[str]:
    return {trigger.id for trigger in _ticket_created_triggers(config)}


def _ticket_created_triggers(config: dict[str, object]) -> list[_TicketCreatedTrigger]:
    triggers = config.get("triggers")
    if not isinstance(triggers, list):
        return []
    results: list[_TicketCreatedTrigger] = []
    for entry in triggers:
        if not isinstance(entry, dict):
            continue
        try:
            materialized = materialize_node_definition(
                _TRIGGER_DEFINITION_ADAPTER.validate_python(entry)
            )
        except ValidationError:
            continue
        if not isinstance(materialized, TicketCreatedTriggerDefinition):
            continue
        trigger_id = str(materialized.id).strip()
        if not trigger_id:
            continue
        results.append(
            _TicketCreatedTrigger(
                id=trigger_id,
                directories=[
                    str(directory).strip().strip("/")
                    for directory in materialized.directories
                    if str(directory).strip().strip("/")
                ],
            )
        )
    return results


def _ticket_cursor_filename(kind: _TicketCursorKind) -> str:
    try:
        return _TICKET_CURSOR_FILENAMES[kind]
    except KeyError as exc:
        raise ValueError(f"Unsupported ticket cursor kind: {kind!r}") from exc


def _issue_timestamp(issue: LocalIssueRecord, timestamp_field: _TicketTimestampField) -> datetime:
    timestamp = parse_ticket_timestamp(getattr(issue, timestamp_field))
    if timestamp is None:
        return datetime.min.replace(tzinfo=UTC)
    return timestamp


def _normalize_ticket_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
