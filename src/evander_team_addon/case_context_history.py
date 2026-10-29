"""Indexed snapshot history, separate from the operational Team aggregate."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .case_context_models import CaseContextSnapshot

if TYPE_CHECKING:
    from .models import TeamState
    from .sqlite_store import TeamStateSqliteStore


HISTORY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS team_case_context_snapshot_index (
    id TEXT PRIMARY KEY,
    dispatch_id TEXT,
    directory_path TEXT NOT NULL,
    view_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS team_context_snapshot_dispatch
    ON team_case_context_snapshot_index(dispatch_id, id);
CREATE TABLE IF NOT EXISTS team_case_context_snapshot_cases (
    case_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (case_id, snapshot_id)
);
CREATE INDEX IF NOT EXISTS team_context_snapshot_case_page
    ON team_case_context_snapshot_cases(case_id, created_at_utc DESC, snapshot_id DESC);
CREATE INDEX IF NOT EXISTS team_context_snapshot_case_delete
    ON team_case_context_snapshot_cases(snapshot_id);
CREATE TABLE IF NOT EXISTS team_case_context_snapshot_pending (id TEXT PRIMARY KEY);
CREATE TRIGGER IF NOT EXISTS team_context_snapshot_insert
AFTER INSERT ON team_case_context_snapshots BEGIN
    INSERT OR IGNORE INTO team_case_context_snapshot_pending VALUES (NEW.id);
END;
CREATE TRIGGER IF NOT EXISTS team_context_snapshot_update
AFTER UPDATE ON team_case_context_snapshots BEGIN
    INSERT OR IGNORE INTO team_case_context_snapshot_pending VALUES (OLD.id);
    INSERT OR IGNORE INTO team_case_context_snapshot_pending VALUES (NEW.id);
END;
CREATE TRIGGER IF NOT EXISTS team_context_snapshot_delete
AFTER DELETE ON team_case_context_snapshots BEGIN
    DELETE FROM team_case_context_snapshot_index WHERE id = OLD.id;
    DELETE FROM team_case_context_snapshot_cases WHERE snapshot_id = OLD.id;
    DELETE FROM team_case_context_snapshot_pending WHERE id = OLD.id;
END;
"""


def index_snapshot(connection: sqlite3.Connection, metadata: Mapping[str, Any]) -> None:
    created_at = datetime.fromisoformat(metadata["created_at"])
    created_at_utc = (
        created_at.replace(tzinfo=created_at.tzinfo or UTC)
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
    )
    connection.execute(
        "INSERT OR REPLACE INTO team_case_context_snapshot_index "
        "(id, dispatch_id, directory_path, view_id, source_revision, created_at, created_at_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            metadata["id"],
            metadata["dispatch_id"],
            metadata["directory_path"],
            metadata["view_id"],
            metadata["source_revision"],
            metadata["created_at"],
            created_at_utc,
        ),
    )
    connection.execute(
        "DELETE FROM team_case_context_snapshot_cases WHERE snapshot_id = ?", (metadata["id"],)
    )
    connection.executemany(
        "INSERT INTO team_case_context_snapshot_cases VALUES (?, ?, ?)",
        [(case_id, metadata["id"], created_at_utc) for case_id in set(metadata["case_ids"])],
    )
    connection.execute(
        "DELETE FROM team_case_context_snapshot_pending WHERE id = ?", (metadata["id"],)
    )


def backfill_snapshot_index(connection: sqlite3.Connection) -> None:
    """Read only headers in bounded batches; leave archived payload bytes untouched.

    Triggers retain a repair queue if an older release writes after a rollback.
    The next schema initialization repairs those entries before publishing readiness.
    """
    connection.execute(
        "INSERT OR IGNORE INTO team_case_context_snapshot_pending "
        "SELECT s.id FROM team_case_context_snapshots s "
        "LEFT JOIN team_case_context_snapshot_index i ON i.id = s.id WHERE i.id IS NULL"
    )
    while rows := connection.execute(
        "SELECT id FROM team_case_context_snapshot_pending ORDER BY id LIMIT 128"
    ).fetchall():
        for row in rows:
            metadata = connection.execute(
                "SELECT id, json_extract(payload_json, '$.dispatch_id') AS dispatch_id, "
                "json_extract(payload_json, '$.directory_path') AS directory_path, "
                "json_extract(payload_json, '$.view.view_id') AS view_id, "
                "json_extract(payload_json, '$.source_revision') AS source_revision, "
                "json_extract(payload_json, '$.created_at') AS created_at, "
                "json_extract(payload_json, '$.case_ids') AS case_ids "
                "FROM team_case_context_snapshots WHERE id = ?",
                (row["id"],),
            ).fetchone()
            if metadata is None:
                connection.execute(
                    "DELETE FROM team_case_context_snapshot_index WHERE id = ?", (row["id"],)
                )
                connection.execute(
                    "DELETE FROM team_case_context_snapshot_cases WHERE snapshot_id = ?",
                    (row["id"],),
                )
                connection.execute(
                    "DELETE FROM team_case_context_snapshot_pending WHERE id = ?", (row["id"],)
                )
                continue
            index_snapshot(
                connection, {**dict(metadata), "case_ids": json.loads(metadata["case_ids"])}
            )


