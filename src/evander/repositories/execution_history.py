from __future__ import annotations

import base64
import hashlib
import json
import math
import sqlite3
import zlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from evander.approval_diagnostics import DEFAULT_SENSITIVE_PAYLOAD_MARKERS
from evander.models import DirectoryPath
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    datetime_to_storage,
    ensure_orchestration_schema,
    json_dumps,
    json_loads_object,
)

EXECUTION_HISTORY_BACKFILL_BATCH_SIZE = 250
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_MARKERS = tuple(
    dict.fromkeys(
        (
            *(marker for marker in DEFAULT_SENSITIVE_PAYLOAD_MARKERS if marker != "pat"),
            "api_key",
            "apikey",
            "access_token",
            "refresh_token",
            "passwd",
            "pwd",
            "secret",
            "credential",
        )
    )
)
_SENSITIVE_KEY_SEGMENTS = frozenset({"pat", "pwd"})
_NUMERIC_OPERATORS = {"gt", "gte", "lt", "lte"}
_MAX_EXACT_FLOAT_INTEGER = (1 << 53) - 1


def _execution_key(kind: str, execution_id: str) -> str:
    return f"{kind}:{execution_id}"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().casefold().replace("-", "_")
    segments = frozenset(segment for segment in normalized.split("_") if segment)
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS) or bool(
        segments & _SENSITIVE_KEY_SEGMENTS
    )


def sanitize_execution_payload(value: Any) -> Any:
    """Remove secret-like values while retaining a useful queryable shape."""
    if isinstance(value, dict):
        return {
            str(key): _REDACTED if _is_sensitive_key(str(key)) else sanitize_execution_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_execution_payload(item) for item in value]
    return value


def canonical_execution_payload(value: Any, *, strip_node_outputs: bool = False) -> Any:
    if not isinstance(value, dict):
        return {}
    payload = dict(value)
    if strip_node_outputs:
        payload.pop("__node_outputs", None)
    return sanitize_execution_payload(payload)


def _store_payload_blob(connection: sqlite3.Connection, payload: Any) -> str:
    raw = json_dumps(payload).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    compressed = zlib.compress(raw, level=6)
    connection.execute(
        """
        INSERT OR IGNORE INTO workflow_execution_payload_blobs(
            sha256, codec, original_bytes, compressed_bytes, payload_blob, created_at
        ) VALUES (?, 'zlib', ?, ?, ?, ?)
        """,
        (
            digest,
            len(raw),
            len(compressed),
            compressed,
            datetime_to_storage(datetime.now(UTC)),
        ),
    )
    return digest


