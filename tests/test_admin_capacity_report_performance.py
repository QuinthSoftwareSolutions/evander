from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from evander.admin.payloads import (
    CapacityHealthPayload,
    CapacityWorkerTelemetryEntryPayload,
    CapacityWorkerTelemetrySummaryPayload,
    WorkflowEditorDocumentPayload,
    WorkflowEditorGraphMetadata,
    WorkflowGraphActivityReportPayload,
)
from evander.admin.services import capacity_health_service, snapshot_cache, workflow_reports_service
from evander.admin.services.capacity_health_service import CapacityHealthAdminService
from evander.admin.services.snapshot_cache import SnapshotCache
from evander.admin.services.workflow_reports_service import (
    WorkflowReportsAdminDependencies,
    WorkflowReportsAdminService,
)
from evander.capacity_health import TelemetryStatus
from evander.execution.container_worker_client import ContainerWorkerClient
from evander.repositories.orchestration_db import ensure_orchestration_schema
from evander.worker.models import WorkerTelemetryPayload
from evander.admin.web import capacity, reports


def test_snapshot_cache_coalesces_concurrent_readers_and_retries_failures(monkeypatch) -> None:
    cache = SnapshotCache[int](ttl_seconds=30)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    all_waiting = threading.Event()
    waiters = []
    waiter_lock = threading.Lock()

    class TrackedFuture(Future):
        def result(self, timeout=None):
            with waiter_lock:
                waiters.append(True)
                if len(waiters) == 3:
                    all_waiting.set()
            return super().result(timeout=timeout)

    monkeypatch.setattr(snapshot_cache, "Future", TrackedFuture)

    def collect() -> int:
        calls.append(True)
        entered.set()
        assert release.wait(2)
        return 42

    with ThreadPoolExecutor(max_workers=4) as executor:
        first = executor.submit(cache.read, "one", collect)
        assert entered.wait(2)
        others = [executor.submit(cache.read, "one", collect) for _ in range(3)]
        assert all_waiting.wait(2)
        release.set()
        assert [future.result(timeout=2) for future in [first, *others]] == [42] * 4
    assert calls == [True]

    def fail() -> int:
        raise ValueError("collection failed")

    with pytest.raises(ValueError, match="collection failed"):
        cache.read("failure", fail)
    assert cache.read("failure", lambda: 7) == 7


