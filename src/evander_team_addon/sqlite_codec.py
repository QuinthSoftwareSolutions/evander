"""SQLite value codecs and command identity normalization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Callable, TypeVar

from .models import TeamOutboxStatus, TeamState

T = TypeVar("T")


class TeamIdempotencyConflictError(RuntimeError):
    def __init__(
        self,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
        existing_digest: str,
        attempted_digest: str,
    ) -> None:
        super().__init__(
            "idempotency key reused with a different normalized payload digest: "
            f"tool={tool_name!r}, caller={caller_identity!r}, "
            f"scope={scope_kind!r}:{scope_id!r}, request_id={request_id!r}"
        )
        self.tool_name = tool_name
        self.caller_identity = caller_identity
        self.scope_kind = scope_kind
        self.scope_id = scope_id
        self.request_id = request_id
        self.existing_digest = existing_digest
        self.attempted_digest = attempted_digest


def normalized_payload_digest(payload: Mapping[str, Any]) -> str:
    normalized = _json_normalize(dict(payload))
    return hashlib.sha256(_json_dumps(normalized).encode("utf-8")).hexdigest()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _state_content_payload(state: TeamState) -> dict[str, Any]:
    payload = state.model_dump(mode="json")
    for field_name in ("schema_version", "revision", "updated_at"):
        payload.pop(field_name, None)
    return payload


def _state_content_changed(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    ignored_fields = {"schema_version", "revision", "updated_at"}
    return any(
        previous.get(field_name) != current.get(field_name)
        for field_name in TeamState.model_fields
        if field_name not in ignored_fields
    )


def _replace_state_result(result: T, state: TeamState) -> T:
    if isinstance(result, TeamState):
        return state  # type: ignore[return-value]
    if isinstance(result, tuple) and result and isinstance(result[0], TeamState):
        return (state, *result[1:])  # type: ignore[return-value]
    return result


def _state_result_candidate(result: object, *, fallback: TeamState) -> TeamState:
    if isinstance(result, TeamState):
        return result
    if isinstance(result, tuple) and result and isinstance(result[0], TeamState):
        return result[0]
    return fallback


def _json_loads(value: str | None) -> Any:
    if value is None or value == "":
        return {}
    return json.loads(value)


def _json_normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_normalize(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_normalize(value.model_dump(mode="json"))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _bool(value: Any) -> bool:
    return bool(int(value or 0))


def _int_bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _optional_db_bool(value: Any) -> bool | None:
    return None if value is None else bool(int(value))


def _optional_bool_db(value: Any) -> int | None:
    return None if value is None else (1 if bool(value) else 0)


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _datetime_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = _required_text(value, "updated_at")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _idempotency_key_values(
    *,
    tool_name: str,
    caller_identity: str,
    scope_kind: str,
    scope_id: str,
    request_id: str,
) -> dict[str, str]:
    return {
        "tool_name": _required_text(tool_name, "tool_name"),
        "caller_identity": _required_text(caller_identity, "caller_identity"),
        "scope_kind": _required_text(scope_kind, "scope_kind"),
        "scope_id": _required_text(scope_id, "scope_id"),
        "request_id": _required_text(request_id, "request_id"),
    }


def _raise_idempotency_conflict(
    *,
    tool_name: str,
    caller_identity: str,
    scope_kind: str,
    scope_id: str,
    request_id: str,
    existing_digest: str,
    attempted_digest: str,
) -> None:
    raise TeamIdempotencyConflictError(
        tool_name=tool_name,
        caller_identity=caller_identity,
        scope_kind=scope_kind,
        scope_id=scope_id,
        request_id=request_id,
        existing_digest=existing_digest,
        attempted_digest=attempted_digest,
    )


def _outbox_status_value(status: TeamOutboxStatus | str) -> str:
    if isinstance(status, TeamOutboxStatus):
        return status.value
    return TeamOutboxStatus(_required_text(status, "status")).value


def _backfill_position_values(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    owner_column: str,
    owner_id: str,
    value_column: str,
    values: Sequence[object],
) -> None:
    existing = connection.execute(
        f"SELECT 1 FROM {table_name} WHERE {owner_column} = ? LIMIT 1",
        (owner_id,),
    ).fetchone()
    if existing is not None:
        return
    for position, value in enumerate(values):
        text = str(value).strip()
        if text:
            connection.execute(
                (
                    f"INSERT INTO {table_name}("
                    f"{owner_column}, position, {value_column}"
                    ") VALUES (?, ?, ?)"
                ),
                (owner_id, position, text),
            )


def _ensure_column(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    existing_columns = {
        str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")
    }
    if column_name in existing_columns:
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _insert_position_values(
    connection: sqlite3.Connection,
    table: str,
    owner_column: str,
    owner_id: str,
    value_column: str,
    values: list[Any],
) -> None:
    for position, value in enumerate(values):
        connection.execute(
            f"""
            INSERT INTO {table}({owner_column}, position, {value_column})
            VALUES (?, ?, ?)
            """,
            (owner_id, position, value),
        )


def _grouped_values(connection: sqlite3.Connection, query: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in connection.execute(query):
        grouped.setdefault(row["owner_id"], []).append(row["value"])
    return grouped


def _grouped_rows(
    connection: sqlite3.Connection,
    query: str,
    mapper: Callable[[sqlite3.Row], dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute(query):
        grouped.setdefault(row["owner_id"], []).append(mapper(row))
    return grouped
