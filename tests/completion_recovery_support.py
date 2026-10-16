"""Synthetic execution history and work limits for completion-recovery tests."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime

from evander.daemons.effect_orchestrator import EffectExecutionRecord, GateExecutionRecord
from evander.repositories import orchestration
from evander.repositories.orchestration_db import connect_orchestration_db


def completion_record(kind, key, *, status="completion_pending", at=None):
    at = at or datetime(2026, 1, 1, tzinfo=UTC)
    model = EffectExecutionRecord if kind == "effect" else GateExecutionRecord
    return model(
        execution_id=key,
        idempotency_key=key,
        event_id="event-" + key,
        trace_id="trace",
        source_node_id="source",
        source_trigger_type="trigger:test",
        status=status,
        started_at=at,
        finished_at=at,
        resolved_config_hash="hash",
        outputs={"success": True},
        **{f"{kind}_id": "node", f"{kind}_type": f"{kind}:test"},
    )


def seed_completed_executions(path, *, kind, count=6000):
    """Bulk insert valid legacy records without unnecessary history indexing."""
    template = completion_record(kind, "template").model_dump(mode="json")
    columns = (
        "execution_id", "idempotency_key", "event_id", "trace_id", "source_node_id",
        "source_trigger_type", "status", f"{kind}_id", f"{kind}_type", "started_at",
        "finished_at",
    )

    def rows():
        for number in range(count):
            key = f"historical-{number:05d}"
            payload = {
                **template,
                "execution_id": key,
                "idempotency_key": key,
                "event_id": "event-" + key,
                "status": ("succeeded", "failed", "waiting", "running")[number % 4],
            }
            yield (*[payload[name] for name in columns], json.dumps(payload))

    with connect_orchestration_db(path) as connection:
        connection.executemany(
            f"INSERT INTO {kind}_executions ({','.join(columns)},payload_json) "
            f"VALUES ({','.join('?' for _ in range(len(columns) + 1))})",
            rows(),
        )


@contextmanager
def bounded_completion_queries(monkeypatch):
    """An execution-table scan exceeds this deterministic SQLite VM budget."""
    original = orchestration.connect_orchestration_db_read_only
    statements = []

    @contextmanager
    def bounded(path):
        with original(path) as connection:
            connection.set_progress_handler(lambda: 1, 2000)
            connection.set_trace_callback(statements.append)
            try:
                yield connection
            finally:
                connection.set_progress_handler(None, 0)
                connection.set_trace_callback(None)

    with monkeypatch.context() as patch:
        patch.setattr(orchestration, "connect_orchestration_db_read_only", bounded)
        yield statements


def assert_completion_query_plans(connection, statements, *, kind):
    queries = [sql for sql in statements if "completion_pending" in sql]
    assert queries
    for sql in queries:
        plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql)]
        assert any(f"SEARCH {kind}_executions USING INDEX idx_{kind}_executions_status " in row
                   for row in plan)
        assert not any("SCAN " in row or "TEMP B-TREE" in row for row in plan)