def test_capacity_snapshot_expires_refreshes_and_keeps_original_timestamp(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(snapshot_cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    service = CapacityHealthAdminService(SimpleNamespace())
    now = datetime(2026, 9, 22, tzinfo=UTC)
    calls = []

    def collect() -> CapacityHealthPayload:
        calls.append(True)
        return CapacityHealthPayload.model_construct(
            generated_at=now + timedelta(seconds=clock[0]),
            workers=CapacityWorkerTelemetrySummaryPayload(),
        )

    monkeypatch.setattr(service, "_collect_capacity_health", collect)
    first = service.read_capacity_health()
    first.workers.entries.append(CapacityWorkerTelemetryEntryPayload(worker_url="http://mutated"))
    clock[0] = 29
    cached = service.read_capacity_health()
    assert cached.generated_at == now
    assert cached.workers.entries == []
    assert len(calls) == 1
    forced = service.read_capacity_health(refresh=True)
    assert forced.generated_at == now + timedelta(seconds=29)
    clock[0] = 60
    assert service.read_capacity_health().generated_at == now + timedelta(seconds=60)
    assert len(calls) == 3


def test_capacity_worker_probes_are_concurrent_and_timeout_is_capped(monkeypatch) -> None:
    probes = threading.Barrier(2)
    constructors = []
    now = datetime(2026, 9, 22, tzinfo=UTC)

    class Client:
        def __init__(self, **kwargs):
            constructors.append(kwargs)

        def health(self):
            probes.wait(timeout=2)
            return {"status": "ok"}

        def telemetry(self):
            probes.wait(timeout=2)
            return WorkerTelemetryPayload.model_construct(generated_at=now)

    monkeypatch.setattr(capacity_health_service, "ContainerWorkerClient", Client)
    service = CapacityHealthAdminService(SimpleNamespace())
    entry = service._capacity_worker_telemetry_entry(
        {"worker_url": "http://worker", "token_env": "WORKER_TOKEN", "timeout_seconds": 120}
    )
    assert constructors[0]["timeout_seconds"] == 5
    assert constructors[0]["max_connect_attempts"] == 1
    assert entry.healthz_status is TelemetryStatus.AVAILABLE
    assert entry.telemetry_status is TelemetryStatus.AVAILABLE
    assert entry.last_heartbeat_at == now


def test_capacity_worker_timeout_does_not_retry(monkeypatch) -> None:
    attempts = []
    monkeypatch.setenv("CAPACITY_TEST_WORKER_TOKEN", "test-token")

    def send_once(self, method, path, **kwargs):
        attempts.append(path)
        raise httpx.ConnectTimeout("probe timed out")

    monkeypatch.setattr(ContainerWorkerClient, "_send_once", send_once)
    service = CapacityHealthAdminService(SimpleNamespace())
    entry = service._capacity_worker_telemetry_entry(
        {"worker_url": "http://worker", "token_env": "CAPACITY_TEST_WORKER_TOKEN"}
    )
    assert sorted(attempts) == ["/healthz", "/v1/telemetry"]
    assert entry.healthz_status is TelemetryStatus.MISSING
    assert entry.telemetry_status is TelemetryStatus.MISSING
    assert not entry.reachable


def test_capacity_different_workers_are_probed_concurrently_in_descriptor_order(
    monkeypatch,
) -> None:
    workers = threading.Barrier(2)
    descriptors = [{"worker_url": "http://second"}, {"worker_url": "http://first"}]
    service = CapacityHealthAdminService(SimpleNamespace())
    monkeypatch.setattr(service, "_capacity_worker_descriptors", lambda sessions: descriptors)

    def probe(descriptor):
        workers.wait(timeout=2)
        return CapacityWorkerTelemetryEntryPayload(worker_url=descriptor["worker_url"])

    monkeypatch.setattr(service, "_capacity_worker_telemetry_entry", probe)
    assert [entry.worker_url for entry in service._capacity_worker_telemetry_entries([])] == [
        "http://second",
        "http://first",
    ]


@pytest.fixture
def activity_service(tmp_path: Path):
    db_path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(db_path)
    document = WorkflowEditorDocumentPayload(
        config_path="config.json",
        layout_path="layout.json",
        config_exists=True,
        layout_exists=True,
        layout={},
        config={"triggers": [{"id": "trigger", "display_name": "Trigger"}]},
        graphs=[
            {
                "id": "graph",
                "name": "Graph",
                "directory_path": "root",
                "node_ids": ["trigger", "effect", "gate"],
            }
        ],
    )
    admin = SimpleNamespace(
        _read_composed_workflow_editor_document=lambda: document,
        _workflow_run_store_paths=lambda: {"orchestration_store_path": db_path},
        _workflow_editor_revision=lambda **kwargs: json.dumps(
            {**kwargs, "graphs": [graph.model_dump(mode="json") for graph in kwargs["graphs"]]},
            sort_keys=True,
        ),
    )
    service = WorkflowReportsAdminService(
        WorkflowReportsAdminDependencies(
            project_dir=tmp_path,
            config=SimpleNamespace(),
            runtime=SimpleNamespace(),
            workflow_config_admin=admin,
            list_agents=lambda **kwargs: [],
        )
    )
    return service, db_path, document


def _insert_trigger(db_path: Path, event_id: str, *, trace_id: str = "trace", hour: int = 10):
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """INSERT INTO trigger_events (
                event_id, delivery_state, trigger_id, trigger_type, trace_id, occurred_at, payload_json
            ) VALUES (?, 'delivered', 'trigger', 'trigger:test', ?, ?, '{}')""",
            (event_id, trace_id, f"2026-09-22T{hour:02d}:00:00Z"),
        )


def test_graph_report_scans_source_activity_once_and_preserves_distinct_runs(
    activity_service, monkeypatch
) -> None:
    service, db_path, _ = activity_service
    _insert_trigger(db_path, "event-one")
    _insert_trigger(db_path, "event-two", hour=11)
    _insert_trigger(db_path, "event-without-trace", trace_id="", hour=12)
    for kind in ("effect", "gate"):
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                f"""INSERT INTO {kind}_executions (
                    execution_id, idempotency_key, event_id, trace_id, source_node_id,
                    source_trigger_type, status, {kind}_id, {kind}_type,
                    started_at, finished_at, payload_json
                ) VALUES (?, ?, 'event-one', 'trace', 'trigger', 'trigger:test',
                    'succeeded', ?, ?, '2026-09-22T10:01:00Z', '2026-09-22T10:02:00Z', '{{}}')""",
                (kind, kind, kind, f"{kind}:test"),
            )
    statements = []
    original_connect = workflow_reports_service.connect_orchestration_db

    @contextmanager
    def connect(path):
        with original_connect(path) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(workflow_reports_service, "connect_orchestration_db", connect)
    report = service.read_workflow_graph_activity_report(
        period_start=datetime(2026, 9, 22, 10, tzinfo=UTC),
        period_end=datetime(2026, 9, 22, 12, tzinfo=UTC),
    )
    graph = report.graphs[0]
    assert graph.activity_count == 5
    assert graph.run_count == 2
    assert graph.trigger_event_count == 3
    assert graph.effect_execution_count == graph.gate_execution_count == 1
    assert graph.status_counts == {"delivered": 3, "succeeded": 2}
    assert graph.first_activity_at == datetime(2026, 9, 22, 10, tzinfo=UTC)
    assert graph.latest_activity_at == datetime(2026, 9, 22, 12, tzinfo=UTC)
    activity_reads = [statement for statement in statements if "WITH activity AS" in statement]
    assert len(activity_reads) == 1
    assert "AS MATERIALIZED" in activity_reads[0]


