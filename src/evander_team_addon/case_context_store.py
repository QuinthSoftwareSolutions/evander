"""Additive SQLite persistence for the case-context aggregates."""

import json
import sqlite3
from typing import Any

CONTEXT_TABLES = {
    "notes": "team_notes",
    "case_context_views": "team_case_context_views",
    "case_context_snapshots": "team_case_context_snapshots",
}
CONTEXT_SCHEMA_SQL = "\n".join(
    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, payload_json TEXT NOT NULL);"
    for table in CONTEXT_TABLES.values()
)


class CaseContextStoreMixin:
    @staticmethod
    def _read_case_context(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for field, table in CONTEXT_TABLES.items():
            payload[field] = {
                row["id"]: json.loads(row["payload_json"])
                for row in connection.execute(f"SELECT id, payload_json FROM {table} ORDER BY id")
            }

    @staticmethod
    def _write_context_records(connection, records, table):
        if table not in CONTEXT_TABLES.values():
            raise ValueError("unknown case-context table")
        connection.executemany(
            f"INSERT INTO {table}(id, payload_json) VALUES (?, ?)",
            [
                (row["id"], json.dumps(row, ensure_ascii=False, sort_keys=True))
                for row in records.values()
            ],
        )

    def _write_notes(self, connection, records):
        self._write_context_records(connection, records, "team_notes")

    def _write_case_context_views(self, connection, records):
        self._write_context_records(connection, records, "team_case_context_views")

    def _write_case_context_snapshots(self, connection, records):
        self._write_context_records(connection, records, "team_case_context_snapshots")
