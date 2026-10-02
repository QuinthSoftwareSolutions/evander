"""One bounded storage maintenance slice, never called on a dispatch thread."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import UTC, datetime

from evander.repositories.orchestration_db import (
    OrchestrationWalCheckpointResult,
    _try_orchestration_retention_lock,
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    json_loads_object,
    maybe_checkpoint_orchestration_wal,
    run_orchestration_retention_slice,
)
from evander.repositories.sqlite_runtime import (
    SqliteOperationDeadlineExceeded,
    is_storage_busy,
    is_storage_contention,
)

logger = logging.getLogger(__name__)


def _restart_completed_gc_sweeps(connection) -> None:
    """Start a new sweep only after every independently paced component finished."""
    connection.execute("BEGIN IMMEDIATE")
    rows = connection.execute(
        "SELECT key,value_json FROM orchestration_metadata "
        "WHERE key IN ('history_scalar_gc_cursor','operational_payload_gc_cursor')"
    ).fetchall()
    metadata = {row[0]: json_loads_object(row[1]) for row in rows}
    history = metadata.get("history_scalar_gc_cursor", {})
    payload = metadata.get("operational_payload_gc_cursor", {})
    if (
        history.get("current_completed") is True
        and history.get("legacy_completed") is True
        and payload.get("completed") is True
    ):
        connection.executemany(
            "UPDATE orchestration_metadata SET value_json=? WHERE key=?",
            [
                ("{}", "history_scalar_gc_cursor"),
                ("{}", "operational_payload_gc_cursor"),
            ],
        )


def run_storage_maintenance(
    config, *, run_garbage_collection: bool = True
) -> OrchestrationWalCheckpointResult:
    path = config.orchestration_store_path
    garbage_collection_pending = True if run_garbage_collection else None
    result = OrchestrationWalCheckpointResult(
        skipped_reason="maintenance_busy", garbage_collection_pending=garbage_collection_pending
    )
    with _try_orchestration_retention_lock(path) as acquired:
        if not acquired:
            return result
        # Each phase owns a separate transaction/deadline. One busy phase must
        # not suppress passive checkpoints or permanently starve another phase.
        phases = ("checkpoint", "history", "retention")
        if run_garbage_collection:
            phases += ("history_gc", "payload_gc")
        phases += ("backfill",)
        garbage_collection_phases: dict[str, bool] = {}
        writer_busy = False
        for phase in phases:
            started = time.monotonic()
            outcome = "succeeded"
            details = {}
            try:
                if phase == "checkpoint":
                    result = maybe_checkpoint_orchestration_wal(
                        path,
                        mode=config.orchestration_wal_checkpoint_mode,
                        min_bytes=config.orchestration_wal_checkpoint_min_bytes,
                        interval_seconds=config.orchestration_wal_checkpoint_interval_seconds,
                        busy_timeout_ms=min(
                            100, config.orchestration_wal_checkpoint_busy_timeout_ms
                        ),
                    )
                    details = {
                        "wal_bytes": result.wal_size_bytes,
                        "busy": result.busy,
                        "skipped_reason": result.skipped_reason,
                    }
                elif phase == "history":
                    from evander.repositories.execution_history import (
                        run_history_indexing_slice,
                    )

                    deadline = time.monotonic() + 0.25
                    details = {"indexed_values": 0, "completed": 0}
                    while time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        indexed = run_history_indexing_slice(
                            path,
                            batch_size=100,
                            time_budget_seconds=remaining,
                        )
                        details["indexed_values"] += indexed.get("indexed_values", 0)
                        details["completed"] += indexed.get("completed", 0)
                        if not any(indexed.values()):
                            break
                        # Yield between committed slices so foreground writers
                        # can use the slot while this backlog is draining.
                        time.sleep(min(0.005, max(0, deadline - time.monotonic())))
                elif phase == "retention":
                    with connect_orchestration_db_read_only(path) as connection:
                        rows = connection.execute(
                            "SELECT key,value_json FROM orchestration_metadata "
                            "WHERE key IN ('last_retention_date','retention_progress')"
                        ).fetchall()
                        metadata = {row[0]: json_loads_object(row[1]) for row in rows}
                    if metadata.get("last_retention_date", {}).get("date") == datetime.now(
                        UTC
                    ).date().isoformat() and not metadata.get("retention_progress"):
                        continue
                    retained = run_orchestration_retention_slice(
                        path,
                        retention_days=config.orchestration_retention_days,
                        compact_history_retention_days=config.orchestration_compact_history_retention_days,
                        batch_size=100,
                        time_budget_seconds=0.25,
                        busy_timeout_ms=0,
                    )
                    details = {
                        "remaining": retained.remaining,
                        "skipped_reason": retained.skipped_reason,
                    }
                elif phase == "history_gc":
                    from evander.repositories.execution_history import (
                        collect_superseded_history_values,
                    )

                    with connect_orchestration_db(
                        path,
                        busy_timeout_ms=0,
                        deadline_seconds=0.25,
                        operation="history_scalar_gc",
                    ) as connection:
                        _restart_completed_gc_sweeps(connection)
                        details = {"deleted": collect_superseded_history_values(
                            connection, hold_completed=True
                        )}
                        row = connection.execute(
                            "SELECT value_json FROM orchestration_metadata "
                            "WHERE key='history_scalar_gc_cursor'"
                        ).fetchone()
                        state = json_loads_object(row[0]) if row else {}
                        details["remaining"] = not (
                            state.get("current_completed") is True
                            and state.get("legacy_completed") is True
                        )
                    garbage_collection_phases[phase] = details["remaining"]
                elif phase == "payload_gc":
                    from evander.repositories.operational_payloads import (
                        collect_unreferenced_payloads,
                    )

                    with connect_orchestration_db(
                        path,
                        busy_timeout_ms=0,
                        deadline_seconds=0.25,
                        operation="operational_payload_gc",
                    ) as connection:
                        details = {"deleted": collect_unreferenced_payloads(
                            connection, limit=100, hold_completed=True
                        )}
                        row = connection.execute(
                            "SELECT value_json FROM orchestration_metadata "
                            "WHERE key='operational_payload_gc_cursor'"
                        ).fetchone()
                        details["remaining"] = not (
                            row and json_loads_object(row[0]).get("completed") is True
                        )
                    garbage_collection_phases[phase] = details["remaining"]
                else:
                    with connect_orchestration_db_read_only(path) as connection:
                        state = connection.execute(
                            "SELECT value_json FROM orchestration_metadata WHERE key='execution_history_backfill'"
                        ).fetchone()
                        if state and json_loads_object(state[0]).get("completed"):
                            continue
                        pending = connection.execute(
                            "SELECT 1 FROM trigger_events "
                            "WHERE delivery_state IN ('pending','inflight') LIMIT 1"
                        ).fetchone()
                    if pending is not None:
                        continue
                    from evander.repositories.execution_history import (
                        run_execution_history_backfill,
                    )

                    details = run_execution_history_backfill(path, batch_size=10, busy_timeout_ms=0)
            except Exception as error:
                outcome = (
                    "busy" if is_storage_contention(error)
                    else "deadline" if isinstance(error, SqliteOperationDeadlineExceeded)
                    else "failed"
                )
                writer_busy = outcome == "busy"
                logger.warning(
                    "storage_maintenance phase=%s outcome=%s",
                    phase,
                    outcome,
                    exc_info=outcome == "failed",
                )
            finally:
                logger.info(
                    "storage_maintenance phase=%s duration_ms=%.1f outcome=%s details=%s",
                    phase,
                    (time.monotonic() - started) * 1000,
                    outcome,
                    details,
                )
            if writer_busy:
                # Foreground work wins contested writer slots. Resume on the
                # normal lane interval rather than repeatedly competing.
                result = replace(result, skipped_reason="storage_busy")
                break
        if len(garbage_collection_phases) == 2:
            # Completion latches survive another component's continuation and
            # only become true after deletion follow-up sweeps finish.
            garbage_collection_pending = any(garbage_collection_phases.values())
        result = replace(result, garbage_collection_pending=garbage_collection_pending)
        try:
            with connect_orchestration_db_read_only(path) as connection:
                pending, oldest = connection.execute(
                    "SELECT COUNT(*), MIN(enqueued_at) FROM workflow_history_index_jobs"
                ).fetchone()
            age = (
                (datetime.now(UTC) - datetime.fromisoformat(oldest)).total_seconds()
                if oldest
                else None
            )
            result = replace(
                result,
                history_index_pending=pending,
                history_index_oldest_age_seconds=max(0, age) if age is not None else None,
            )
            if pending:
                logger.info(
                    "storage_backlog history_index_pending=%s oldest_age_seconds=%.3f", pending, age
                )
        except Exception as error:
            logger.warning(
                "storage_backlog outcome=%s",
                "busy" if is_storage_busy(error) else "failed",
                exc_info=not is_storage_busy(error),
            )
    return result
