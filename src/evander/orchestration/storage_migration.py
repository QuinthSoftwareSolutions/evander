"""Explicit offline conversion and rollback for operational payload references."""

from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Any

from evander.repositories.history_index_storage import uses_interned_history
from evander.repositories.operational_payloads import FORMAT_KEY, TABLES, load_record, store_record
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    ensure_orchestration_schema,
    orchestration_maintenance_index_status,
)


def check_orchestration_storage(path: Path, *, verify: bool = False) -> dict[str, Any]:
    """Read-only even for a database from an older release."""
    result: dict[str, Any] = {
        "database": str(path),
        "exists": path.is_file(),
        "tables": {},
        "errors": [],
    }
    if not path.is_file():
        result["errors"].append("Database does not exist")
        return result
    result["maintenance"] = orchestration_maintenance_index_status(path)
    with connect_orchestration_db_read_only(path, deadline_seconds=300) as connection:
        connection.execute("BEGIN")
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for kind, (table, _) in TABLES.items():
            if table not in tables:
                result["errors"].append(f"Missing table: {table}")
                continue
            counts = connection.execute(
                f"""SELECT COUNT(*),
                COALESCE(SUM(json_type(payload_json,?) IS NOT NULL),0) FROM {table}""",
                (f"$.{FORMAT_KEY}",),
            ).fetchone()
            result["tables"][kind] = {"records": counts[0], "referenced_records": counts[1]}
            if verify:
                for row in connection.execute(f"SELECT payload_json FROM {table}"):
                    try:
                        load_record(connection, row[0])
                    except (ValueError, KeyError, TypeError, zlib.error) as exc:
                        result["errors"].append(f"Invalid {kind} payload: {type(exc).__name__}")
        result["verified"] = verify and not result["errors"]
        modern_state = {}
        for table in (
            "workflow_history_index_jobs",
            "workflow_history_index_state",
            "workflow_execution_projections",
            "workflow_history_retirements",
        ):
            modern_state[table] = (
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if table in tables
                else 0
            )
        result["modern_metadata_rows"] = modern_state
        result["downgrade_ready"] = (
            not result["errors"]
            and all(item["referenced_records"] == 0 for item in result["tables"].values())
            and not any(modern_state.values())
            and not uses_interned_history(connection)
        )
        result["history_index_format"] = (
            "interned" if uses_interned_history(connection) else "legacy"
        )
    return result