def write_snapshots(
    connection: sqlite3.Connection, snapshots: Sequence[CaseContextSnapshot]
) -> None:
    for snapshot in snapshots:
        payload = snapshot.model_dump(mode="json")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        existing = connection.execute(
            "SELECT payload_json FROM team_case_context_snapshots WHERE id = ?", (snapshot.id,)
        ).fetchone()
        if existing is not None:
            if CaseContextSnapshot.model_validate_json(existing["payload_json"]) != snapshot:
                raise ValueError("cannot replace an immutable case-context snapshot")
            continue
        connection.execute(
            "INSERT INTO team_case_context_snapshots VALUES (?, ?)", (snapshot.id, serialized)
        )
        index_snapshot(connection, {**payload, "view_id": snapshot.view.view_id})


class CaseContextSnapshotRepository:
    def __init__(self, store: TeamStateSqliteStore) -> None:
        self._store = store

    @staticmethod
    def stage(state: TeamState, snapshot: CaseContextSnapshot) -> None:
        """Stage only new history in the enclosing revision-checked state transaction."""
        existing = state._case_context_snapshot_writes.get(snapshot.id)
        if existing is not None and existing != snapshot:
            raise ValueError("cannot replace a staged case-context snapshot")
        state._case_context_snapshot_writes[snapshot.id] = snapshot

    def get(
        self, snapshot_id: str | None, *, state: TeamState | None = None
    ) -> CaseContextSnapshot | None:
        if snapshot_id is None:
            return None
        if state is not None and snapshot_id in state._case_context_snapshot_writes:
            return state._case_context_snapshot_writes[snapshot_id]
        with self._store._read_connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM team_case_context_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        return CaseContextSnapshot.model_validate_json(row["payload_json"]) if row else None

    def ids_for_dispatch(
        self, dispatch_id: str | None, *, state: TeamState | None = None
    ) -> list[str]:
        if dispatch_id is None:
            return []
        with self._store._read_connection() as connection:
            ids = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM team_case_context_snapshot_index WHERE dispatch_id = ?",
                    (dispatch_id,),
                )
            }
        if state is not None:
            ids.update(
                s.id
                for s in state._case_context_snapshot_writes.values()
                if s.dispatch_id == dispatch_id
            )
        return sorted(ids)

    def for_dispatch(
        self, dispatch_id: str, *, state: TeamState | None = None
    ) -> list[CaseContextSnapshot]:
        with self._store._read_connection() as connection:
            snapshots = {
                row["id"]: CaseContextSnapshot.model_validate_json(row["payload_json"])
                for row in connection.execute(
                    "SELECT s.id, s.payload_json FROM team_case_context_snapshot_index i "
                    "JOIN team_case_context_snapshots s ON s.id = i.id WHERE i.dispatch_id = ?",
                    (dispatch_id,),
                )
            }
        if state is not None:
            snapshots.update(
                (s.id, s)
                for s in state._case_context_snapshot_writes.values()
                if s.dispatch_id == dispatch_id
            )
        return [snapshots[key] for key in sorted(snapshots)]

    def previous_id(
        self, dispatch_completion_times: Mapping[str, datetime], *, state: TeamState
    ) -> str | None:
        """Select by completion time then ID without loading any historical evidence."""
        dispatch_ids = list(dispatch_completion_times)
        best = None
        with self._store._read_connection() as connection:
            for start in range(0, len(dispatch_ids), 200):
                batch = dispatch_ids[start : start + 200]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    "SELECT dispatch_id, max(id) AS id FROM team_case_context_snapshot_index "
                    f"WHERE dispatch_id IN ({placeholders}) GROUP BY dispatch_id",
                    batch,
                )
                for row in rows:
                    candidate = (dispatch_completion_times[row["dispatch_id"]], row["id"])
                    best = max(best, candidate) if best else candidate
        for snapshot in state._case_context_snapshot_writes.values():
            if snapshot.dispatch_id in dispatch_completion_times:
                candidate = (dispatch_completion_times[snapshot.dispatch_id], snapshot.id)
                best = max(best, candidate) if best else candidate
        return best[1] if best else None

    def list_for_case(
        self, case_id: str, *, directory_path: str, cursor: int = 0, limit: int = 20
    ) -> dict[str, Any]:
        if cursor < 0 or not 1 <= limit <= 50:
            raise ValueError("invalid snapshot page")
        with self._store._read_connection() as connection:
            rows = connection.execute(
                "SELECT i.id, i.view_id, i.source_revision, i.created_at "
                "FROM team_case_context_snapshot_cases c "
                "JOIN team_case_context_snapshot_index i ON i.id = c.snapshot_id "
                "WHERE c.case_id = ? AND (i.directory_path = ? "
                "OR substr(i.directory_path, 1, length(?) + 1) = ? || '/') "
                "ORDER BY c.created_at_utc DESC, c.snapshot_id DESC LIMIT ? OFFSET ?",
                (case_id, directory_path, directory_path, directory_path, limit + 1, cursor),
            ).fetchall()
        return {
            "records": [
                {**dict(row), "created_at": datetime.fromisoformat(row["created_at"]).isoformat()}
                for row in rows[:limit]
            ],
            "next_cursor": cursor + limit if len(rows) > limit else None,
        }