def _read_payload_blob(connection: sqlite3.Connection, digest: str | None) -> dict[str, Any]:
    if not digest:
        return {}
    row = connection.execute(
        """
        SELECT codec, payload_blob
        FROM workflow_execution_payload_blobs
        WHERE sha256 = ?
        """,
        (digest,),
    ).fetchone()
    if row is None or row["codec"] != "zlib":
        return {}
    try:
        decoded = zlib.decompress(bytes(row["payload_blob"]))
        value = json.loads(decoded.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _escape_json_pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _iter_object_scalars(
    value: Any,
    *,
    pointer: str = "",
    depth: int = 0,
) -> Iterable[tuple[str, Any]]:
    if depth > 32:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            child_pointer = f"{pointer}/{_escape_json_pointer_segment(str(key))}"
            if len(child_pointer) > 1024:
                continue
            if isinstance(item, dict):
                yield from _iter_object_scalars(item, pointer=child_pointer, depth=depth + 1)
            elif isinstance(item, list):
                # Arrays remain available in the compressed detail payload but
                # deliberately have no v1 element predicates.
                continue
            else:
                yield child_pointer, item


def _scalar_columns(value: Any) -> tuple[str, str | None, str | None, float | None, int | None]:
    if value is None:
        return "null", None, None, None, None
    if isinstance(value, bool):
        return (
            "boolean",
            hashlib.sha256(json_dumps(value).encode()).hexdigest(),
            None,
            None,
            int(value),
        )
    if isinstance(value, (int, float)):
        try:
            numeric_value = float(value)
        except (OverflowError, ValueError):
            numeric_value = None
        if numeric_value is not None and not math.isfinite(numeric_value):
            numeric_value = None
        return (
            "number",
            hashlib.sha256(json_dumps(value).encode()).hexdigest(),
            _canonical_number_text(value),
            numeric_value,
            None,
        )
    text = str(value)
    return (
        "string",
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text[:512],
        None,
        None,
    )


def _canonical_number_text(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return json_dumps(value)
    decimal_value = Decimal(str(value))
    if decimal_value.is_zero():
        return "0"
    return format(decimal_value.normalize(), "f")


def _numeric_fallback_is_exact(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return abs(value) <= _MAX_EXACT_FLOAT_INTEGER
    return math.isfinite(value) and abs(value) <= _MAX_EXACT_FLOAT_INTEGER


def _replace_scalar_index(
    connection: sqlite3.Connection,
    *,
    execution_key: str,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any],
) -> None:
    connection.execute(
        "DELETE FROM workflow_node_execution_history_values WHERE execution_key = ?",
        (execution_key,),
    )
    rows: list[tuple[Any, ...]] = []
    for location, payload in (("input", input_payload), ("output", output_payload)):
        for pointer, value in _iter_object_scalars(payload):
            value_type, value_hash, text_value, numeric_value, boolean_value = _scalar_columns(
                value
            )
            rows.append(
                (
                    execution_key,
                    location,
                    pointer,
                    value_type,
                    value_hash,
                    text_value,
                    numeric_value,
                    boolean_value,
                )
            )
    connection.executemany(
        """
        INSERT INTO workflow_node_execution_history_values(
            execution_key, location, json_pointer, value_type, value_hash,
            text_value, numeric_value, boolean_value
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def archive_execution_record(
    connection: sqlite3.Connection,
    *,
    kind: str,
    record: Mapping[str, Any],
) -> str | None:
    if kind == "trigger":
        execution_id = str(record.get("event_id") or "").strip()
        node_id = str(record.get("trigger_id") or "").strip()
        node_type = str(record.get("trigger_type") or "").strip()
        # Completion events are transported through trigger_events too, but
        # their effect/gate node already has an execution record. Archiving the
        # transport envelope would show one node execution twice.
        if not node_type.startswith("trigger:"):
            return None
        status = str(record.get("delivery_state") or "unknown")
        started_at = record.get("occurred_at")
        finished_at = record.get("delivered_at") or record.get("inflight_at") or started_at
        input_payload: dict[str, Any] = {}
        output_payload = canonical_execution_payload(record.get("payload"))
        published_event_id = execution_id
        config_revision = None
        config_hash = None
    elif kind in {"effect", "gate"}:
        execution_id = str(record.get("execution_id") or "").strip()
        node_id_field = "effect_id" if kind == "effect" else "gate_id"
        node_type_field = "effect_type" if kind == "effect" else "gate_type"
        node_id = str(record.get(node_id_field) or "").strip()
        node_type = str(record.get(node_type_field) or "").strip()
        status = str(record.get("status") or "unknown")
        started_at = record.get("started_at")
        finished_at = record.get("finished_at") or started_at
        input_payload = canonical_execution_payload(
            record.get("input_payload"), strip_node_outputs=True
        )
        output_payload = canonical_execution_payload(record.get("outputs"))
        published_event_id = record.get("published_event_id")
        config_revision = record.get("config_revision")
        config_hash = record.get("resolved_config_hash")
    else:
        raise ValueError(f"Unsupported execution history kind: {kind}")

    if not execution_id or not node_id or not node_type or started_at is None:
        raise ValueError(f"Incomplete {kind} execution record cannot be archived")
    execution_key = _execution_key(kind, execution_id)
    started_text = _storage_timestamp(started_at)
    finished_text = _storage_timestamp(finished_at) or started_text
    directory_path = _execution_directory_path(
        connection,
        node_id=node_id,
        config_revision=_optional_text(config_revision),
        record=record,
    )
    input_digest = _store_payload_blob(connection, input_payload)
    output_digest = _store_payload_blob(connection, output_payload)
    connection.execute(
        """
        INSERT INTO workflow_node_execution_history(
            execution_key, execution_kind, execution_id, event_id, trace_id,
            node_id, node_type, source_node_id, source_trigger_type, status,
            started_at, finished_at, config_revision, config_hash,
            directory_path, published_event_id, error, input_blob_sha256,
            output_blob_sha256, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(execution_key) DO UPDATE SET
            event_id = excluded.event_id,
            trace_id = excluded.trace_id,
            node_id = excluded.node_id,
            node_type = excluded.node_type,
            source_node_id = excluded.source_node_id,
            source_trigger_type = excluded.source_trigger_type,
            status = excluded.status,
            started_at = excluded.started_at,
            finished_at = excluded.finished_at,
            config_revision = excluded.config_revision,
            config_hash = excluded.config_hash,
            directory_path = excluded.directory_path,
            published_event_id = excluded.published_event_id,
            error = excluded.error,
            input_blob_sha256 = excluded.input_blob_sha256,
            output_blob_sha256 = excluded.output_blob_sha256,
            updated_at = excluded.updated_at
        """,
        (
            execution_key,
            kind,
            execution_id,
            str(record.get("event_id") or execution_id),
            _optional_text(record.get("trace_id")),
            node_id,
            node_type,
            _optional_text(record.get("source_node_id")),
            _optional_text(record.get("source_trigger_type")),
            status,
            started_text,
            finished_text,
            _optional_text(config_revision),
            _optional_text(config_hash),
            directory_path,
            _optional_text(published_event_id),
            _optional_text(record.get("error")),
            input_digest,
            output_digest,
            datetime_to_storage(datetime.now(UTC)),
        ),
    )
    _replace_scalar_index(
        connection,
        execution_key=execution_key,
        input_payload=input_payload,
        output_payload=output_payload,
    )
    _replace_source_provenance(
        connection,
        execution_key=execution_key,
        kind=kind,
        record=record,
    )
    return execution_key


def _replace_source_provenance(
    connection: sqlite3.Connection,
    *,
    execution_key: str,
    kind: str,
    record: Mapping[str, Any],
) -> None:
    connection.execute(
        "DELETE FROM workflow_node_execution_history_sources WHERE execution_key = ?",
        (execution_key,),
    )
    rows: list[tuple[str, str, str | None, str | None]] = []
    if kind == "gate":
        source_event_ids = record.get("input_event_ids_by_source")
        occurred_by_source = record.get("input_occurred_at_by_source")
        source_ids = set()
        inputs_by_source = record.get("inputs_by_source")
        if isinstance(inputs_by_source, dict):
            source_ids.update(str(value) for value in inputs_by_source)
        if isinstance(source_event_ids, dict):
            source_ids.update(str(value) for value in source_event_ids)
        for source_id in sorted(source_ids):
            rows.append(
                (
                    execution_key,
                    source_id,
                    _optional_text(
                        source_event_ids.get(source_id)
                        if isinstance(source_event_ids, dict)
                        else None
                    ),
                    _storage_timestamp(
                        occurred_by_source.get(source_id)
                        if isinstance(occurred_by_source, dict)
                        else None
                    ),
                )
            )
    source_node_id = _optional_text(record.get("source_node_id"))
    if source_node_id and all(row[1] != source_node_id for row in rows):
        rows.append((execution_key, source_node_id, _optional_text(record.get("event_id")), None))
    connection.executemany(
        """
        INSERT INTO workflow_node_execution_history_sources(
            execution_key, source_node_id, source_event_id, occurred_at
        ) VALUES (?, ?, ?, ?)
        """,
        rows,
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_directory_path(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return DirectoryPath(segments=text).as_posix()
    except ValueError:
        return None


def _snapshot_directory_path(
    connection: sqlite3.Connection,
    *,
    config_revision: str | None,
    node_id: str,
) -> str | None:
    if not config_revision:
        return None
    row = connection.execute(
        "SELECT payload_json FROM workflow_config_snapshots WHERE revision = ?",
        (config_revision,),
    ).fetchone()
    if row is None:
        return None
    snapshot = json_loads_object(row["payload_json"])
    graphs = snapshot.get("workflow_graphs")
    if not isinstance(graphs, list):
        return None
    paths: set[str] = set()
    for graph in graphs:
        if not isinstance(graph, dict):
            continue
        node_ids = graph.get("node_ids")
        if not isinstance(node_ids, list) or node_id not in {
            str(item or "").strip() for item in node_ids
        }:
            continue
        directory_path = _normalize_directory_path(graph.get("directory_path"))
        if directory_path:
            paths.add(directory_path)
    return next(iter(paths)) if len(paths) == 1 else None


def _execution_directory_path(
    connection: sqlite3.Connection,
    *,
    node_id: str,
    config_revision: str | None,
    record: Mapping[str, Any],
) -> str | None:
    return _normalize_directory_path(record.get("directory_path")) or _snapshot_directory_path(
        connection,
        config_revision=config_revision,
        node_id=node_id,
    )


def _storage_timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return None


def archive_records_for_retention(connection: sqlite3.Connection, *, cutoff: str) -> int:
    rows = connection.execute(
        """
        SELECT 'trigger' AS execution_kind, payload_json, occurred_at AS sort_at
        FROM trigger_events
        WHERE delivery_state = 'delivered'
          AND trigger_type LIKE 'trigger:%'
          AND COALESCE(delivered_at, occurred_at) < ?
        UNION ALL
        SELECT 'effect', payload_json, started_at
        FROM effect_executions
        WHERE (
            status IN ('succeeded', 'skipped', 'dropped')
            AND COALESCE(finished_at, started_at) < ?
        ) OR (
            status = 'failed'
            AND (dismissed_at IS NOT NULL OR cleared_at IS NOT NULL)
            AND COALESCE(dismissed_at, cleared_at, finished_at, started_at) < ?
        )
        UNION ALL
        SELECT 'gate', payload_json, started_at
        FROM gate_executions
        WHERE (
            status IN ('succeeded', 'skipped', 'dropped')
            AND COALESCE(finished_at, started_at) < ?
        ) OR (
            status = 'failed'
            AND (dismissed_at IS NOT NULL OR cleared_at IS NOT NULL)
            AND COALESCE(dismissed_at, cleared_at, finished_at, started_at) < ?
        )
        ORDER BY sort_at ASC
        """,
        (cutoff, cutoff, cutoff, cutoff, cutoff),
    ).fetchall()
    for row in rows:
        archive_execution_record(
            connection,
            kind=str(row["execution_kind"]),
            record=json_loads_object(row["payload_json"]),
        )
    return len(rows)


def run_execution_history_backfill(
    path: Path | str,
    *,
    batch_size: int = EXECUTION_HISTORY_BACKFILL_BATCH_SIZE,
) -> dict[str, Any]:
    if batch_size < 1 or batch_size > 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        state_row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key = ?",
            ("execution_history_backfill",),
        ).fetchone()
        state = json_loads_object(state_row["value_json"]) if state_row else {}
        if state.get("completed") is True:
            return state
        rows = connection.execute(
            """
            SELECT execution_kind, payload_json, sort_at, source_key
            FROM (
                SELECT 'trigger' AS execution_kind, t.payload_json, t.occurred_at AS sort_at,
                       'trigger:' || t.event_id AS source_key
                FROM trigger_events t
                WHERE t.trigger_type LIKE 'trigger:%'
                  AND NOT EXISTS (
                    SELECT 1 FROM workflow_node_execution_history h
                    WHERE h.execution_key = 'trigger:' || t.event_id
                )
                UNION ALL
                SELECT 'effect', e.payload_json, e.started_at,
                       'effect:' || e.execution_id
                FROM effect_executions e
                WHERE NOT EXISTS (
                    SELECT 1 FROM workflow_node_execution_history h
                    WHERE h.execution_key = 'effect:' || e.execution_id
                )
                UNION ALL
                SELECT 'gate', g.payload_json, g.started_at,
                       'gate:' || g.execution_id
                FROM gate_executions g
                WHERE NOT EXISTS (
                    SELECT 1 FROM workflow_node_execution_history h
                    WHERE h.execution_key = 'gate:' || g.execution_id
                )
            ) candidates
            ORDER BY sort_at ASC, source_key ASC
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()
        for row in rows:
            archive_execution_record(
                connection,
                kind=str(row["execution_kind"]),
                record=json_loads_object(row["payload_json"]),
            )
        completed = len(rows) < batch_size
        next_state = {
            "completed": completed,
            "archived": int(state.get("archived") or 0) + len(rows),
            "last_batch_size": len(rows),
            "updated_at": datetime_to_storage(datetime.now(UTC)),
        }
        connection.execute(
            """
            INSERT INTO orchestration_metadata(key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            ("execution_history_backfill", json_dumps(next_state)),
        )
        return next_state


def search_node_executions(
    path: Path | str,
    *,
    node_id: str,
    started_from: datetime,
    started_to: datetime,
    statuses: list[str],
    filters: list[Mapping[str, Any]],
    limit: int,
    cursor: str | None,
    full_retention_days: int,
    compact_retention_days: int,
) -> dict[str, Any]:
    ensure_orchestration_schema(path)
    where, params = _history_search_predicates(
        node_id=node_id,
        started_from=started_from,
        started_to=started_to,
        statuses=statuses,
        filters=filters,
    )
    cursor_value = _decode_cursor(cursor) if cursor else None
    page_where = list(where)
    page_params = list(params)
    if cursor_value:
        page_where.append("(h.started_at < ? OR (h.started_at = ? AND h.execution_key < ?))")
        page_params.extend([cursor_value[0], cursor_value[0], cursor_value[1]])
    base_from = "FROM workflow_node_execution_history h"
    with connect_orchestration_db(path) as connection:
        matched = int(
            connection.execute(
                f"SELECT COUNT(*) {base_from} WHERE {' AND '.join(where)}",
                params,
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT h.*,
                CASE h.execution_kind
                    WHEN 'trigger' THEN EXISTS(
                        SELECT 1 FROM trigger_events t WHERE t.event_id = h.execution_id
                    )
                    WHEN 'effect' THEN EXISTS(
                        SELECT 1 FROM effect_executions e WHERE e.execution_id = h.execution_id
                    )
                    WHEN 'gate' THEN EXISTS(
                        SELECT 1 FROM gate_executions g WHERE g.execution_id = h.execution_id
                    )
                    ELSE 0
                END AS full_record_available
            {base_from}
            WHERE {" AND ".join(page_where)}
            ORDER BY h.started_at DESC, h.execution_key DESC
            LIMIT ?
            """,
            [*page_params, limit + 1],
        ).fetchall()
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        items = [_history_row_payload(connection, row, filters=filters) for row in visible_rows]
        next_cursor = None
        if has_more and visible_rows:
            last = visible_rows[-1]
            next_cursor = _encode_cursor(str(last["started_at"]), str(last["execution_key"]))
        availability = _history_availability(
            connection,
            node_id=node_id,
            started_from=started_from,
            started_to=started_to,
            full_retention_days=full_retention_days,
            compact_retention_days=compact_retention_days,
        )
    return {
        "generated_at": datetime_to_storage(datetime.now(UTC)),
        "node_id": node_id,
        "started_from": datetime_to_storage(started_from),
        "started_to": datetime_to_storage(started_to),
        "matched": matched,
        "returned": len(items),
        "limit": limit,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "executions": items,
        "availability": availability,
    }


def list_node_execution_authorization_paths(
    path: Path | str,
    *,
    node_id: str,
    started_from: datetime,
    started_to: datetime,
) -> tuple[str, ...]:
    """Return every execution-time scope represented in a history query window.

    Rows created before directory provenance was recorded deliberately fall back
    to root so a directory-local grant cannot inherit access to legacy payloads.
    """
    ensure_orchestration_schema(path)
    where, params = _history_search_predicates(
        node_id=node_id,
        started_from=started_from,
        started_to=started_to,
        statuses=[],
        filters=[],
    )
    with connect_orchestration_db(path) as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT COALESCE(NULLIF(h.directory_path, ''), 'root') AS directory_path
            FROM workflow_node_execution_history h
            WHERE {" AND ".join(where)}
            ORDER BY directory_path
            """,
            params,
        ).fetchall()
    return tuple(str(row["directory_path"]) for row in rows)


def read_node_execution_authorization_path(
    path: Path | str,
    *,
    execution_key: str,
) -> str:
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        row = connection.execute(
            """
            SELECT COALESCE(NULLIF(directory_path, ''), 'root') AS directory_path
            FROM workflow_node_execution_history
            WHERE execution_key = ?
            """,
            (execution_key,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Workflow node execution not found: {execution_key}")
    return str(row["directory_path"])


def _history_search_predicates(
    *,
    node_id: str,
    started_from: datetime,
    started_to: datetime,
    statuses: list[str],
    filters: list[Mapping[str, Any]],
) -> tuple[list[str], list[Any]]:
    where = ["h.node_id = ?", "h.started_at >= ?", "h.started_at < ?"]
    params: list[Any] = [
        node_id,
        _storage_timestamp(started_from),
        _storage_timestamp(started_to),
    ]
    if statuses:
        where.append(f"h.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    for index, item in enumerate(filters):
        clause, clause_params = _filter_clause(item, alias=f"v{index}")
        where.append(clause)
        params.extend(clause_params)
    return where, params


def list_node_execution_fields(
    path: Path | str,
    *,
    node_id: str,
    started_from: datetime,
    started_to: datetime,
) -> dict[str, Any]:
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        rows = connection.execute(
            """
            SELECT v.location, v.json_pointer, v.value_type, COUNT(*) AS observed_count
            FROM workflow_node_execution_history_values v
            JOIN workflow_node_execution_history h
              ON h.execution_key = v.execution_key
            WHERE h.node_id = ? AND h.started_at >= ? AND h.started_at < ?
            GROUP BY v.location, v.json_pointer, v.value_type
            ORDER BY v.location, v.json_pointer, v.value_type
            LIMIT 1000
            """,
            (
                node_id,
                _storage_timestamp(started_from),
                _storage_timestamp(started_to),
            ),
        ).fetchall()
    return {
        "node_id": node_id,
        "fields": [dict(row) for row in rows],
    }


def read_node_execution_detail(path: Path | str, *, execution_key: str) -> dict[str, Any]:
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        row = connection.execute(
            "SELECT * FROM workflow_node_execution_history WHERE execution_key = ?",
            (execution_key,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Workflow node execution not found: {execution_key}")
        full_record = _full_record(connection, row)
        sources = [
            dict(item)
            for item in connection.execute(
                """
                SELECT source_node_id, source_event_id, occurred_at
                FROM workflow_node_execution_history_sources
                WHERE execution_key = ?
                ORDER BY source_node_id
                """,
                (execution_key,),
            ).fetchall()
        ]
        payload = _history_row_payload(connection, row, filters=[])
        payload.update(
            {
                "input": _read_payload_blob(connection, row["input_blob_sha256"]),
                "output": _read_payload_blob(connection, row["output_blob_sha256"]),
                "sources": sources,
                "record_tier": "full" if full_record is not None else "compact",
                "full_record": full_record,
            }
        )
        return payload


def _filter_clause(item: Mapping[str, Any], *, alias: str) -> tuple[str, list[Any]]:
    location = str(item.get("location") or "")
    pointer = str(item.get("path") or "")
    operator = str(item.get("operator") or "")
    base = (
        f"{alias}.execution_key = h.execution_key AND {alias}.location = ? "
        f"AND {alias}.json_pointer = ?"
    )
    params: list[Any] = [location, pointer]
    if operator == "exists":
        return (
            f"EXISTS (SELECT 1 FROM workflow_node_execution_history_values {alias} WHERE {base})",
            params,
        )
    if operator == "not_exists":
        return (
            f"NOT EXISTS (SELECT 1 FROM workflow_node_execution_history_values {alias} WHERE {base})",
            params,
        )
    value = item.get("value")
    value_type, value_hash, _text, numeric_value, boolean_value = _scalar_columns(value)
    comparison = ""
    if operator == "eq":
        comparison = f" AND {alias}.value_type = ?"
        params.append(value_type)
        if value_type == "null":
            pass
        elif value_type == "boolean":
            comparison += f" AND {alias}.boolean_value = ?"
            params.append(boolean_value)
        elif value_type == "number":
            comparison += (
                f" AND ({alias}.text_value = ? OR "
                f"({alias}.text_value IS NULL AND {alias}.value_hash = ?)"
            )
            params.extend([_canonical_number_text(value), value_hash])
            if _numeric_fallback_is_exact(value):
                comparison += f" OR ({alias}.text_value IS NULL AND {alias}.numeric_value = ?)"
                params.append(numeric_value)
            comparison += ")"
        else:
            comparison += f" AND {alias}.value_hash = ?"
            params.append(value_hash)
    elif operator in _NUMERIC_OPERATORS:
        sql_operator = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
        comparison = (
            f" AND {alias}.value_type = 'number' AND {alias}.numeric_value {sql_operator} ?"
        )
        params.append(numeric_value)
    return (
        f"EXISTS (SELECT 1 FROM workflow_node_execution_history_values {alias} WHERE {base}{comparison})",
        params,
    )


def _history_row_payload(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    filters: list[Mapping[str, Any]],
) -> dict[str, Any]:
    matched_values: dict[str, Any] = {}
    for item in filters:
        if item.get("operator") == "not_exists":
            continue
        value_row = connection.execute(
            """
            SELECT value_type, text_value, numeric_value, boolean_value
            FROM workflow_node_execution_history_values
            WHERE execution_key = ? AND location = ? AND json_pointer = ?
            """,
            (row["execution_key"], item.get("location"), item.get("path")),
        ).fetchone()
        if value_row is not None:
            matched_values[f"{item.get('location')}:{item.get('path')}"] = _indexed_value(value_row)
    keys = set(row.keys())
    return {
        "execution_key": row["execution_key"],
        "execution_kind": row["execution_kind"],
        "execution_id": row["execution_id"],
        "event_id": row["event_id"],
        "trace_id": row["trace_id"],
        "node_id": row["node_id"],
        "node_type": row["node_type"],
        "source_node_id": row["source_node_id"],
        "source_trigger_type": row["source_trigger_type"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "config_revision": row["config_revision"],
        "published_event_id": row["published_event_id"],
        "error": row["error"],
        "full_record_available": bool(row["full_record_available"])
        if "full_record_available" in keys
        else _full_record(connection, row) is not None,
        "matched_values": matched_values,
    }


def _indexed_value(row: sqlite3.Row) -> Any:
    if row["value_type"] == "null":
        return None
    if row["value_type"] == "boolean":
        return bool(row["boolean_value"])
    if row["value_type"] == "number":
        if row["text_value"] is not None:
            try:
                return json.loads(str(row["text_value"]))
            except json.JSONDecodeError:
                pass
        return row["numeric_value"]
    return row["text_value"]


def _full_record(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any] | None:
    table_and_column = {
        "trigger": ("trigger_events", "event_id"),
        "effect": ("effect_executions", "execution_id"),
        "gate": ("gate_executions", "execution_id"),
    }.get(str(row["execution_kind"]))
    if table_and_column is None:
        return None
    table, column = table_and_column
    source = connection.execute(
        f"SELECT payload_json FROM {table} WHERE {column} = ?",
        (row["execution_id"],),
    ).fetchone()
    return (
        sanitize_execution_payload(json_loads_object(source["payload_json"]))
        if source is not None
        else None
    )


def _history_availability(
    connection: sqlite3.Connection,
    *,
    node_id: str,
    started_from: datetime,
    started_to: datetime,
    full_retention_days: int,
    compact_retention_days: int,
) -> dict[str, Any]:
    bounds = connection.execute(
        """
        SELECT MIN(started_at) AS earliest, MAX(started_at) AS latest
        FROM workflow_node_execution_history
        WHERE node_id = ? AND started_at >= ? AND started_at < ?
        """,
        (
            node_id,
            _storage_timestamp(started_from),
            _storage_timestamp(started_to),
        ),
    ).fetchone()
    state_row = connection.execute(
        "SELECT value_json FROM orchestration_metadata WHERE key = ?",
        ("execution_history_backfill",),
    ).fetchone()
    return {
        "full_retention_days": full_retention_days,
        "compact_retention_days": compact_retention_days,
        "full_retention_boundary": datetime_to_storage(
            datetime.now(UTC) - timedelta(days=full_retention_days)
        ),
        "compact_retention_boundary": datetime_to_storage(
            datetime.now(UTC) - timedelta(days=compact_retention_days)
        ),
        "earliest_compact_execution": bounds["earliest"],
        "latest_compact_execution": bounds["latest"],
        "backfill": json_loads_object(state_row["value_json"]) if state_row else {},
    }


def _encode_cursor(started_at: str, execution_key: str) -> str:
    payload = json_dumps({"started_at": started_at, "execution_key": execution_key}).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        started_at = _storage_timestamp(str(payload["started_at"]))
        execution_key = str(payload["execution_key"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if not started_at or not execution_key:
        raise ValueError("cursor is invalid")
    return started_at, execution_key