def test_graph_default_snapshot_is_reused_but_explicit_period_and_refresh_are_fresh(
    activity_service, monkeypatch
) -> None:
    service, db_path, document = activity_service
    now = datetime(2026, 9, 22, 12, tzinfo=UTC)
    monkeypatch.setattr(workflow_reports_service, "_utc_now", lambda: now)
    _insert_trigger(db_path, "first")
    first = service.read_workflow_graph_activity_report()
    assert first.graphs[0].activity_count == 1
    first.graphs.clear()
    _insert_trigger(db_path, "second")
    assert service.read_workflow_graph_activity_report().graphs[0].activity_count == 1
    explicit = service.read_workflow_graph_activity_report(period_end=now)
    assert explicit.graphs[0].activity_count == 2
    assert service.read_workflow_graph_activity_report(refresh=True).graphs[0].activity_count == 2
    _insert_trigger(db_path, "third")
    document.graphs[0].name = "Renamed"
    changed = service.read_workflow_graph_activity_report()
    assert changed.graphs[0].activity_count == 3
    assert changed.graphs[0].name == "Renamed"
    assert changed.period_end == changed.generated_at == now


def test_graph_aggregate_payload_matches_legacy_two_query_report(activity_service, monkeypatch):
    service, db_path, document = activity_service
    now = datetime(2026, 9, 22, 13, tzinfo=UTC)
    monkeypatch.setattr(workflow_reports_service, "_utc_now", lambda: now)
    document.graphs.append(
        WorkflowEditorGraphMetadata(
            id="other",
            name="Other",
            directory_path="root/other",
            node_ids=["trigger-other", "effect"],
            user_ids=["other-user"],
        )
    )
    for event_id, trace_id, hour in [
        ("shared-one", "same-trace", 10),
        ("shared-two", "same-trace", 11),
        ("fallback-event", "", 12),
        ("too-early", "excluded", 9),
        ("too-late", "excluded", 13),
        ("other-graph", "same-trace", 11),
        ("unknown-node", "excluded", 11),
    ]:
        _insert_trigger(db_path, event_id, trace_id=trace_id, hour=hour)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE trigger_events SET trigger_id='trigger-other' WHERE event_id='other-graph'"
        )
        connection.execute(
            "UPDATE trigger_events SET trigger_id='unknown' WHERE event_id='unknown-node'"
        )
        for kind, execution, trace_id, event_id, status in [
            ("effect", "effect-success", "same-trace", "shared-one", "succeeded"),
            ("effect", "effect-failure", "same-trace", "shared-one", "failed"),
            ("gate", "fallback-event-gate", "", "fallback-event", "rejected"),
            ("gate", "fallback-execution", "", "", ""),
        ]:
            connection.execute(
                f"""INSERT INTO {kind}_executions (
                    execution_id, idempotency_key, event_id, trace_id, source_node_id,
                    source_trigger_type, status, {kind}_id, {kind}_type,
                    started_at, finished_at, payload_json
                ) VALUES (?, ?, ?, ?, 'trigger', 'trigger:test', ?, ?, ?,
                    '2026-09-22T10:01:00Z', '2026-09-22T10:02:00Z', '{{}}')""",
                (execution, execution, event_id, trace_id, status, kind, f"{kind}:test"),
            )
    period = {
        "period_start": datetime(2026, 9, 22, 10, tzinfo=UTC),
        "period_end": datetime(2026, 9, 22, 12, tzinfo=UTC),
    }
    optimized = service.read_workflow_graph_activity_report(**period)
    original_connect = workflow_reports_service.connect_orchestration_db

    class LegacyAggregationConnection:
        def __init__(self, connection):
            self.connection = connection

        def executemany(self, *args):
            return self.connection.executemany(*args)

        def execute(self, sql, parameters=()):
            if "WITH activity AS MATERIALIZED" not in sql:
                return self.connection.execute(sql, parameters)
            # Reproduce the original two independent activity CTE scans, then
            # adapt their rows to the combined result shape consumed by the report.
            cte = sql.split("SELECT graph_id, node_id, source, status,", 1)[0].replace(
                "AS MATERIALIZED", "AS"
            )
            nodes = self.connection.execute(
                cte
                + """
                SELECT graph_id, node_id, source, status, COUNT(*) AS activity_count,
                       MIN(activity_at) AS first_activity_at, MAX(activity_at) AS latest_activity_at
                FROM activity GROUP BY graph_id, node_id, source, status
            """,
                parameters,
            ).fetchall()
            runs = self.connection.execute(
                cte
                + """
                SELECT graph_id, COUNT(DISTINCT run_key) AS run_count
                FROM activity GROUP BY graph_id
            """,
                parameters,
            ).fetchall()
            return [{**dict(row), "run_count": None} for row in nodes] + [dict(row) for row in runs]

    @contextmanager
    def connect_legacy(path):
        with original_connect(path) as connection:
            yield LegacyAggregationConnection(connection)

    monkeypatch.setattr(workflow_reports_service, "connect_orchestration_db", connect_legacy)
    legacy = service.read_workflow_graph_activity_report(**period)
    assert optimized.model_dump(mode="json") == legacy.model_dump(mode="json")
    assert [
        (graph.graph_id, graph.activity_count, graph.run_count) for graph in optimized.graphs
    ] == [
        ("graph", 7, 3),
        ("other", 1, 1),
    ]


