"""Consistent, indexed inputs for incrementally refreshed usage reports.

The change table keeps one row per identity, not one row per write. Tombstones
allow an existing report to observe deletions without retaining usage payloads.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from .models import ModelInferenceUsageEvent, ModelUsageCorrelationContext


def ensure_cost_report_tracking(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS model_usage_report_clock (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            generation TEXT NOT NULL,
            revision INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO model_usage_report_clock VALUES (1, lower(hex(randomblob(16))), 0);
        CREATE TABLE IF NOT EXISTS model_usage_report_changes (
            kind TEXT NOT NULL,
            identity TEXT NOT NULL,
            revision INTEGER NOT NULL,
            PRIMARY KEY (kind, identity)
        );
        CREATE INDEX IF NOT EXISTS idx_model_usage_report_changes_revision
            ON model_usage_report_changes(revision);
        """
    )
    # Identifiers below are fixed internal constants, never user input.
    for table, kind, identity in (
        ("model_usage_events", "event", "event_id"),
        ("model_usage_correlation_contexts", "context", "dispatch_id"),
    ):
        for operation, reference in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
            condition = ""
            if operation == "UPDATE":
                fields = (
                    ("payload_json",) if kind == "event" else ("team_id", "project_id", "agent_id")
                )
                condition = "WHEN " + " OR ".join(
                    f"OLD.{field} IS NOT NEW.{field}" for field in fields
                )
            connection.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_report_{operation.lower()}
                AFTER {operation} ON {table} {condition}
                BEGIN
                    UPDATE model_usage_report_clock SET revision = revision + 1 WHERE singleton = 1;
                    INSERT INTO model_usage_report_changes(kind, identity, revision)
                    VALUES ('{kind}', {reference}.{identity},
                        (SELECT revision FROM model_usage_report_clock WHERE singleton = 1))
                    ON CONFLICT(kind, identity) DO UPDATE SET revision = excluded.revision;
                END
                """
            )


class CostReportSnapshot:
    """A read transaction held while one report revision is assembled."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        row = connection.execute(
            "SELECT generation, revision FROM model_usage_report_clock WHERE singleton = 1"
        ).fetchone()
        self.generation = str(row["generation"])
        self.revision = int(row["revision"])

    def changes_since(self, revision: int) -> tuple[set[str], set[str]]:
        events: set[str] = set()
        contexts: set[str] = set()
        for row in self.connection.execute(
            "SELECT kind, identity FROM model_usage_report_changes WHERE revision > ?",
            (revision,),
        ):
            (events if row["kind"] == "event" else contexts).add(row["identity"])
        return events, contexts

    def events(self) -> Iterable[ModelInferenceUsageEvent]:
        for row in self.connection.execute(
            "SELECT payload_json FROM model_usage_events ORDER BY occurred_at, event_id"
        ):
            yield ModelInferenceUsageEvent.model_validate_json(row["payload_json"])

    def event_ids_for(self, *, field: str, values: Iterable[str]) -> set[str]:
        if field not in {"session_id", "dispatch_id"}:
            raise ValueError("unsupported cost report lookup")
        result: set[str] = set()
        for value in values:
            result.update(
                row["event_id"]
                for row in self.connection.execute(
                    f"SELECT event_id FROM model_usage_events WHERE {field} = ?", (value,)
                )
            )
        return result

    def event(self, event_id: str) -> ModelInferenceUsageEvent | None:
        row = self.connection.execute(
            "SELECT payload_json FROM model_usage_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return ModelInferenceUsageEvent.model_validate_json(row["payload_json"]) if row else None

    def context(self, dispatch_id: str) -> ModelUsageCorrelationContext | None:
        row = self.connection.execute(
            "SELECT payload_json FROM model_usage_correlation_contexts WHERE dispatch_id = ?",
            (dispatch_id,),
        ).fetchone()
        return (
            ModelUsageCorrelationContext.model_validate_json(row["payload_json"]) if row else None
        )
