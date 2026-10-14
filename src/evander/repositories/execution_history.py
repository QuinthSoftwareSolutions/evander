from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
import time
import zlib
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from heapq import merge
from itertools import islice
from pathlib import Path
from typing import Any, Mapping

from evander.approval_diagnostics import (
    DEFAULT_REDACTED_PAYLOAD_KEYS,
    DEFAULT_SENSITIVE_PAYLOAD_MARKERS,
)
from evander.models import DirectoryPath
from evander.repositories.maintenance_indexes import (
    RETENTION_SOURCE_INDEXES,
    MaintenanceIndexesRequired,
    retention_predicate,
)
from evander.repositories.history_index_storage import (
    HistoryIndexStorage,
    list_history_field_rows,
    scalar_gc_components,
    write_history_values,
)
from evander.repositories.operational_payloads import FORMAT_KEY, load_record
from evander.repositories.orchestration_db import (
    WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    _maintenance_indexes_ready,
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    datetime_to_storage,
    ensure_orchestration_schema,
    json_dumps,
    json_loads_object,
)
from evander.repositories.sqlite_runtime import (
    SqliteOperationDeadlineExceeded,
    is_storage_contention,
)

EXECUTION_HISTORY_BACKFILL_BATCH_SIZE = 250
EXECUTION_HISTORY_RETENTION_BATCH_SIZE = 250
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
_SENSITIVE_KEY_NAMES = frozenset(
    re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key))
    .casefold()
    .replace("-", "_")
    for key in DEFAULT_REDACTED_PAYLOAD_KEYS
)
_NUMERIC_OPERATORS = {"gt", "gte", "lt", "lte"}
_MAX_EXACT_FLOAT_INTEGER = (1 << 53) - 1


class _ContainerValue:
    def __init__(self, kind: str) -> None:
        self.kind = kind


def _execution_key(kind: str, execution_id: str) -> str:
    return f"{kind}:{execution_id}"


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip())
    normalized = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", normalized)
    normalized = normalized.casefold().replace("-", "_")
    segments = frozenset(segment for segment in normalized.split("_") if segment)
    return (
        normalized in _SENSITIVE_KEY_NAMES
        or any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)
        or bool(segments & _SENSITIVE_KEY_SEGMENTS)
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


@dataclass(frozen=True)
class PreparedHistoryPayload:
    payload: dict[str, Any]
    digest: str
    original_bytes: int
    compressed: bytes


def _prepare_history_payload(payload: dict[str, Any]) -> PreparedHistoryPayload:
    raw = json_dumps(payload).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return PreparedHistoryPayload(payload, digest, len(raw), zlib.compress(raw, level=1))


def prepare_execution_history_payloads(
    kind: str, record: Mapping[str, Any]
) -> tuple[PreparedHistoryPayload, PreparedHistoryPayload]:
    """Sanitize, hash, and compress before acquiring the operational writer."""
    inputs = (
        {}
        if kind == "trigger"
        else canonical_execution_payload(record.get("input_payload"), strip_node_outputs=True)
    )
    outputs = canonical_execution_payload(record.get("payload" if kind == "trigger" else "outputs"))
    return _prepare_history_payload(inputs), _prepare_history_payload(outputs)


