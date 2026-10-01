"""Retention must select the oldest eligible sources without scanning the backlog."""

import sqlite3

import pytest

from evander.repositories.execution_history import (
    _retention_candidates,
    _source_payload_bytes,
    prepare_records_for_retention,
)
from evander.repositories.maintenance_indexes import RETENTION_SOURCE_INDEXES
from evander.repositories.orchestration_db import (
    _create_maintenance_indexes,
    connect_orchestration_db,
    ensure_orchestration_schema,
)

CUTOFF = "2026-09-01T00:00:00Z"
OLD = "2026-08-01T00:00:00Z"
RECENT = "2026-09-10T00:00:00Z"


def _trigger(connection, source_id, *, state="delivered", kind="trigger:test", delivered=OLD):
    connection.execute(
        """INSERT INTO trigger_events
        (event_id,delivery_state,trigger_id,trigger_type,trace_id,occurred_at,delivered_at,payload_json)
        VALUES (?,?, 'source',?,'trace',?,?, '{}')""",
        (source_id, state, kind, OLD, delivered),
    )


def _execution(connection, kind, source_id, *, status="succeeded", finished=OLD,
               dismissed=None, cleared=None):
    connection.execute(
        f"""INSERT INTO {kind}_executions
        (execution_id,idempotency_key,event_id,trace_id,source_node_id,source_trigger_type,
         status,{kind}_id,{kind}_type,started_at,finished_at,dismissed_at,cleared_at,payload_json)
        VALUES (?,?,'event','trace','node','trigger:test',?,'node','test',?,?,?,?, '{{}}')""",
        (source_id, source_id, status, OLD, finished, dismissed, cleared),
    )


