"""Shared index definitions and bounded source-retention selection SQL."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MaintenanceIndex:
    name: str
    table: str
    expressions: tuple[str, ...]
    predicate: str | None = None

    @property
    def create_sql(self) -> str:
        sql = f"CREATE INDEX {self.name} ON {self.table}({','.join(self.expressions)})"
        return sql if self.predicate is None else f"{sql} WHERE {self.predicate}"

    def matches_definition(self, sql: str | None) -> bool:
        # SQLite stores the CREATE statement without IF NOT EXISTS. Compare the
        # whole definition: index_info alone cannot verify expressions or WHERE.
        return sql is not None and " ".join(sql.split()) == " ".join(self.create_sql.split())


@dataclass(frozen=True)
class RetentionSourceIndex:
    index: MaintenanceIndex
    kind: str

    @property
    def eligibility_predicate(self) -> str:
        return f"{self.index.predicate} AND {self.index.expressions[0]} < :cutoff"

    @property
    def selection_sql(self) -> str:
        kind = (
            "CASE WHEN trigger_type LIKE 'trigger:%' THEN 'trigger' ELSE 'transport' END"
            if self.kind == "trigger"
            else f"'{self.kind}'"
        )
        timestamp, key = self.index.expressions
        return (
            f"SELECT {kind} execution_kind,{key} source_id,{timestamp} eligibility_at "
            f"FROM {self.index.table} INDEXED BY {self.index.name} "
            f"WHERE {self.eligibility_predicate} "
            f"ORDER BY {timestamp},{key} LIMIT :limit"
        )


RETENTION_SOURCE_INDEXES = (
    RetentionSourceIndex(
        MaintenanceIndex(
            "idx_trigger_retention", "trigger_events",
            ("COALESCE(delivered_at,occurred_at)", "event_id"),
            "delivery_state='delivered'",
        ),
        "trigger",
    ),
    *(
        RetentionSourceIndex(
            MaintenanceIndex(
                f"idx_{kind}_retention_{branch}", f"{kind}_executions",
                (timestamp, "execution_id"), predicate,
            ),
            kind,
        )
        for kind in ("effect", "gate")
        for branch, timestamp, predicate in (
            (
                "completed", "COALESCE(finished_at,started_at)",
                "status IN ('succeeded','skipped','dropped')",
            ),
            (
                "failed", "COALESCE(dismissed_at,cleared_at,finished_at,started_at)",
                "status='failed' AND (dismissed_at IS NOT NULL OR cleared_at IS NOT NULL)",
            ),
        )
    ),
)

MAINTENANCE_INDEXES = (
    MaintenanceIndex(
        "idx_workflow_history_input_blob", "workflow_node_execution_history", ("input_blob_sha256",),
    ),
    MaintenanceIndex(
        "idx_workflow_history_output_blob", "workflow_node_execution_history", ("output_blob_sha256",),
    ),
    MaintenanceIndex(
        "idx_internal_delivery_event", "internal_event_deliveries", ("internal_event_row_id",),
    ),
    *(source.index for source in RETENTION_SOURCE_INDEXES),
)


class MaintenanceIndexesRequired(ValueError):
    """Source selection must wait for the explicit offline index build."""


def retention_predicate(kind: str) -> str:
    if kind in {"trigger", "transport"}:
        comparison = "LIKE" if kind == "trigger" else "NOT LIKE"
        return (
            f"{RETENTION_SOURCE_INDEXES[0].eligibility_predicate} "
            f"AND trigger_type {comparison} 'trigger:%'"
        )
    return "(" + " OR ".join(
        f"({source.eligibility_predicate})"
        for source in RETENTION_SOURCE_INDEXES if source.kind == kind
    ) + ")"