def _store_payload_blob(connection: sqlite3.Connection, payload: Any) -> str:
    prepared = (
        payload
        if isinstance(payload, PreparedHistoryPayload)
        else _prepare_history_payload(payload)
    )
    digest = prepared.digest
    if (
        connection.execute(
            "SELECT 1 FROM workflow_execution_payload_blobs WHERE sha256 = ?", (digest,)
        ).fetchone()
        is not None
    ):
        return digest
    compressed = prepared.compressed
    connection.execute(
        """
        INSERT OR IGNORE INTO workflow_execution_payload_blobs(
            sha256, codec, original_bytes, compressed_bytes, payload_blob, created_at
        ) VALUES (?, 'zlib', ?, ?, ?, ?)
        """,
        (
            digest,
            prepared.original_bytes,
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


def _scalar_columns(value: Any) -> tuple[str, str | None, str | None, float | None, int | None]:
    if isinstance(value, _ContainerValue):
        return "container", None, value.kind, None, None
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


def archive_execution_record(
    connection: sqlite3.Connection,
    *,
    kind: str,
    record: Mapping[str, Any],
    prepared_payloads: tuple[PreparedHistoryPayload, PreparedHistoryPayload] | None = None,
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
        output_payload = (
            prepared_payloads[1].payload
            if prepared_payloads
            else canonical_execution_payload(record.get("payload"))
        )
        published_event_id = execution_id
        config_revision = record.get("config_revision")
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
        input_payload = (
            prepared_payloads[0].payload
            if prepared_payloads
            else canonical_execution_payload(record.get("input_payload"), strip_node_outputs=True)
        )
        output_payload = (
            prepared_payloads[1].payload
            if prepared_payloads
            else canonical_execution_payload(record.get("outputs"))
        )
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
    # Backfill and retention may arrive without an existing write transaction.
    # Keep reused blobs alive until the history row commits its references.
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    input_digest = _store_payload_blob(
        connection, prepared_payloads[0] if prepared_payloads else input_payload
    )
    output_digest = _store_payload_blob(
        connection, prepared_payloads[1] if prepared_payloads else output_payload
    )
    previous_payloads = connection.execute(
        "SELECT input_blob_sha256, output_blob_sha256 "
        "FROM workflow_node_execution_history WHERE execution_key = ?",
        (execution_key,),
    ).fetchone()
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
    # Status, publication and timing updates do not change the searchable
    # input/output values. Keep their existing index rows in the same transaction.
    if previous_payloads is None or tuple(previous_payloads) != (input_digest, output_digest):
        connection.execute(
            """INSERT INTO workflow_history_index_jobs(
                execution_key,input_blob_sha256,output_blob_sha256,enqueued_at,next_offset,cursor_json
            ) VALUES(?,?,?,?,0,NULL) ON CONFLICT(execution_key) DO UPDATE SET
                input_blob_sha256=excluded.input_blob_sha256,output_blob_sha256=excluded.output_blob_sha256,
                enqueued_at=excluded.enqueued_at,next_offset=0,cursor_json=NULL""",
            (execution_key, input_digest, output_digest, datetime_to_storage(datetime.now(UTC))),
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


@dataclass(frozen=True)
class PreparedHistoryIndex:
    job: dict[str, Any]
    generation: str
    rows: list[tuple[Any, ...]]
    cursor_json: str | None
    exhausted: bool


def _history_index_snapshot(connection: sqlite3.Connection):
    job = connection.execute("""SELECT * FROM workflow_history_index_jobs
        ORDER BY enqueued_at,execution_key LIMIT 1""").fetchone()
    if job is None:
        return None
    payloads = []
    for field in ("input_blob_sha256", "output_blob_sha256"):
        row = connection.execute(
            "SELECT codec,payload_blob FROM workflow_execution_payload_blobs WHERE sha256=?",
            (job[field],),
        ).fetchone()
        if row is None or row[0] != "zlib":
            raise ValueError("Missing execution history indexing payload")
        payloads.append(bytes(row[1]))
    return dict(job), payloads


def _iter_scalars_after(value, *, after=(), prefix=(), depth=0):
    """Seek through sorted object keys using the durable traversal path.

    Completed sibling subtrees are skipped without walking their descendants.
    Arrays are indexed for presence only, as in the original scalar contract.
    """
    if depth > 32 or not isinstance(value, dict):
        return
    keys = sorted(value)
    offset = (
        bisect_left(keys, after[len(prefix)])
        if len(after) > len(prefix) and after[: len(prefix)] == prefix
        else 0
    )
    for key in keys[offset:]:
        item = value[key]
        path = (*prefix, key)
        pointer = "/" + "/".join(_escape_json_pointer_segment(part) for part in path)
        if len(pointer) > 1024:
            continue
        if path > after:
            yield (
                path,
                pointer,
                _ContainerValue("object")
                if isinstance(item, dict)
                else _ContainerValue("array")
                if isinstance(item, list)
                else item,
            )
        if isinstance(item, dict) and (path >= after or after[: len(path)] == path):
            yield from _iter_scalars_after(item, after=after, prefix=path, depth=depth + 1)


def _prepare_history_index(snapshot, *, batch_size, time_budget_seconds):
    if snapshot is None:
        return None
    job, compressed = snapshot
    started = time.monotonic()
    generation = job["input_blob_sha256"] + ":" + job["output_blob_sha256"]
    cursor = json.loads(job["cursor_json"]) if job.get("cursor_json") else None

    def values():
        for location, blob in zip(("input", "output"), compressed, strict=True):
            if cursor and location < cursor[0]:
                continue
            after = tuple(cursor[1]) if cursor and location == cursor[0] else ()
            payload = json.loads(zlib.decompress(blob))
            for path, pointer, value in _iter_scalars_after(payload, after=after):
                yield (
                    (location, path),
                    (job["execution_key"], generation, location, pointer, *_scalar_columns(value)),
                )

    iterator = values()
    rows = []
    exhausted = False
    next_cursor = job.get("cursor_json")
    for _ in range(max(1, min(100, batch_size))):
        if rows and time.monotonic() - started >= time_budget_seconds:
            break
        item = next(iterator, None)
        if item is None:
            exhausted = True
            break
        cursor_value, row = item
        rows.append(row)
        next_cursor = json_dumps(cursor_value)
    return PreparedHistoryIndex(job, generation, rows, next_cursor, exhausted)


def prepare_pending_history_indexing(
    path: Path | str, *, batch_size: int = 100, time_budget_seconds: float = 0.25
) -> PreparedHistoryIndex | None:
    with connect_orchestration_db_read_only(
        path, deadline_seconds=0.25, operation="history_index_snapshot"
    ) as connection:
        connection.execute("BEGIN")
        snapshot = _history_index_snapshot(connection)
    # Decompression, traversal and scalar hashing hold no SQLite transaction.
    return _prepare_history_index(
        snapshot, batch_size=batch_size, time_budget_seconds=time_budget_seconds
    )


_UNPREPARED = object()


def run_pending_history_indexing(
    connection: sqlite3.Connection,
    *,
    batch_size: int = 100,
    time_budget_seconds: float = 0.25,
    prepared: PreparedHistoryIndex | None | object = _UNPREPARED,
) -> dict[str, int]:
    """Apply one prepared batch, exposing only a complete generation atomically.

    Production maintenance prepares outside this connection. The optional inline
    preparation path supports small explicit administrative batches and tests.
    """
    if prepared is _UNPREPARED:
        prepared = _prepare_history_index(
            _history_index_snapshot(connection),
            batch_size=batch_size,
            time_budget_seconds=time_budget_seconds,
        )
    if prepared is None:
        return {"indexed_values": 0, "completed": 0}
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    job = prepared.job
    key = job["execution_key"]
    current = connection.execute(
        """SELECT input_blob_sha256,output_blob_sha256,next_offset,cursor_json
        FROM workflow_history_index_jobs WHERE execution_key=?""",
        (key,),
    ).fetchone()
    if current is None or tuple(current) != (
        job["input_blob_sha256"],
        job["output_blob_sha256"],
        job["next_offset"],
        job.get("cursor_json"),
    ):
        return {"indexed_values": 0, "completed": 0}
    write_history_values(connection, prepared.rows)
    if prepared.exhausted:
        connection.execute(
            """INSERT INTO workflow_history_index_state VALUES(?,?)
            ON CONFLICT(execution_key) DO UPDATE SET generation=excluded.generation""",
            (key, prepared.generation),
        )
        connection.execute("DELETE FROM workflow_history_index_jobs WHERE execution_key=?", (key,))
    else:
        connection.execute(
            "UPDATE workflow_history_index_jobs SET next_offset=next_offset+?,cursor_json=? WHERE execution_key=?",
            (len(prepared.rows), prepared.cursor_json, key),
        )
    return {"indexed_values": len(prepared.rows), "completed": int(prepared.exhausted)}


def run_history_indexing_slice(
    path: Path | str,
    *,
    batch_size: int = 100,
    time_budget_seconds: float = 0.25,
    writer_time_budget_seconds: float = 0.1,
    busy_timeout_ms: int = 0,
) -> dict[str, int]:
    """Prepare outside the writer and durably adapt its bounded field batch.

    Execution timeouts roll back all values/cursor changes before reducing the
    limit. Writer contention leaves the limit unchanged. Fast, fully used
    batches recover their limit in the same transaction as the indexing work.
    """
    batch_cap = max(1, min(100, batch_size))
    with connect_orchestration_db_read_only(
        path, deadline_seconds=0.25, operation="history_index_limit"
    ) as connection:
        row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='history_index_batch_limit'"
        ).fetchone()
    expected_limit = row[0] if row else None
    limit = json_loads_object(expected_limit)
    effective_batch = min(batch_cap, max(1, int(limit.get("rows", 100))))
    prepared = prepare_pending_history_indexing(
        path, batch_size=effective_batch, time_budget_seconds=time_budget_seconds
    )
    if prepared is None:
        return {"indexed_values": 0, "completed": 0}
    started = time.monotonic()
    try:
        with connect_orchestration_db(
            path,
            busy_timeout_ms=busy_timeout_ms,
            deadline_seconds=writer_time_budget_seconds,
            operation="history_index_batch",
        ) as connection:
            indexed = run_pending_history_indexing(
                connection,
                batch_size=effective_batch,
                time_budget_seconds=time_budget_seconds,
                prepared=prepared,
            )
            if (
                expected_limit is not None
                and indexed["indexed_values"] >= effective_batch
                and effective_batch < batch_cap
                and time.monotonic() - started < writer_time_budget_seconds / 2
            ):
                recovered = {**limit, "rows": min(batch_cap, effective_batch * 2)}
                connection.execute(
                    """UPDATE orchestration_metadata SET value_json=?
                    WHERE key='history_index_batch_limit' AND value_json=?""",
                    (json_dumps(recovered), expected_limit),
                )
        return indexed
    except SqliteOperationDeadlineExceeded as error:
        if not is_storage_contention(error):
            # The failed indexing transaction has closed and rolled back. Read
            # the current limit so a concurrent reduction always wins.
            with connect_orchestration_db(
                path,
                busy_timeout_ms=busy_timeout_ms,
                deadline_seconds=writer_time_budget_seconds,
                operation="history_index_backoff",
            ) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT value_json FROM orchestration_metadata "
                    "WHERE key='history_index_batch_limit'"
                ).fetchone()
                current = json_loads_object(row[0]) if row else {}
                reduced = {
                    **current,
                    "rows": min(max(1, int(current.get("rows", 100))), max(1, effective_batch // 2)),
                }
                connection.execute(
                    """INSERT INTO orchestration_metadata VALUES ('history_index_batch_limit',?)
                    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                    (json_dumps(reduced),),
                )
        raise


def collect_superseded_history_values(
    connection: sqlite3.Connection, *, limit: int = 100, hold_completed: bool = False
) -> int:
    """Scan bounded candidate windows, preserving progress through live values.

    Maintenance can hold completed components until it resets the shared sweep.
    The default keeps the standalone collector's repeating-sweep behavior.
    """
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    limit = min(limit, 100)
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT value_json FROM orchestration_metadata WHERE key='history_scalar_gc_cursor'"
    ).fetchone()
    previous = json_loads_object(row[0]) if row else {}
    tables = scalar_gc_components(connection)
    progress = dict(previous) if hold_completed else {
        name: previous[name] for name, _, _ in tables if name in previous
    }
    deleted = 0
    for name, table, orphan in tables:
        completed_key = f"{name}_completed"
        deleted_key = f"{name}_deleted_in_sweep"
        if hold_completed and progress.get(completed_key) is True:
            continue
        candidates = [
            item[0]
            for item in connection.execute(
                f"SELECT rowid FROM {table} WHERE rowid>? ORDER BY rowid LIMIT ?",
                (int(progress.get(name, 0)), limit),
            ).fetchall()
        ]
        component_deleted = 0
        if candidates:
            placeholders = ",".join("?" for _ in candidates)
            cursor = connection.execute(
                f"DELETE FROM {table} AS v WHERE rowid IN ({placeholders}) AND {orphan}",
                candidates,
            )
            component_deleted = cursor.rowcount
            deleted += component_deleted
        progress[name] = candidates[-1] if len(candidates) == limit else 0
        if hold_completed:
            deleted_in_sweep = bool(progress.get(deleted_key) or component_deleted)
            exhausted = len(candidates) < limit
            # A completed component waits for its peers. A sweep that deleted
            # values gets one clean follow-up before it can become idle.
            progress[completed_key] = exhausted and not deleted_in_sweep
            progress[deleted_key] = deleted_in_sweep and not exhausted
    if progress != previous and (row is not None or any(progress.values())):
        connection.execute(
            """INSERT INTO orchestration_metadata VALUES ('history_scalar_gc_cursor',?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
            (json_dumps(progress),),
        )
    return deleted


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
        """
        SELECT CASE WHEN json_valid(payload_json) THEN
            CASE WHEN json_type(payload_json, '$.workflow_graphs') = 'array'
                THEN json_extract(payload_json, '$.workflow_graphs') END
            END AS graphs_json
        FROM workflow_config_snapshots WHERE revision = ?
        """,
        (config_revision,),
    ).fetchone()
    if row is None or row["graphs_json"] is None:
        return None
    # The rest of a snapshot can contain megabytes of prompts and provider
    # config. Only graph membership is needed to preserve historical scope.
    graphs = json.loads(row["graphs_json"])
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


RETENTION_SOURCE_BYTES = 1024 * 1024
_RETENTION_SOURCE_TABLES = {
    "trigger": ("trigger_events", "event_id"),
    "transport": ("trigger_events", "event_id"),
    "effect": ("effect_executions", "execution_id"),
    "gate": ("gate_executions", "execution_id"),
}


@dataclass(frozen=True)
class PreparedRetentionSource:
    kind: str
    source_id: str
    payload_json: str
    record: dict[str, Any]
    payloads: tuple[PreparedHistoryPayload, PreparedHistoryPayload] | None


@dataclass(frozen=True)
class PreparedRetentionBatch:
    sources: tuple[PreparedRetentionSource, ...]
    payload_bytes: int


def _retention_candidates(connection, *, cutoff, batch_size):
    if not _maintenance_indexes_ready(connection):
        raise MaintenanceIndexesRequired("Maintenance indexes must be built offline before retention")
    streams = [
        connection.execute(
            source.selection_sql, {"cutoff": cutoff, "limit": batch_size}
        ).fetchall()
        for source in RETENTION_SOURCE_INDEXES
    ]
    # Each range yields at most N metadata rows. The union's oldest N must be
    # among these prefixes; hydrate payloads only after the bounded merge.
    return list(islice(merge(
        *streams, key=lambda row: (row["eligibility_at"], row["source_id"], row["execution_kind"])
    ), batch_size))


def _source_payload_bytes(connection, *, kind, source_id):
    table, key = _RETENTION_SOURCE_TABLES[kind]
    size = connection.execute(
        f"SELECT length(CAST(payload_json AS BLOB)) FROM {table} WHERE {key}=?",
        (source_id,),
    ).fetchone()[0]
    if kind != "transport":
        # Drive lookups from reachable keys. Reordering this join can make
        # SQLite scan every stored blob even when this source has no references.
        size += connection.execute(
            """WITH RECURSIVE reachable(sha256) AS (
            SELECT sha256 FROM workflow_operational_record_refs
            WHERE record_kind=? AND record_id=?
            UNION SELECT e.child_sha256 FROM workflow_operational_blob_edges e
            JOIN reachable r ON e.parent_sha256=r.sha256)
            SELECT COALESCE(SUM(b.original_bytes),0) FROM reachable r
            CROSS JOIN workflow_operational_blobs b ON b.sha256=r.sha256""",
            (kind, source_id),
        ).fetchone()[0]
    return size


def _read_retention_sources(connection, *, cutoff, batch_size, max_payload_bytes):
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    if max_payload_bytes < 1:
        raise ValueError("max_payload_bytes must be positive")
    candidates = _retention_candidates(connection, cutoff=cutoff, batch_size=batch_size)
    selected = []
    total_bytes = 0
    for candidate in candidates:
        kind, source_id = candidate["execution_kind"], candidate["source_id"]
        table, key = _RETENTION_SOURCE_TABLES[kind]
        # Include reachable uncompressed blobs so reference envelopes cannot
        # evade the same byte budget as legacy inline payloads.
        size = _source_payload_bytes(connection, kind=kind, source_id=source_id)
        if selected and total_bytes + size > max_payload_bytes:
            break
        raw = connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key}=?", (source_id,)
        ).fetchone()[0]
        selected.append(
            (kind, source_id, raw, {} if kind == "transport" else load_record(connection, raw))
        )
        total_bytes += size
        # One oversized source is allowed alone, so it can eventually retire.
        if total_bytes >= max_payload_bytes:
            break
    return selected, total_bytes


def _prepare_retention_sources(snapshot) -> PreparedRetentionBatch:
    rows, payload_bytes = snapshot
    return PreparedRetentionBatch(
        tuple(
            PreparedRetentionSource(
                kind,
                source_id,
                raw,
                record,
                None if kind == "transport" else prepare_execution_history_payloads(kind, record),
            )
            for kind, source_id, raw, record in rows
        ),
        payload_bytes,
    )


def prepare_records_for_retention(
    path: Path | str,
    *,
    cutoff: str,
    batch_size: int = EXECUTION_HISTORY_RETENTION_BATCH_SIZE,
    max_payload_bytes: int = RETENTION_SOURCE_BYTES,
) -> PreparedRetentionBatch:
    """Read a byte-bounded snapshot, then sanitize and compress with no DB writer."""
    with connect_orchestration_db_read_only(
        path, deadline_seconds=0.25, operation="retention_source_snapshot"
    ) as connection:
        connection.execute("BEGIN")
        snapshot = _read_retention_sources(
            connection, cutoff=cutoff, batch_size=batch_size, max_payload_bytes=max_payload_bytes
        )
    return _prepare_retention_sources(snapshot)


def archive_records_for_retention(
    connection: sqlite3.Connection,
    *,
    cutoff: str,
    batch_size: int = EXECUTION_HISTORY_RETENTION_BATCH_SIZE,
    prepared: PreparedRetentionBatch | None = None,
) -> list[tuple[str, str]]:
    """Archive unchanged eligible sources and return keys safe to delete atomically.

    Production callers prepare before acquiring the writer. The inline fallback
    supports explicit small administrative batches using the same byte cap.
    """
    if prepared is None:
        prepared = _prepare_retention_sources(
            _read_retention_sources(
                connection,
                cutoff=cutoff,
                batch_size=batch_size,
                max_payload_bytes=RETENTION_SOURCE_BYTES,
            )
        )
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    safe = []
    for source in prepared.sources:
        table, key = _RETENTION_SOURCE_TABLES[source.kind]
        current = connection.execute(
            f"SELECT 1 FROM {table} WHERE {key}=:id AND payload_json=:payload "
            f"AND {retention_predicate(source.kind)}",
            {"id": source.source_id, "payload": source.payload_json, "cutoff": cutoff},
        ).fetchone()
        if current is None:
            continue
        if source.kind != "transport":
            archive_execution_record(
                connection,
                kind=source.kind,
                record=source.record,
                prepared_payloads=source.payloads,
            )
            if (
                connection.execute(
                    "SELECT 1 FROM workflow_history_index_jobs WHERE execution_key=?",
                    (_execution_key(source.kind, source.source_id),),
                ).fetchone()
                is not None
            ):
                continue
        safe.append((source.kind, source.source_id))
    return safe


def run_execution_history_backfill(
    path: Path | str,
    *,
    batch_size: int = EXECUTION_HISTORY_BACKFILL_BATCH_SIZE,
    busy_timeout_ms: int = 100,
) -> dict[str, Any]:
    """Copy a byte-bounded legacy batch, preparing payloads before the writer.

    The durable cursor only advances over unchanged, archived, or deleted rows.
    A changed source without history is retried instead of publishing stale data.
    """
    if batch_size < 1 or batch_size > 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    ensure_orchestration_schema(path)
    with connect_orchestration_db_read_only(
        path, deadline_seconds=0.25, operation="history_backfill_snapshot"
    ) as connection:
        connection.execute("BEGIN")
        state_row = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='execution_history_backfill'"
        ).fetchone()
        expected_state = state_row[0] if state_row else None
        state = json_loads_object(expected_state) if expected_state else {}
        if state.get("completed") is True:
            return state
        last_sort_at = _optional_text(state.get("last_sort_at"))
        last_source_key = _optional_text(state.get("last_source_key"))
        selections = []
        for kind in ("trigger", "effect", "gate"):
            table, key = _RETENTION_SOURCE_TABLES[kind]
            timestamp = "occurred_at" if kind == "trigger" else "started_at"
            trigger_only = "source.trigger_type LIKE 'trigger:%' AND " if kind == "trigger" else ""
            selections.append(f"""SELECT '{kind}' execution_kind,source.{key} source_id,
                source.{timestamp} sort_at,'{kind}:' || source.{key} source_key FROM {table} source
                WHERE {trigger_only}(:last_sort_at IS NULL OR source.{timestamp}>:last_sort_at
                    OR (source.{timestamp}=:last_sort_at AND '{kind}:' || source.{key}>:last_source_key))
                AND NOT EXISTS(SELECT 1 FROM workflow_node_execution_history h
                    WHERE h.execution_key='{kind}:' || source.{key})""")
        candidates = connection.execute(
            " UNION ALL ".join(selections) + " ORDER BY sort_at,source_key LIMIT :limit",
            {"last_sort_at": last_sort_at, "last_source_key": last_source_key, "limit": batch_size},
        ).fetchall()
        rows = []
        payload_bytes = 0
        for row in candidates:
            kind, source_id = row["execution_kind"], row["source_id"]
            size = _source_payload_bytes(connection, kind=kind, source_id=source_id)
            if rows and payload_bytes + size > RETENTION_SOURCE_BYTES:
                break
            table, key = _RETENTION_SOURCE_TABLES[kind]
            raw = connection.execute(
                f"SELECT payload_json FROM {table} WHERE {key}=?",
                (source_id,),
            ).fetchone()[0]
            # Legacy JSON parsing can also be expensive. It needs no read
            # snapshot; only external operational references need hydration here.
            record = load_record(connection, raw) if FORMAT_KEY in raw else None
            rows.append((dict(row), raw, record))
            payload_bytes += size
            if payload_bytes >= RETENTION_SOURCE_BYTES:
                break
        exhausted = len(candidates) < batch_size and len(rows) == len(candidates)
    prepared = []
    for row, raw, record in rows:
        record = json.loads(raw) if record is None else record
        prepared.append(
            (row, raw, record, prepare_execution_history_payloads(row["execution_kind"], record))
        )
    with connect_orchestration_db(
        path,
        busy_timeout_ms=busy_timeout_ms,
        deadline_seconds=0.25,
        operation="history_backfill",
    ) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current_state = connection.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='execution_history_backfill'"
        ).fetchone()
        if (current_state[0] if current_state else None) != expected_state:
            return json_loads_object(current_state[0]) if current_state else {}
        archived = 0
        completed = exhausted
        for row, raw, record, payloads in prepared:
            kind, source_id = row["execution_kind"], row["source_id"]
            table, key = _RETENTION_SOURCE_TABLES[kind]
            timestamp = "occurred_at" if kind == "trigger" else "started_at"
            existing = connection.execute(
                "SELECT 1 FROM workflow_node_execution_history WHERE execution_key=?",
                (row["source_key"],),
            ).fetchone()
            if existing is None:
                trigger_only = " AND trigger_type LIKE 'trigger:%'" if kind == "trigger" else ""
                unchanged = connection.execute(
                    f"SELECT 1 FROM {table} WHERE {key}=? AND payload_json=? AND {timestamp}=?{trigger_only}",
                    (source_id, raw, row["sort_at"]),
                ).fetchone()
                if unchanged is None:
                    current = connection.execute(
                        f"SELECT {timestamp} FROM {table} WHERE {key}=?",
                        (source_id,),
                    ).fetchone()
                    if current is not None:
                        # Changed payloads retry from the prior cursor. A changed
                        # ordering key requires a fresh metadata scan as well.
                        if current[0] != row["sort_at"]:
                            last_sort_at = last_source_key = None
                        completed = False
                        break
                else:
                    archive_execution_record(
                        connection, kind=kind, record=record, prepared_payloads=payloads
                    )
                    archived += 1
            last_sort_at = row["sort_at"]
            last_source_key = row["source_key"]
        next_state = {
            "completed": completed,
            "archived": int(state.get("archived") or 0) + archived,
            "last_batch_size": archived,
            "last_sort_at": last_sort_at,
            "last_source_key": last_source_key,
            "updated_at": datetime_to_storage(datetime.now(UTC)),
        }
        connection.execute(
            """INSERT INTO orchestration_metadata(key,value_json)
            VALUES ('execution_history_backfill',?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
            (json_dumps(next_state),),
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
    include_total: bool = True,
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
    with connect_orchestration_db_read_only(
        path,
        deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    ) as connection:
        connection.execute("BEGIN")
        storage = (
            HistoryIndexStorage(connection, (str(item.get("path") or "") for item in filters))
            if filters
            else None
        )
        if storage is not None and storage.interned:
            where, params = _history_search_predicates(
                node_id=node_id,
                started_from=started_from,
                started_to=started_to,
                statuses=statuses,
                filters=filters,
                storage=storage,
            )
            page_where, page_params = list(where), list(params)
            if cursor_value:
                page_where.append(
                    "(h.started_at < ? OR (h.started_at = ? AND h.execution_key < ?))"
                )
                page_params.extend([cursor_value[0], cursor_value[0], cursor_value[1]])
            base_from += storage.search_joins
        _validate_range_filters(
            connection,
            node_id=node_id,
            started_from=started_from,
            started_to=started_to,
            statuses=statuses,
            filters=filters,
            storage=storage,
        )
        matched = None
        if include_total:
            matched = int(
                connection.execute(
                    f"SELECT COUNT(*) {base_from} WHERE {' AND '.join(where)}",
                    params,
                ).fetchone()[0]
            )
        rows = connection.execute(
            f"""
            SELECT h.*{storage.search_columns if storage is not None else ""},
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
        items = [
            _history_row_payload(connection, row, filters=filters, storage=storage)
            for row in visible_rows
        ]
        next_cursor = None
        if has_more and visible_rows:
            last = visible_rows[-1]
            next_cursor = _encode_cursor(str(last["started_at"]), str(last["execution_key"]))
        indexing_pending_count = connection.execute(
            """SELECT COUNT(*) FROM workflow_history_index_jobs j
                JOIN workflow_node_execution_history h ON h.execution_key=j.execution_key
                WHERE h.node_id=? AND h.started_at>=? AND h.started_at<?""",
            (node_id, _storage_timestamp(started_from), _storage_timestamp(started_to)),
        ).fetchone()[0]
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
        "indexing_complete": indexing_pending_count == 0,
        "indexing_pending_count": indexing_pending_count,
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
    with connect_orchestration_db_read_only(
        path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    ) as connection:
        connection.execute("BEGIN")
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
    with connect_orchestration_db_read_only(
        path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    ) as connection:
        connection.execute("BEGIN")
        row = connection.execute(
            """
            SELECT COALESCE(NULLIF(directory_path, ''), 'root') AS directory_path
            FROM workflow_node_execution_history
            WHERE execution_key = ?
              AND NOT EXISTS (SELECT 1 FROM workflow_history_retirements r
                  WHERE r.execution_key=workflow_node_execution_history.execution_key)
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
    storage: HistoryIndexStorage | None = None,
) -> tuple[list[str], list[Any]]:
    where = ["h.node_id = ?", "h.started_at >= ?", "h.started_at < ?",
             "NOT EXISTS (SELECT 1 FROM workflow_history_retirements r "
             "WHERE r.execution_key=h.execution_key)"]
    params: list[Any] = [
        node_id,
        _storage_timestamp(started_from),
        _storage_timestamp(started_to),
    ]
    if statuses:
        where.append(f"h.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if filters:
        where.append(
            "NOT EXISTS (SELECT 1 FROM workflow_history_index_jobs j WHERE j.execution_key=h.execution_key)"
        )
    for index, item in enumerate(filters):
        clause, clause_params = _filter_clause(item, alias=f"v{index}", storage=storage)
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
    with connect_orchestration_db_read_only(
        path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    ) as connection:
        connection.execute("BEGIN")
        rows = list_history_field_rows(
            connection, node_id, _storage_timestamp(started_from), _storage_timestamp(started_to)
        )
    return {
        "node_id": node_id,
        "fields": [dict(row) for row in rows],
    }


def read_node_execution_detail(path: Path | str, *, execution_key: str) -> dict[str, Any]:
    ensure_orchestration_schema(path)
    with connect_orchestration_db_read_only(
        path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    ) as connection:
        connection.execute("BEGIN")
        row = connection.execute(
            """SELECT * FROM workflow_node_execution_history WHERE execution_key = ?
               AND NOT EXISTS (SELECT 1 FROM workflow_history_retirements r
                   WHERE r.execution_key=workflow_node_execution_history.execution_key)""",
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


def _filter_clause(item: Mapping[str, Any], *, alias: str,
                   storage: HistoryIndexStorage | None = None) -> tuple[str, list[Any]]:
    location = str(item.get("location") or "")
    pointer = str(item.get("path") or "")
    operator = str(item.get("operator") or "")
    base = (
        f"{alias}.execution_key = h.execution_key AND {alias}.location = ? "
        f"AND {alias}.json_pointer = ?"
    )
    params: list[Any] = [location, pointer]
    if storage is not None and storage.interned and operator in {"exists", "not_exists"}:
        predicate, direct_params = storage.exists_sql(alias, location, pointer, "")
        return (f"NOT ({predicate})" if operator == "not_exists" else predicate), direct_params
    if operator == "exists":
        return (
            f"EXISTS (SELECT 1 FROM workflow_node_execution_current_values {alias} WHERE {base})",
            params,
        )
    if operator == "not_exists":
        return (
            f"NOT EXISTS (SELECT 1 FROM workflow_node_execution_current_values {alias} WHERE {base})",
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
            f" AND {alias}.value_type = 'number' "
            f"AND execution_history_numeric_compare({alias}.text_value, ?) {sql_operator} 0"
        )
        params.append(_canonical_number_text(value))
    if storage is not None and storage.interned:
        return storage.exists_sql(alias, location, pointer, comparison, params[2:])
    return (
        f"EXISTS (SELECT 1 FROM workflow_node_execution_current_values {alias} WHERE {base}{comparison})",
        params,
    )


def _validate_range_filters(
    connection: sqlite3.Connection,
    *,
    node_id: str,
    started_from: datetime,
    started_to: datetime,
    statuses: list[str],
    filters: list[Mapping[str, Any]],
    storage: HistoryIndexStorage | None = None,
) -> None:
    """Refuse range filters over pre-v3 float-only scalar rows.

    Those rows have no exact text representation, so using their REAL value for
    comparisons could silently collapse large JSON integers. New rows always
    retain canonical numeric text and use the SQLite comparison function.
    """
    for item in filters:
        if str(item.get("operator") or "") not in _NUMERIC_OPERATORS:
            continue
        where = ["h.node_id = ?", "h.started_at >= ?", "h.started_at < ?"]
        params: list[Any] = [
            node_id,
            _storage_timestamp(started_from),
            _storage_timestamp(started_to),
        ]
        if statuses:
            where.append(f"h.status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        if storage is not None and storage.interned:
            predicate, direct_params = storage.exists_sql(
                "v",
                str(item.get("location") or ""),
                str(item.get("path") or ""),
                " AND v.value_type='number' AND v.text_value IS NULL",
            )
            row = connection.execute(
                f"SELECT 1 FROM workflow_node_execution_history h {storage.search_joins} "
                f"WHERE {' AND '.join(where)} AND {predicate} "
                "AND NOT EXISTS(SELECT 1 FROM workflow_history_index_jobs j WHERE j.execution_key=h.execution_key) LIMIT 1",
                [*params, *direct_params],
            ).fetchone()
        else:
            row = connection.execute(
                f"""
            SELECT 1
            FROM workflow_node_execution_current_values v
            JOIN workflow_node_execution_history h ON h.execution_key = v.execution_key
            WHERE {" AND ".join(where)}
              AND v.location = ?
              AND v.json_pointer = ?
              AND v.value_type = 'number'
              AND v.text_value IS NULL
            LIMIT 1
            """,
                [*params, str(item.get("location") or ""), str(item.get("path") or "")],
            ).fetchone()
        if row is not None:
            raise ValueError(
                "numeric range filters are unavailable for legacy history values without an "
                "exact numeric representation"
            )


def _history_row_payload(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    filters: list[Mapping[str, Any]],
    storage: HistoryIndexStorage | None = None,
) -> dict[str, Any]:
    matched_values: dict[str, Any] = {}
    for item in filters:
        if item.get("operator") == "not_exists":
            continue
        if storage is not None and storage.interned:
            value_row = storage.matched_value(row, item.get("location"), item.get("path"))
        else:
            value_row = connection.execute(
                """
            SELECT value_type, text_value, numeric_value, boolean_value
            FROM workflow_node_execution_current_values
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
        "config_hash": row["config_hash"],
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
        sanitize_execution_payload(load_record(connection, source["payload_json"]))
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
    # Separate index seeks avoid scanning the whole window for a combined
    # MIN/MAX aggregate, including when the caller opts out of an exact count.
    bounds: dict[str, str | None] = {}
    for name, order in (("earliest", "ASC"), ("latest", "DESC")):
        row = connection.execute(
            f"""
            SELECT started_at
            FROM workflow_node_execution_history
            WHERE node_id = ? AND started_at >= ? AND started_at < ?
              AND NOT EXISTS (SELECT 1 FROM workflow_history_retirements r
                  WHERE r.execution_key=workflow_node_execution_history.execution_key)
            ORDER BY started_at {order}
            LIMIT 1
            """,
            (
                node_id,
                _storage_timestamp(started_from),
                _storage_timestamp(started_to),
            ),
        ).fetchone()
        bounds[name] = row["started_at"] if row is not None else None
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