@pytest.mark.parametrize("kind", ["capacity", "report"])
@pytest.mark.parametrize("bypass", ["query", "header"])
def test_snapshot_routes_honor_explicit_freshness_bypass(monkeypatch, kind, bypass):
    now = datetime(2026, 9, 22, tzinfo=UTC)
    observed = []

    def read(**kwargs):
        observed.append(kwargs)
        if kind == "capacity":
            return CapacityHealthPayload.model_construct(generated_at=now)
        return WorkflowGraphActivityReportPayload(
            generated_at=now, period_start=now, period_end=now
        )

    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    service = SimpleNamespace(
        capacity_health_admin=SimpleNamespace(read_capacity_health=read),
        workflow_reports_admin=SimpleNamespace(read_workflow_graph_activity_report=read),
    )
    if kind == "capacity":
        monkeypatch.setattr(capacity, "run_in_threadpool", run_inline)
        route = capacity.capacity_routes(service, None)[0]
    else:
        monkeypatch.setattr(reports, "run_in_threadpool", run_inline)
        route = reports.report_routes(service, None)[0]
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": route.path,
            "query_string": b"_dashboard_refresh=1" if bypass == "query" else b"",
            "headers": [(b"cache-control", b"no-cache")] if bypass == "header" else [],
        }
    )
    assert asyncio.run(route.endpoint(request)).status_code == 200
    assert observed[0]["refresh"] is True