def test_snapshot_preserves_delivery_completion_acknowledgement_and_cutoff_rules(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        _trigger(connection, "trigger-old")
        _trigger(connection, "transport-old", kind="internal:message")
        _trigger(connection, "occurred-fallback", delivered=None)
        _trigger(connection, "inflight", state="inflight")
        _trigger(connection, "newly-delivered", delivered=RECENT)
        _trigger(connection, "trigger-boundary", delivered=CUTOFF)
        for kind in ("effect", "gate"):
            for status in ("succeeded", "skipped", "dropped"):
                _execution(connection, kind, f"{kind}-{status}", status=status)
            for status in ("running", "waiting", "failed"):
                _execution(connection, kind, f"{kind}-{status}", status=status)
            _execution(connection, kind, f"{kind}-recent", finished=RECENT)
            _execution(connection, kind, f"{kind}-boundary", finished=CUTOFF)
            _execution(connection, kind, f"{kind}-dismissed", status="failed", dismissed=OLD)
            _execution(connection, kind, f"{kind}-cleared", status="failed", cleared=OLD)
            # Dismissal takes precedence even when clearing is older.
            _execution(connection, kind, f"{kind}-new-dismissal", status="failed",
                       dismissed=RECENT, cleared=OLD)
            _execution(connection, kind, f"{kind}-old-dismissal", status="failed",
                       dismissed=OLD, cleared=RECENT, finished=RECENT)
            _execution(connection, kind, f"{kind}-ack-boundary", status="failed", cleared=CUTOFF)

    batch = prepare_records_for_retention(path, cutoff=CUTOFF, batch_size=100)
    assert [(source.kind, source.source_id) for source in batch.sources] == [
        ("effect", "effect-cleared"), ("effect", "effect-dismissed"),
        ("effect", "effect-dropped"), ("effect", "effect-old-dismissal"),
        ("effect", "effect-skipped"), ("effect", "effect-succeeded"),
        ("gate", "gate-cleared"), ("gate", "gate-dismissed"), ("gate", "gate-dropped"),
        ("gate", "gate-old-dismissal"), ("gate", "gate-skipped"), ("gate", "gate-succeeded"),
        ("trigger", "occurred-fallback"), ("transport", "transport-old"), ("trigger", "trigger-old"),
    ]
    assert prepare_records_for_retention(path, cutoff=OLD, batch_size=1).sources == ()


def test_snapshot_merges_limited_ranges_by_eligibility_then_id_then_kind(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        _trigger(connection, "z-oldest", kind="internal:message", delivered="2026-07-01T00:00:00Z")
        for source_id in ("same", "next", "last"):
            _execution(connection, "effect", source_id)
            _execution(connection, "gate", source_id)
            _trigger(connection, source_id)
        _execution(connection, "effect", "acked", status="failed", cleared="2026-08-20T00:00:00Z")
        _execution(connection, "gate", "acked", status="failed", dismissed="2026-08-10T00:00:00Z")
    first = prepare_records_for_retention(path, cutoff=CUTOFF, batch_size=5)
    assert [(source.kind, source.source_id) for source in first.sources] == [
        ("transport", "z-oldest"), ("effect", "last"), ("gate", "last"),
        ("trigger", "last"), ("effect", "next"),
    ]
    all_sources = prepare_records_for_retention(path, cutoff=CUTOFF, batch_size=100).sources
    assert [source.kind for source in all_sources[-2:]] == ["gate", "effect"]


@pytest.fixture(scope="module")
def large_metadata_database():
    # Keep the population realistic while measuring VM work independently of
    # disk speed. Payload hydration and archival have their own boundary tests.
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE trigger_events(event_id TEXT PRIMARY KEY,trigger_type TEXT,
            delivery_state TEXT,occurred_at TEXT,delivered_at TEXT);
        CREATE TABLE effect_executions(execution_id TEXT PRIMARY KEY,status TEXT,
            started_at TEXT,finished_at TEXT,dismissed_at TEXT,cleared_at TEXT);
        CREATE TABLE gate_executions(execution_id TEXT PRIMARY KEY,status TEXT,
            started_at TEXT,finished_at TEXT,dismissed_at TEXT,cleared_at TEXT);
        CREATE TABLE workflow_node_execution_history(input_blob_sha256 TEXT,output_blob_sha256 TEXT);
        CREATE TABLE internal_event_deliveries(internal_event_row_id INTEGER);
        WITH RECURSIVE rows(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM rows WHERE n<167000)
        INSERT INTO trigger_events
        SELECT printf('%06d',n), CASE WHEN n%2=0 THEN 'trigger:test' ELSE 'internal:test' END,
            'delivered','2026-01-01T00:00:00Z',
            CASE WHEN n%3=0 THEN '2026-08-01T00:00:00Z' ELSE '2026-09-10T00:00:00Z' END FROM rows;
    """)
    for kind in ("effect", "gate"):
        connection.execute(f"""INSERT INTO {kind}_executions
            SELECT event_id, CASE CAST(event_id AS INTEGER)%5
                WHEN 0 THEN 'succeeded' WHEN 1 THEN 'failed' WHEN 2 THEN 'running'
                WHEN 3 THEN 'skipped' ELSE 'failed' END,
                occurred_at,delivered_at,
                CASE WHEN CAST(event_id AS INTEGER)%5=1 THEN delivered_at END,NULL
            FROM trigger_events""")
    _create_maintenance_indexes(connection)
    yield connection
    connection.close()


@pytest.mark.parametrize("batch_size", [1, 100])
@pytest.mark.parametrize("cutoff,expected", [(CUTOFF, True), ("2000-01-01T00:00:00Z", False)])
def test_candidate_search_is_bounded_on_half_a_million_rows(
    large_metadata_database, batch_size, cutoff, expected,
):
    connection = large_metadata_database
    steps = 0

    def budget():
        nonlocal steps
        steps += 100
        return int(steps > 20_000)

    connection.set_progress_handler(budget, 100)
    try:
        candidates = _retention_candidates(connection, cutoff=cutoff, batch_size=batch_size)
    finally:
        connection.set_progress_handler(None, 0)
    assert len(candidates) == (batch_size if expected else 0)
    assert steps <= 20_000
    for source in RETENTION_SOURCE_INDEXES:
        plans = [row[3] for row in connection.execute(
            "EXPLAIN QUERY PLAN " + source.selection_sql, {"cutoff": cutoff, "limit": batch_size},
        )]
        assert any("SEARCH " in plan and source.index.name in plan and "<expr><?" in plan
                   for plan in plans)
        assert not any("SCAN " in plan or "TEMP B-TREE" in plan for plan in plans)


def test_completed_timestamp_falls_back_to_started_at(large_metadata_database):
    connection = large_metadata_database
    connection.execute("INSERT INTO effect_executions VALUES ('fallback','succeeded',?,NULL,NULL,NULL)",
                       ("1999-01-01T00:00:00Z",))
    try:
        candidates = _retention_candidates(connection, cutoff="2000-01-01T00:00:00Z", batch_size=1)
        assert [(row["execution_kind"], row["source_id"]) for row in candidates] == [
            ("effect", "fallback"),
        ]
    finally:
        connection.execute("DELETE FROM effect_executions WHERE execution_id='fallback'")


@pytest.mark.parametrize("referenced", [False, True])
def test_source_byte_budget_looks_up_only_reachable_blobs(tmp_path, referenced):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as connection:
        _execution(connection, "effect", "small")
        connection.execute("""WITH RECURSIVE rows(n) AS
            (SELECT 1 UNION ALL SELECT n+1 FROM rows WHERE n<100000)
            INSERT INTO workflow_operational_blobs
            SELECT printf('%06d',n),'zlib-json-v1',X'',1000000 FROM rows""")
        if referenced:
            connection.executemany(
                "INSERT INTO workflow_operational_blobs VALUES (?,'zlib-json-v1',X'',?)",
                [("root-a", 10), ("root-b", 20), ("shared-child", 30)],
            )
            connection.executemany("INSERT INTO workflow_operational_record_refs VALUES ('effect','small',?)",
                                   [("root-a",), ("root-b",)])
            connection.executemany("INSERT INTO workflow_operational_blob_edges VALUES (?,?)",
                                   [("root-a", "shared-child"), ("root-b", "shared-child")])
        # Global blob scans fail this instruction budget even when their sum
        # would happen to be correct. Shared reachable children count once.
        connection.set_progress_handler(lambda: 1, 2000)
        try:
            size = _source_payload_bytes(connection, kind="effect", source_id="small")
        finally:
            connection.set_progress_handler(None, 0)
    assert size == (62 if referenced else 2)
