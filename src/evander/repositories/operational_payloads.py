"""Private, lossless operational payload storage (never the sanitized history store).

Rows keep their small lifecycle fields inline. Large values are immutable compressed
objects; typed nodes keep user dictionaries, including reference-shaped ones, literal.
Legacy inline rows remain readable until explicitly migrated by the operator.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evander.repositories.history_index_storage import (
    interned_schema_is_current,
    uses_interned_history,
)

THRESHOLD_BYTES = 16 * 1024
FORMAT_KEY = "__evander_operational_storage_v1__"
PAYLOAD_FIELDS = frozenset({
    "payload", "input_payload", "outputs", "resolved_config", "inputs_by_source",
    "input_event_ids_by_source", "input_occurred_at_by_source", "binding_trace",
    "completion_event_for_execution_ids",
})
TABLES = {
    "trigger": ("trigger_events", "event_id"),
    "effect": ("effect_executions", "execution_id"),
    "gate": ("gate_executions", "execution_id"),
}


def ensure_operational_schema(connection: sqlite3.Connection) -> None:
    # execute individually: executescript would commit the caller's migration.
    statements = [
        """CREATE TABLE IF NOT EXISTS workflow_operational_blobs(
            sha256 TEXT PRIMARY KEY, codec TEXT NOT NULL, payload_blob BLOB NOT NULL,
            original_bytes INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS workflow_operational_blob_edges(
            parent_sha256 TEXT NOT NULL REFERENCES workflow_operational_blobs(sha256) ON DELETE CASCADE,
            child_sha256 TEXT NOT NULL REFERENCES workflow_operational_blobs(sha256),
            PRIMARY KEY(parent_sha256, child_sha256))""",
        """CREATE INDEX IF NOT EXISTS idx_operational_edges_child
            ON workflow_operational_blob_edges(child_sha256)""",
        """CREATE TABLE IF NOT EXISTS workflow_operational_record_refs(
            record_kind TEXT NOT NULL, record_id TEXT NOT NULL,
            sha256 TEXT NOT NULL REFERENCES workflow_operational_blobs(sha256),
            PRIMARY KEY(record_kind, record_id, sha256))""",
        """CREATE INDEX IF NOT EXISTS idx_operational_refs_blob
            ON workflow_operational_record_refs(sha256)""",
        """CREATE TABLE IF NOT EXISTS workflow_execution_projections(
            record_kind TEXT NOT NULL, record_id TEXT NOT NULL,
            has_session_reference INTEGER NOT NULL DEFAULT 0,
            dispatch_id TEXT, session_id TEXT, queued INTEGER NOT NULL DEFAULT 0,
            native_thread_observed_at TEXT, PRIMARY KEY(record_kind, record_id))""",
        """CREATE INDEX IF NOT EXISTS idx_execution_projection_dispatch
            ON workflow_execution_projections(dispatch_id, session_id, record_kind, record_id)""",
        """CREATE TABLE IF NOT EXISTS workflow_history_index_jobs(
            execution_key TEXT PRIMARY KEY REFERENCES workflow_node_execution_history(execution_key) ON DELETE CASCADE,
            input_blob_sha256 TEXT NOT NULL, output_blob_sha256 TEXT NOT NULL,
            enqueued_at TEXT NOT NULL, next_offset INTEGER NOT NULL DEFAULT 0, cursor_json TEXT)""",
        """CREATE INDEX IF NOT EXISTS idx_history_index_jobs_age
            ON workflow_history_index_jobs(enqueued_at, execution_key)""",
        """CREATE TABLE IF NOT EXISTS workflow_history_retirements(
            execution_key TEXT PRIMARY KEY REFERENCES workflow_node_execution_history(execution_key)
                ON DELETE CASCADE)""",
        """CREATE TRIGGER IF NOT EXISTS workflow_history_cancel_retirement
            AFTER UPDATE ON workflow_node_execution_history
            WHEN EXISTS(SELECT 1 FROM workflow_history_retirements r
                        WHERE r.execution_key=NEW.execution_key)
            BEGIN
                INSERT INTO workflow_history_index_jobs
                    VALUES(NEW.execution_key,NEW.input_blob_sha256,NEW.output_blob_sha256,
                           NEW.updated_at,0,NULL)
                    ON CONFLICT(execution_key) DO UPDATE SET
                        input_blob_sha256=excluded.input_blob_sha256,
                        output_blob_sha256=excluded.output_blob_sha256,
                        enqueued_at=excluded.enqueued_at,next_offset=0,cursor_json=NULL;
                DELETE FROM workflow_history_retirements WHERE execution_key=NEW.execution_key;
            END""",
    ]
    statements.append("""CREATE TABLE IF NOT EXISTS workflow_history_index_state(
        execution_key TEXT PRIMARY KEY REFERENCES workflow_node_execution_history(execution_key) ON DELETE CASCADE,
        generation TEXT NOT NULL)""")
    if not uses_interned_history(connection):
        statements.extend(
            [
                """CREATE TABLE IF NOT EXISTS workflow_history_index_values(
            execution_key TEXT NOT NULL REFERENCES workflow_node_execution_history(execution_key) ON DELETE CASCADE,
            generation TEXT NOT NULL, location TEXT NOT NULL, json_pointer TEXT NOT NULL,
            value_type TEXT NOT NULL, value_hash TEXT, text_value TEXT, numeric_value REAL, boolean_value INTEGER,
            PRIMARY KEY(execution_key, generation, location, json_pointer))""",
                """CREATE INDEX IF NOT EXISTS idx_history_generation_lookup ON workflow_history_index_values(
            location, json_pointer, value_type, value_hash, execution_key, generation)""",
                """CREATE INDEX IF NOT EXISTS idx_history_generation_numeric ON workflow_history_index_values(
            location, json_pointer, value_type, numeric_value, execution_key, generation)""",
                """CREATE INDEX IF NOT EXISTS idx_history_generation_exact ON workflow_history_index_values(
            location, json_pointer, value_type, text_value, execution_key, generation)""",
                """CREATE VIEW IF NOT EXISTS workflow_node_execution_current_values AS
            SELECT v.execution_key,v.location,v.json_pointer,v.value_type,v.value_hash,v.text_value,v.numeric_value,v.boolean_value
            FROM workflow_history_index_values v
            JOIN workflow_history_index_state s ON s.execution_key=v.execution_key AND s.generation=v.generation
            WHERE NOT EXISTS(SELECT 1 FROM workflow_history_index_jobs j WHERE j.execution_key=v.execution_key)
            UNION ALL
            SELECT v.* FROM workflow_node_execution_history_values v
            WHERE NOT EXISTS(SELECT 1 FROM workflow_history_index_state s WHERE s.execution_key=v.execution_key)
              AND NOT EXISTS(SELECT 1 FROM workflow_history_index_jobs j WHERE j.execution_key=v.execution_key)""",
            ]
        )
    for statement in statements:
        connection.execute(statement)
    for kind, (table, key) in TABLES.items():
        connection.execute(f"""CREATE TRIGGER IF NOT EXISTS operational_{kind}_deleted
            AFTER DELETE ON {table} BEGIN
                DELETE FROM workflow_operational_record_refs WHERE record_kind = '{kind}' AND record_id = OLD.{key};
                DELETE FROM workflow_execution_projections WHERE record_kind = '{kind}' AND record_id = OLD.{key};
            END""")


def operational_schema_is_current(connection: sqlite3.Connection) -> bool:
    names = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return {
        "workflow_operational_blobs",
        "workflow_operational_blob_edges",
        "workflow_operational_record_refs",
        "workflow_execution_projections",
        "workflow_history_index_jobs",
        "workflow_history_retirements",
    } <= names and (not uses_interned_history(connection) or interned_schema_is_current(connection))


initialize_payload_schema = ensure_operational_schema


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _references(node: Any):
    tag, value = node
    if tag == "r":
        yield value
    elif tag == "o":
        for child in value.values():
            yield from _references(child)
    elif tag == "a":
        for child in value:
            yield from _references(child)


@dataclass(frozen=True)
class PreparedOperationalBlob:
    digest: str
    compressed: bytes
    original_bytes: int
    children: tuple[str, ...]


@dataclass(frozen=True)
class PreparedOperationalRecord:
    kind: str
    record_id: str
    payload_json: str
    blobs: tuple[PreparedOperationalBlob, ...]
    references: tuple[str, ...]
    projection: tuple[Any, ...]


def _encode(value: Any, blobs: dict[str, PreparedOperationalBlob]) -> Any:
    if isinstance(value, dict):
        node = ["o", {key: _encode(item, blobs) for key, item in value.items()}]
    elif isinstance(value, list):
        node = ["a", [_encode(item, blobs) for item in value]]
    else:
        node = ["v", value]
    raw = _json(node).encode("utf-8")
    if len(raw) < THRESHOLD_BYTES:
        return node
    digest = hashlib.sha256(raw).hexdigest()
    if digest not in blobs:
        blobs[digest] = PreparedOperationalBlob(
            digest, zlib.compress(raw, level=1), len(raw), tuple(sorted(set(_references(node))))
        )
    return ["r", digest]


def _decode(connection: sqlite3.Connection, node: Any, cache: dict[str, Any]) -> Any:
    tag, value = node
    if tag == "v":
        return value
    if tag == "o":
        return {key: _decode(connection, child, cache) for key, child in value.items()}
    if tag == "a":
        return [_decode(connection, child, cache) for child in value]
    if tag != "r":
        raise ValueError("Unsupported operational payload node")
    if value not in cache:
        row = connection.execute(
            "SELECT codec, payload_blob FROM workflow_operational_blobs WHERE sha256 = ?", (value,)
        ).fetchone()
        if row is None or row[0] != "zlib-json-v1":
            raise ValueError("Missing or unsupported operational payload blob")
        raw = zlib.decompress(row[1])
        if hashlib.sha256(raw).hexdigest() != value:
            raise ValueError("Operational payload digest mismatch")
        # Cache encoded nodes, not mutable public dictionaries shared by callers.
        cache[value] = json.loads(raw)
    return _decode(connection, cache[value], cache)


def has_session_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (
                str(key).lower().endswith("session_id")
                and isinstance(child, str)
                and bool(child.strip())
            )
            or has_session_reference(child)
            for key, child in value.items()
        )
    return isinstance(value, list) and any(has_session_reference(child) for child in value)


def prepare_record(*, kind: str, record: dict[str, Any]) -> PreparedOperationalRecord:
    """Encode, hash and compress before opening the bounded writer operation."""
    record_id = str(record[TABLES[kind][1]])
    inline = dict(record)
    encoded: dict[str, Any] = {}
    blobs: dict[str, PreparedOperationalBlob] = {}
    for key, value in record.items():
        # Lifecycle fields are read and changed independently of immutable
        # payloads. Their meaning must not depend on their serialized size.
        if key not in PAYLOAD_FIELDS:
            continue
        if isinstance(value, (dict, list)) or (
            isinstance(value, str) and len(value) >= THRESHOLD_BYTES
        ):
            node = _encode(value, blobs)
            if any(_references(node)):
                encoded[key] = node
                inline[key] = (
                    {} if isinstance(value, dict) else [] if isinstance(value, list) else ""
                )
    if encoded:
        inline[FORMAT_KEY] = {"fields": encoded}
    outputs = record.get("outputs") or {}
    config = record.get("resolved_config") or {}
    scope = outputs.get("scope")
    if not isinstance(scope, Mapping):
        scope = {}
    return PreparedOperationalRecord(
        kind,
        record_id,
        _json(inline),
        tuple(blobs.values()),
        tuple(sorted({ref for node in encoded.values() for ref in _references(node)})),
        (
            kind,
            record_id,
            int(has_session_reference(record)),
            outputs.get("dispatch_id"),
            outputs.get("session_id") or scope.get("session_id") or config.get("session_id"),
            int(bool(outputs.get("queued"))),
            outputs.get("native_thread_observed_at"),
        ),
    )


def store_prepared_record(
    connection: sqlite3.Connection, prepared: PreparedOperationalRecord
) -> str:
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    # Preparation orders children before parents, satisfying edge foreign keys.
    for blob in prepared.blobs:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO workflow_operational_blobs VALUES (?, 'zlib-json-v1', ?, ?)",
            (blob.digest, blob.compressed, blob.original_bytes),
        )
        if cursor.rowcount:
            connection.executemany(
                "INSERT INTO workflow_operational_blob_edges VALUES (?, ?)",
                [(blob.digest, child) for child in blob.children],
            )
    connection.execute(
        "DELETE FROM workflow_operational_record_refs WHERE record_kind = ? AND record_id = ?",
        (prepared.kind, prepared.record_id),
    )
    connection.executemany(
        "INSERT INTO workflow_operational_record_refs VALUES (?, ?, ?)",
        [(prepared.kind, prepared.record_id, digest) for digest in prepared.references],
    )
    connection.execute(
        """INSERT INTO workflow_execution_projections VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(record_kind, record_id) DO UPDATE SET
            has_session_reference=excluded.has_session_reference, dispatch_id=excluded.dispatch_id,
            session_id=excluded.session_id, queued=excluded.queued,
            native_thread_observed_at=excluded.native_thread_observed_at""",
        prepared.projection,
    )
    return prepared.payload_json


def store_record(connection: sqlite3.Connection, *, kind: str, record: dict[str, Any]) -> str:
    # Compatibility for explicit offline migration and archive callers.
    return store_prepared_record(connection, prepare_record(kind=kind, record=record))


def load_record(connection: sqlite3.Connection, payload: str | bytes) -> dict[str, Any]:
    record = json.loads(payload)
    if FORMAT_KEY not in record:
        return record
    storage = record.pop(FORMAT_KEY)
    cache: dict[str, Any] = {}
    for key, node in storage["fields"].items():
        if key not in PAYLOAD_FIELDS and record.get(key) not in ("", {}, []):
            # Early codec rows could externalize lifecycle text. A subsequent
            # inline update is authoritative even if the old reference remains.
            continue
        record[key] = _decode(connection, node, cache)
    return record


def load_lifecycle_field(
    connection: sqlite3.Connection, value: Any, encoded_json: str | None,
) -> Any:
    """Read an early codec lifecycle reference without loading execution inputs."""
    if encoded_json is not None and value in ("", {}, []):
        return _decode(connection, json.loads(encoded_json), {})
    return value


def discard_record_field_references(
    connection: sqlite3.Connection, *, kind: str, record_id: str, fields: Iterable[str],
) -> None:
    """Drop replaced legacy references in the caller's lifecycle transaction.

    Inspect only encoded reference metadata, never decompress immutable inputs.
    Shared roots stay reachable through any other field of the same record.
    """
    table, key = TABLES[kind]
    row = connection.execute(
        f"SELECT json_extract(payload_json, ?) FROM {table} WHERE {key}=?",
        (f"$.{FORMAT_KEY}.fields", record_id),
    ).fetchone()
    if row is None or row[0] is None:
        return
    encoded = json.loads(row[0])
    replaced = set(fields).intersection(encoded)
    if not replaced:
        return
    for field in replaced:
        del encoded[field]
    paths = [f"$.{FORMAT_KEY}.fields.{field}" for field in sorted(replaced)]
    if not encoded:
        paths = [f"$.{FORMAT_KEY}"]
    connection.execute(
        f"UPDATE {table} SET payload_json=json_remove(payload_json,"
        + ",".join("?" for _ in paths) + f") WHERE {key}=?",
        [*paths, record_id],
    )
    roots = sorted({ref for node in encoded.values() for ref in _references(node)})
    keep = " AND sha256 NOT IN (" + ",".join("?" for _ in roots) + ")" if roots else ""
    connection.execute(
        "DELETE FROM workflow_operational_record_refs WHERE record_kind=? AND record_id=?" + keep,
        [kind, record_id, *roots],
    )


def load_record_at_path(path: Path, payload: str | bytes) -> dict[str, Any]:
    if (FORMAT_KEY.encode() if isinstance(payload, bytes) else FORMAT_KEY) not in payload:
        return json.loads(payload)
    from evander.repositories.orchestration_db import connect_orchestration_db_read_only

    with connect_orchestration_db_read_only(path) as connection:
        return load_record(connection, payload)


def collect_unreferenced_payloads(
    connection: sqlite3.Connection, *, limit: int = 100, hold_completed: bool = False
) -> int:
    """Scan one bounded DAG window; later sweeps reclaim orphaned children.

    Maintenance can hold completion after a clean sweep until its next restart.
    The default keeps the standalone collector's repeating-sweep behavior.
    """
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    limit = min(limit, 100)
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT value_json FROM orchestration_metadata WHERE key='operational_payload_gc_cursor'"
    ).fetchone()
    previous = json.loads(row[0]) if row else {}
    if hold_completed and previous.get("completed") is True:
        return 0
    progress = dict(previous) if hold_completed else {}
    candidates = [
        item[0]
        for item in connection.execute(
            "SELECT sha256 FROM workflow_operational_blobs WHERE sha256>? ORDER BY sha256 LIMIT ?",
            (previous.get("after", ""), limit),
        ).fetchall()
    ]
    deleted = 0
    if candidates:
        placeholders = ",".join("?" for _ in candidates)
        cursor = connection.execute(
            f"""DELETE FROM workflow_operational_blobs AS b
            WHERE sha256 IN ({placeholders})
              AND NOT EXISTS(SELECT 1 FROM workflow_operational_record_refs r WHERE r.sha256=b.sha256)
              AND NOT EXISTS(SELECT 1 FROM workflow_operational_blob_edges e WHERE e.child_sha256=b.sha256)""",
            candidates,
        )
        deleted = cursor.rowcount
    after = candidates[-1] if len(candidates) == limit else ""
    progress["after"] = after
    if hold_completed:
        deleted_in_sweep = bool(progress.get("deleted_in_sweep") or deleted)
        exhausted = len(candidates) < limit
        # Parents can sort after their children. Remember deletions across all
        # windows and complete a clean follow-up sweep before becoming idle.
        progress["completed"] = exhausted and not deleted_in_sweep
        progress["deleted_in_sweep"] = deleted_in_sweep and not exhausted
    if progress != previous and (row is not None or any(progress.values())):
        connection.execute(
            """INSERT INTO orchestration_metadata VALUES ('operational_payload_gc_cursor',?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
            (json.dumps(progress),),
        )
    return deleted
