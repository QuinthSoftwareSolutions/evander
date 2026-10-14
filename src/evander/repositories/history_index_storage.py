"""Physical scalar storage behind the stable execution-history contract.

Existing databases retain their text-keyed tables until explicitly converted
offline. The interned format stores repeated keys once; application reads and
writes use integer IDs, while views remain available for operator inspection.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

EXECUTIONS = "workflow_history_execution_keys"
GENERATIONS = "workflow_history_generations"
PATHS = "workflow_history_paths"
CURRENT = "workflow_history_index_entries"
LEGACY = "workflow_history_legacy_entries"
OLD_CURRENT = "workflow_history_index_values"
OLD_LEGACY = "workflow_node_execution_history_values"
FORMAT_KEY = "history_scalar_storage"
VALUE_COLUMNS = "value_type,value_hash,text_value,numeric_value,boolean_value"
PUBLIC_COLUMNS = "execution_key,location,json_pointer," + VALUE_COLUMNS


def uses_interned_history(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (CURRENT,)
        ).fetchone()
        is not None
    )


def ensure_legacy_values_schema(connection: sqlite3.Connection) -> None:
    connection.execute(f"""CREATE TABLE IF NOT EXISTS {OLD_LEGACY}(
        execution_key TEXT NOT NULL, location TEXT NOT NULL, json_pointer TEXT NOT NULL,
        value_type TEXT NOT NULL, value_hash TEXT, text_value TEXT, numeric_value REAL,
        boolean_value INTEGER, PRIMARY KEY(execution_key,location,json_pointer),
        FOREIGN KEY(execution_key) REFERENCES workflow_node_execution_history(execution_key)
        ON DELETE CASCADE)""")
    for suffix, column in (("lookup", "value_hash"), ("numeric", "numeric_value")):
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_workflow_node_history_values_{suffix} "
            f"ON {OLD_LEGACY}(location,json_pointer,value_type,{column},execution_key)"
        )


def ensure_interned_history_schema(connection: sqlite3.Connection, *, indexes: bool = True) -> None:
    connection.execute(f"""CREATE TABLE IF NOT EXISTS {EXECUTIONS}(
        id INTEGER PRIMARY KEY, value TEXT NOT NULL UNIQUE,
        FOREIGN KEY(value) REFERENCES workflow_node_execution_history(execution_key)
        ON DELETE CASCADE)""")
    for table in (GENERATIONS, PATHS):
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {table}(id INTEGER PRIMARY KEY,value TEXT NOT NULL UNIQUE)"
        )
    for table, current in ((CURRENT, True), (LEGACY, False)):
        generation = (
            f"generation_ref INTEGER NOT NULL REFERENCES {GENERATIONS}(id)," if current else ""
        )
        connection.execute(f"""CREATE TABLE IF NOT EXISTS {table}(
            rowid INTEGER PRIMARY KEY,
            execution_ref INTEGER NOT NULL REFERENCES {EXECUTIONS}(id) ON DELETE CASCADE,
            {generation} location TEXT NOT NULL, path_ref INTEGER NOT NULL REFERENCES {PATHS}(id),
            value_type TEXT NOT NULL,value_hash TEXT,text_value TEXT,numeric_value REAL,boolean_value INTEGER)""")
        if not indexes:
            continue
        prefix = "idx_history_entries" if current else "idx_history_legacy_entries"
        generation_key = "generation_ref," if current else ""
        generation_tail = ",generation_ref" if current else ""
        definitions = [
            ("key", f"execution_ref,{generation_key}location,path_ref", "", True),
            (
                "lookup",
                f"location,path_ref,value_type,value_hash,execution_ref{generation_tail}",
                "",
                False,
            ),
            (
                "numeric",
                f"location,path_ref,value_type,numeric_value,execution_ref{generation_tail}",
                " WHERE value_type='number'",
                False,
            ),
            (
                "exact",
                f"location,path_ref,value_type,text_value,execution_ref{generation_tail}",
                " WHERE value_type='number'",
                False,
            ),
            ("path", "path_ref", "", False),
        ]
        if current:
            definitions.append(("generation", "generation_ref", "", False))
        for suffix, columns, where, unique in definitions:
            connection.execute(
                f"CREATE {'UNIQUE ' if unique else ''}INDEX IF NOT EXISTS {prefix}_{suffix} "
                f"ON {table}({columns}){where}"
            )


def create_history_views(connection: sqlite3.Connection) -> None:
    for name in ("workflow_node_execution_current_values", OLD_CURRENT, OLD_LEGACY):
        connection.execute(f"DROP VIEW IF EXISTS {name}")
    for name, table, current in ((OLD_CURRENT, CURRENT, True), (OLD_LEGACY, LEGACY, False)):
        generation = "g.value AS generation," if current else ""
        join = f"JOIN {GENERATIONS} g ON g.id=v.generation_ref" if current else ""
        connection.execute(f"""CREATE VIEW {name} AS
            SELECT e.value AS execution_key,{generation}v.location,p.value AS json_pointer,
                   v.value_type,v.value_hash,v.text_value,v.numeric_value,v.boolean_value,v.rowid AS rowid
            FROM {table} v JOIN {EXECUTIONS} e ON e.id=v.execution_ref
            JOIN {PATHS} p ON p.id=v.path_ref {join}""")
    create_current_values_view(connection)


def create_current_values_view(connection: sqlite3.Connection) -> None:
    connection.execute("DROP VIEW IF EXISTS workflow_node_execution_current_values")
    selected = ",".join("v." + column for column in PUBLIC_COLUMNS.split(","))
    connection.execute(f"""CREATE VIEW workflow_node_execution_current_values AS
        SELECT {selected} FROM {OLD_CURRENT} v
        JOIN workflow_history_index_state s ON s.execution_key=v.execution_key AND s.generation=v.generation
        WHERE NOT EXISTS(SELECT 1 FROM workflow_history_index_jobs j WHERE j.execution_key=v.execution_key)
        UNION ALL SELECT {selected} FROM {OLD_LEGACY} v
        WHERE NOT EXISTS(SELECT 1 FROM workflow_history_index_state s WHERE s.execution_key=v.execution_key)
        AND NOT EXISTS(SELECT 1 FROM workflow_history_index_jobs j WHERE j.execution_key=v.execution_key)""")


def interned_schema_is_current(connection: sqlite3.Connection) -> bool:
    objects = {
        (row[0], row[1]) for row in connection.execute("SELECT type,name FROM sqlite_master")
    }
    required = {("table", name) for name in (EXECUTIONS, GENERATIONS, PATHS, CURRENT, LEGACY)}
    required.update(
        ("view", name)
        for name in (OLD_CURRENT, OLD_LEGACY, "workflow_node_execution_current_values")
    )
    for prefix, suffixes in (
        ("idx_history_entries", ("key", "lookup", "numeric", "exact", "path", "generation")),
        ("idx_history_legacy_entries", ("key", "lookup", "numeric", "exact", "path")),
    ):
        required.update(("index", prefix + "_" + suffix) for suffix in suffixes)
    if not required <= objects:
        return False
    row = connection.execute(
        "SELECT value_json FROM orchestration_metadata WHERE key=?", (FORMAT_KEY,)
    ).fetchone()
    try:
        metadata = json.loads(row[0]) if row else {}
        return metadata.get("format") == "interned" and metadata.get("version") == 1
    except (AttributeError, TypeError, ValueError):
        return False


def convert_history_index_storage(
    connection: sqlite3.Connection, *, interned: bool = True
) -> dict[str, Any]:
    """Convert an offline disposable copy, retaining every value and rowid.

    The caller owns commit/rollback and must never run this against a live store.
    Normal initialization deliberately does not invoke this conversion.
    """
    present = uses_interned_history(connection)
    if present == interned:
        return {"changed": False, "format": "interned" if interned else "legacy"}
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (OLD_CURRENT, OLD_LEGACY)
    }
    if interned:
        ensure_interned_history_schema(connection, indexes=False)
        connection.execute(
            f"INSERT INTO {EXECUTIONS}(value) SELECT execution_key FROM workflow_node_execution_history ORDER BY execution_key"
        )
        connection.execute(f"""INSERT INTO {GENERATIONS}(value)
            SELECT generation FROM {OLD_CURRENT}
            UNION SELECT generation FROM workflow_history_index_state
            UNION SELECT input_blob_sha256 || ':' || output_blob_sha256 FROM workflow_history_index_jobs ORDER BY 1""")
        connection.execute(
            f"INSERT INTO {PATHS}(value) SELECT json_pointer FROM {OLD_CURRENT} UNION SELECT json_pointer FROM {OLD_LEGACY} ORDER BY 1"
        )
        for old, new, current in ((OLD_CURRENT, CURRENT, True), (OLD_LEGACY, LEGACY, False)):
            gen_select = "g.id," if current else ""
            gen_join = f"JOIN {GENERATIONS} g ON g.value=v.generation" if current else ""
            connection.execute(f"""INSERT INTO {new}
                SELECT v.rowid,e.id,{gen_select}v.location,p.id,{",".join("v." + c for c in VALUE_COLUMNS.split(","))}
                FROM {old} v JOIN {EXECUTIONS} e ON e.value=v.execution_key
                JOIN {PATHS} p ON p.value=v.json_pointer {gen_join} ORDER BY v.rowid""")
            if connection.execute(f"SELECT COUNT(*) FROM {new}").fetchone()[0] != counts[old]:
                raise ValueError(
                    "History conversion lost a reference; transaction must be rolled back"
                )
        connection.execute("DROP VIEW workflow_node_execution_current_values")
        for old in (OLD_CURRENT, OLD_LEGACY):
            connection.execute(f"DROP TABLE {old}")
        ensure_interned_history_schema(connection)
        create_history_views(connection)
        connection.execute(
            "INSERT INTO orchestration_metadata(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (FORMAT_KEY, json.dumps({"format": "interned", "version": 1})),
        )
    else:
        for old, current in ((OLD_CURRENT, True), (OLD_LEGACY, False)):
            table = old + "_restore"
            gen = "generation TEXT NOT NULL," if current else ""
            gen_key = "generation," if current else ""
            columns = (
                PUBLIC_COLUMNS.replace("execution_key,", "execution_key,generation,", 1)
                if current
                else PUBLIC_COLUMNS
            )
            connection.execute(f"""CREATE TABLE {table}(
                execution_key TEXT NOT NULL REFERENCES workflow_node_execution_history(execution_key) ON DELETE CASCADE,
                {gen}location TEXT NOT NULL,json_pointer TEXT NOT NULL,value_type TEXT NOT NULL,
                value_hash TEXT,text_value TEXT,numeric_value REAL,boolean_value INTEGER,
                PRIMARY KEY(execution_key,{gen_key}location,json_pointer))""")
            connection.execute(
                f"INSERT INTO {table}(rowid,{columns}) SELECT rowid,{columns} FROM {old} ORDER BY rowid"
            )
            if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != counts[old]:
                raise ValueError(
                    "History rollback lost a reference; transaction must be rolled back"
                )
        for view in ("workflow_node_execution_current_values", OLD_CURRENT, OLD_LEGACY):
            connection.execute(f"DROP VIEW {view}")
        for table in (CURRENT, LEGACY, EXECUTIONS, GENERATIONS, PATHS):
            connection.execute(f"DROP TABLE {table}")
        for old in (OLD_CURRENT, OLD_LEGACY):
            connection.execute(f"ALTER TABLE {old}_restore RENAME TO {old}")
        ensure_legacy_values_schema(connection)
        for table, prefix, current in (
            (OLD_CURRENT, "idx_history_generation", True),
            (OLD_LEGACY, "idx_workflow_node_history_values", False),
        ):
            tail = ",generation" if current else ""
            for suffix, column in (
                ("lookup", "value_hash"),
                ("numeric", "numeric_value"),
                ("exact", "text_value"),
            ):
                where = " WHERE value_type='number'" if suffix != "lookup" else ""
                connection.execute(
                    f"CREATE INDEX IF NOT EXISTS {prefix}_{suffix} ON {table}(location,json_pointer,value_type,{column},execution_key{tail}){where}"
                )
        create_current_values_view(connection)
        connection.execute("DELETE FROM orchestration_metadata WHERE key=?", (FORMAT_KEY,))
    return {"changed": True, "format": "interned" if interned else "legacy", "scalar_rows": counts}


def _intern(connection: sqlite3.Connection, table: str, values: Iterable[str]) -> dict[str, int]:
    values = list(dict.fromkeys(values))
    result: dict[str, int] = {}
    # Bound SQLite parameters even for explicit administrative batches.
    for offset in range(0, len(values), 400):
        batch = values[offset : offset + 400]
        placeholders = ",".join("?" for _ in batch)
        result.update(
            (row[1], row[0])
            for row in connection.execute(
                f"SELECT id,value FROM {table} WHERE value IN ({placeholders})", batch
            )
        )
        missing = [value for value in batch if value not in result]
        if missing:
            connection.executemany(
                f"INSERT OR IGNORE INTO {table}(value) VALUES(?)", [(v,) for v in missing]
            )
            result.update(
                (row[1], row[0])
                for row in connection.execute(
                    f"SELECT id,value FROM {table} WHERE value IN ({placeholders})", batch
                )
            )
    return result


def write_history_values(
    connection: sqlite3.Connection, rows: Sequence[tuple[Any, ...]], *, legacy: bool = False
) -> None:
    if not rows:
        return
    if not uses_interned_history(connection):
        table, count = (OLD_LEGACY, 8) if legacy else (OLD_CURRENT, 9)
        connection.executemany(
            f"INSERT OR REPLACE INTO {table} VALUES({','.join('?' for _ in range(count))})", rows
        )
        return
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    executions = _intern(connection, EXECUTIONS, (row[0] for row in rows))
    paths = _intern(connection, PATHS, (row[2 if legacy else 3] for row in rows))
    if legacy:
        values = [(executions[row[0]], row[1], paths[row[2]], *row[3:]) for row in rows]
        columns = "execution_ref,location,path_ref," + VALUE_COLUMNS
        table = LEGACY
    else:
        generations = _intern(connection, GENERATIONS, (row[1] for row in rows))
        values = [
            (executions[row[0]], generations[row[1]], row[2], paths[row[3]], *row[4:])
            for row in rows
        ]
        columns = "execution_ref,generation_ref,location,path_ref," + VALUE_COLUMNS
        table = CURRENT
    connection.executemany(
        f"INSERT OR REPLACE INTO {table}({columns}) VALUES({','.join('?' for _ in values[0])})",
        values,
    )


class HistoryIndexStorage:
    def __init__(self, connection: sqlite3.Connection, paths: Iterable[str] = ()):
        self.connection = connection
        self.interned = uses_interned_history(connection)
        self.path_ids: dict[str, int] = {}
        paths = list(dict.fromkeys(paths))
        for offset in range(0, len(paths), 400):
            batch = paths[offset : offset + 400]
            if self.interned:
                self.path_ids.update(
                    (row[1], row[0])
                    for row in connection.execute(
                        f"SELECT id,value FROM {PATHS} WHERE value IN ({','.join('?' for _ in batch)})",
                        batch,
                    )
                )

    @property
    def search_joins(self) -> str:
        if not self.interned:
            return ""
        return (
            f" LEFT JOIN {EXECUTIONS} he ON he.value=h.execution_key"
            " LEFT JOIN workflow_history_index_state hs ON hs.execution_key=h.execution_key"
            f" LEFT JOIN {GENERATIONS} hg ON hg.value=hs.generation"
        )

    @property
    def search_columns(self) -> str:
        return (
            (
                ",he.id AS history_execution_ref,hg.id AS history_generation_ref,"
                "hs.execution_key AS history_current_state"
            )
            if self.interned
            else ""
        )

    def exists_sql(
        self,
        alias: str,
        location: str,
        path: str,
        comparison: str,
        comparison_params: Sequence[Any] = (),
    ) -> tuple[str, list[Any]]:
        path_id = self.path_ids.get(path)
        if path_id is None:
            return "0", []
        predicate = f"{alias}.location=? AND {alias}.path_ref=?{comparison}"
        # Each branch probes a compact unique key; no text-key compatibility view.
        return (
            f"((hs.execution_key IS NOT NULL AND EXISTS(SELECT 1 FROM {CURRENT} {alias} "
            f"WHERE {alias}.execution_ref=he.id AND {alias}.generation_ref=hg.id AND {predicate})) "
            f"OR (hs.execution_key IS NULL AND EXISTS(SELECT 1 FROM {LEGACY} {alias} "
            f"WHERE {alias}.execution_ref=he.id AND {predicate})))",
            [location, path_id, *comparison_params] * 2,
        )

    def matched_value(self, row: sqlite3.Row, location: str, path: str):
        path_id = self.path_ids.get(path)
        if path_id is None or row["history_execution_ref"] is None:
            return None
        parameters = [row["history_execution_ref"], location, path_id]
        current = row["history_current_state"] is not None
        table = CURRENT if current else LEGACY
        generation = " AND generation_ref=?" if current else ""
        if current:
            parameters.append(row["history_generation_ref"])
        return self.connection.execute(
            f"SELECT value_type,text_value,numeric_value,boolean_value FROM {table} "
            f"WHERE execution_ref=? AND location=? AND path_ref=?{generation}",
            parameters,
        ).fetchone()


def delete_history_values(
    connection: sqlite3.Connection, keys: list[str], limit: int
) -> tuple[int, str]:
    """Drain bounded children, returning a predicate for deleting empty parents."""
    interned = uses_interned_history(connection)
    tables = (LEGACY, CURRENT) if interned else (OLD_LEGACY, OLD_CURRENT)
    parameters: list[Any] = keys
    column = "execution_key"
    if interned:
        parameters = [
            row[0]
            for row in connection.execute(
                f"SELECT id FROM {EXECUTIONS} WHERE value IN ({','.join('?' for _ in keys)})", keys
            )
        ]
        column = "execution_ref"
    removed = 0
    if parameters:
        placeholders = ",".join("?" for _ in parameters)
        for table in tables:
            if removed >= limit:
                break
            removed += connection.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                f"WHERE {column} IN ({placeholders}) LIMIT ?)",
                [*parameters, limit - removed],
            ).rowcount
    reference = (
        f"(SELECT id FROM {EXECUTIONS} WHERE value=h.execution_key)"
        if interned
        else "h.execution_key"
    )
    empty = " AND ".join(
        f"NOT EXISTS(SELECT 1 FROM {table} v WHERE v.{column}={reference})" for table in tables
    )
    return removed, empty


def scalar_gc_components(connection: sqlite3.Connection):
    if not uses_interned_history(connection):
        return (
            (
                "current",
                OLD_CURRENT,
                "NOT EXISTS(SELECT 1 FROM workflow_history_index_state s WHERE s.execution_key=v.execution_key AND s.generation=v.generation) AND NOT EXISTS(SELECT 1 FROM workflow_history_index_jobs j WHERE j.execution_key=v.execution_key AND j.input_blob_sha256 || ':' || j.output_blob_sha256=v.generation)",
            ),
            (
                "legacy",
                OLD_LEGACY,
                "EXISTS(SELECT 1 FROM workflow_history_index_state s WHERE s.execution_key=v.execution_key)",
            ),
        )
    return (
        (
            "current",
            CURRENT,
            f"NOT EXISTS(SELECT 1 FROM {EXECUTIONS} e JOIN workflow_history_index_state s ON s.execution_key=e.value JOIN {GENERATIONS} g ON g.value=s.generation WHERE e.id=v.execution_ref AND g.id=v.generation_ref) AND NOT EXISTS(SELECT 1 FROM {EXECUTIONS} e JOIN workflow_history_index_jobs j ON j.execution_key=e.value JOIN {GENERATIONS} g ON g.value=j.input_blob_sha256 || ':' || j.output_blob_sha256 WHERE e.id=v.execution_ref AND g.id=v.generation_ref)",
        ),
        (
            "legacy",
            LEGACY,
            f"EXISTS(SELECT 1 FROM {EXECUTIONS} e JOIN workflow_history_index_state s ON s.execution_key=e.value WHERE e.id=v.execution_ref)",
        ),
        (
            "paths",
            PATHS,
            f"NOT EXISTS(SELECT 1 FROM {CURRENT} x WHERE x.path_ref=v.id) AND NOT EXISTS(SELECT 1 FROM {LEGACY} x WHERE x.path_ref=v.id)",
        ),
        (
            "generations",
            GENERATIONS,
            f"NOT EXISTS(SELECT 1 FROM {CURRENT} x WHERE x.generation_ref=v.id)",
        ),
    )


def scalar_gc_complete(connection: sqlite3.Connection, state: dict[str, Any]) -> bool:
    return all(
        state.get(name + "_completed") is True for name, _, _ in scalar_gc_components(connection)
    )


def list_history_field_rows(
    connection: sqlite3.Connection, node_id: str, started_from: str, started_to: str
):
    storage = HistoryIndexStorage(connection)
    if storage.interned:
        columns = "he.id AS execution_ref,hg.id AS generation_ref,hs.execution_key AS current_state"
        current, legacy, key, generation = CURRENT, LEGACY, "execution_ref", "generation_ref"
        path = "path_ref"
        joins = storage.search_joins
        output = (
            f"SELECT c.location,p.value AS json_pointer,c.value_type,c.observed_count "
            f"FROM counts c JOIN {PATHS} p ON p.id=c.path_ref "
            "ORDER BY c.location,p.value,c.value_type LIMIT 1000"
        )
    else:
        columns = "h.execution_key,s.generation,s.execution_key AS current_state"
        current, legacy, key, generation = OLD_CURRENT, OLD_LEGACY, "execution_key", "generation"
        path = "json_pointer"
        joins = " LEFT JOIN workflow_history_index_state s ON s.execution_key=h.execution_key"
        output = "SELECT * FROM counts ORDER BY location,json_pointer,value_type LIMIT 1000"
    # Select the requested executions first. A combined compatibility view can
    # otherwise cause SQLite to index every scalar before applying this window.
    return connection.execute(
        f"""WITH selected AS MATERIALIZED (
        SELECT {columns} FROM workflow_node_execution_history h {joins}
        WHERE h.node_id=? AND h.started_at>=? AND h.started_at<?
        AND NOT EXISTS(SELECT 1 FROM workflow_history_retirements r WHERE r.execution_key=h.execution_key)
        AND NOT EXISTS(SELECT 1 FROM workflow_history_index_jobs j WHERE j.execution_key=h.execution_key)
    ), selected_values AS (
        SELECT v.location,v.{path},v.value_type
        FROM selected h CROSS JOIN {current} v
        WHERE h.current_state IS NOT NULL AND v.{key}=h.{key} AND v.{generation}=h.{generation}
        UNION ALL
        SELECT v.location,v.{path},v.value_type
        FROM selected h CROSS JOIN {legacy} v
        WHERE h.current_state IS NULL AND v.{key}=h.{key}
    ), counts AS (
        SELECT location,{path},value_type,COUNT(*) AS observed_count FROM selected_values
        GROUP BY location,{path},value_type
    ) {output}""",
        (node_id, started_from, started_to),
    ).fetchall()