def migrate_orchestration_storage(
    path: Path, *, downgrade: bool = False, batch_size: int = 100, max_batches: int | None = None
) -> dict[str, Any]:
    """Resumable offline batches; callers must stop all processes using this DB.

    Rollback rehydrates inline records before any old executable is started.
    Shared blobs are retained, so an interrupted rollback remains dual-readable.
    """
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    if not path.is_file():
        raise ValueError("Database does not exist")
    if downgrade:
        with connect_orchestration_db_read_only(path) as connection:
            if uses_interned_history(connection):
                raise ValueError(
                    "Convert to a separate legacy history index with convert-history-index --legacy before payload downgrade"
                )
    ensure_orchestration_schema(path)
    # An interrupted conversion owns its checkpoints until completion. Mixing
    # directions would let a resumed cursor skip records rewritten by the other
    # direction and falsely report a completed rollback.
    opposite = "references" if downgrade else "inline"
    with connect_orchestration_db_read_only(path) as connection:
        conflicting = connection.execute(
            "SELECT 1 FROM orchestration_metadata WHERE key LIKE ? LIMIT 1",
            (f"operational_migration:{opposite}:%",),
        ).fetchone()
    if conflicting is not None:
        raise ValueError(
            "An opposite-direction storage migration is incomplete; finish it before changing direction"
        )
    from evander.repositories.orchestration_db import build_orchestration_maintenance_indexes

    indexes = build_orchestration_maintenance_indexes(path)
    changed = 0
    batches = 0
    complete = True
    direction = "inline" if downgrade else "references"
    for kind, (table, key) in TABLES.items():
        state_key = f"operational_migration:{direction}:{kind}"
        while max_batches is None or batches < max_batches:
            with connect_orchestration_db(
                path, deadline_seconds=300, busy_timeout_ms=100
            ) as connection:
                connection.execute("BEGIN IMMEDIATE")
                saved = connection.execute(
                    "SELECT value_json FROM orchestration_metadata WHERE key=?", (state_key,)
                ).fetchone()
                after = json.loads(saved[0]).get("after", "") if saved else ""
                rows = connection.execute(
                    f"SELECT {key},length(CAST(payload_json AS BLOB)) FROM {table} WHERE {key}>? ORDER BY {key} LIMIT ?",
                    (after, batch_size),
                ).fetchall()
                if not rows:
                    break
                processed = 0
                payload_bytes = 0
                for metadata in rows:
                    if processed and payload_bytes + metadata[1] > 1024 * 1024:
                        break
                    row = connection.execute(
                        f"SELECT {key},payload_json FROM {table} WHERE {key}=?", (metadata[0],)
                    ).fetchone()
                    record = load_record(connection, row[1])
                    if downgrade:
                        payload = json.dumps(
                            record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                        )
                        connection.execute(
                            "DELETE FROM workflow_operational_record_refs WHERE record_kind=? AND record_id=?",
                            (kind, row[0]),
                        )
                    else:
                        payload = store_record(connection, kind=kind, record=record)
                    connection.execute(
                        f"UPDATE {table} SET payload_json=? WHERE {key}=?", (payload, row[0])
                    )
                    after = row[0]
                    payload_bytes += metadata[1]
                    processed += 1
                connection.execute(
                    """INSERT INTO orchestration_metadata(key,value_json) VALUES(?,?)
                    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                    (state_key, json.dumps({"after": after})),
                )
                changed += processed
            batches += 1
        else:
            complete = False
            break
    if complete and downgrade:
        history_complete, history_batches = _downgrade_history(
            path, max_batches=None if max_batches is None else max(0, max_batches - batches)
        )
        complete = history_complete
        batches += history_batches
    if complete:
        with connect_orchestration_db(path, deadline_seconds=300) as connection:
            connection.execute(
                "DELETE FROM orchestration_metadata WHERE key LIKE 'operational_migration:%'"
            )
    return {
        "database": str(path),
        "direction": direction,
        "processed": changed,
        "batches": batches,
        "complete": complete,
        "indexes": indexes,
    }


def _downgrade_history(path: Path, *, max_batches: int | None) -> tuple[bool, int]:
    """Restore the prior binary's scalar-search contract in resumable slices.

    New indexing metadata must also be removed: otherwise a later upgrade could
    prefer a projection that the old binary no longer updates.
    """
    from evander.repositories.execution_history import (
        prepare_pending_history_indexing,
        run_pending_history_indexing,
    )

    state_key = "operational_migration:inline:history"
    batches = 0
    while max_batches is None or batches < max_batches:
        with connect_orchestration_db_read_only(path, deadline_seconds=300) as connection:
            row = connection.execute(
                "SELECT value_json FROM orchestration_metadata WHERE key=?", (state_key,)
            ).fetchone()
            state = json.loads(row[0]) if row else {"phase": "cancel_retirement", "after": 0}
        phase = state["phase"]
        if phase == "done":
            return True, batches
        prepared = prepare_pending_history_indexing(path) if phase == "index" else None
        with connect_orchestration_db(
            path, deadline_seconds=300, busy_timeout_ms=100
        ) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if phase == "cancel_retirement":
                # An older binary cannot hide partially retired history. The
                # update trigger restores a complete index before rollback.
                restored = connection.execute("""UPDATE workflow_node_execution_history
                    SET updated_at=updated_at WHERE execution_key IN (
                        SELECT execution_key FROM workflow_history_retirements LIMIT 100)
                    """).rowcount
                if not restored:
                    state = {"phase": "index", "after": 0}
            elif phase == "index":
                if prepared is None:
                    state = {"phase": "clear_legacy", "after": 0}
                else:
                    run_pending_history_indexing(connection, prepared=prepared)
            elif phase == "clear_legacy":
                deleted = connection.execute("""DELETE FROM workflow_node_execution_history_values WHERE rowid IN (
                    SELECT v.rowid FROM workflow_node_execution_history_values v
                    JOIN workflow_history_index_state s ON s.execution_key=v.execution_key LIMIT 100)""").rowcount
                if not deleted:
                    state = {"phase": "copy", "after": 0}
            elif phase == "copy":
                rows = connection.execute(
                    """SELECT v.rowid,v.execution_key,v.location,v.json_pointer,v.value_type,
                    v.value_hash,v.text_value,v.numeric_value,v.boolean_value FROM workflow_history_index_values v
                    JOIN workflow_history_index_state s ON s.execution_key=v.execution_key AND s.generation=v.generation
                    WHERE v.rowid>? ORDER BY v.rowid LIMIT 100""",
                    (state["after"],),
                ).fetchall()
                connection.executemany(
                    "INSERT OR REPLACE INTO workflow_node_execution_history_values VALUES(?,?,?,?,?,?,?,?)",
                    [tuple(row)[1:] for row in rows],
                )
                state = (
                    {"phase": "copy", "after": rows[-1][0]}
                    if rows
                    else {"phase": "clear_state", "after": 0}
                )
            else:
                table, next_phase = {
                    "clear_state": ("workflow_history_index_state", "clear_values"),
                    "clear_values": ("workflow_history_index_values", "clear_projections"),
                    "clear_projections": ("workflow_execution_projections", "done"),
                }[phase]
                deleted = connection.execute(
                    f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} LIMIT 100)"
                ).rowcount
                if not deleted:
                    state = {"phase": next_phase, "after": 0}
            connection.execute(
                """INSERT INTO orchestration_metadata(key,value_json) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
                (state_key, json.dumps(state)),
            )
        batches += 1
    return False, batches
