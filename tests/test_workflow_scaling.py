from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from random import Random
from types import SimpleNamespace

import pytest

from evander.daemons import trigger_orchestrator
from evander.daemons.effect_orchestrator import (
    _cyclic_guaranteed_context_node_ids,
    _guaranteed_context_node_ids,
)
from evander.events import internal, scheduled
from evander.events.internal import InternalEventBus, InternalEventSubscriptionSpec
from evander.events.scheduled import ScheduledInternalEventStore
from evander.repositories.orchestration_db import connect_orchestration_db


def test_pending_subscription_page_work_is_bounded_by_page_not_backlog(tmp_path, monkeypatch):
    path = tmp_path / "orchestration.db"
    bus = InternalEventBus(path)
    spec = InternalEventSubscriptionSpec("listener", "trigger:event", "test.event", {})
    bus.reconcile_subscription(spec, config_revision=None)
    now = datetime.now(UTC).isoformat()
    with connect_orchestration_db(path) as connection:
        connection.executemany(
            "INSERT INTO internal_events(event_id,event_type,created_at,payload_json) "
            "VALUES (?, 'test.event', ?, '{}')",
            [(f"event-{index}", now) for index in range(4000)],
        )
        connection.execute(
            "INSERT INTO internal_event_deliveries "
            "SELECT 'listener',row_id,'pending',?,NULL FROM internal_events",
            (now,),
        )

    operations = 0

    def progress():
        nonlocal operations
        operations += 100
        return 0

    @contextmanager
    def measured_connection(database_path):
        with connect_orchestration_db(database_path) as connection:
            connection.set_progress_handler(progress, 100)
            yield connection

    monkeypatch.setattr(internal, "connect_orchestration_db", measured_connection)
    def match(_event):
        pytest.fail("Pending deliveries must not be rematched")

    page = bus.poll_subscription(spec, matcher=match, limit=10)
    assert [event.event_id for event in page] == [f"event-{index}" for index in range(10)]
    assert operations < 5000
    assert bus.poll_subscription(spec, matcher=match, limit=10) == page
    for event in page[:2]:
        bus.acknowledge_delivery(spec.trigger_id, event.event_id)
    assert [event.event_id for event in bus.poll_subscription(spec, matcher=match, limit=10)] == [
        f"event-{index}" for index in range(2, 12)
    ]


def test_scheduled_publication_pages_skip_payload_hydration_and_resume_after_failure(
    tmp_path, monkeypatch
):
    path = tmp_path / "orchestration.db"
    store = ScheduledInternalEventStore(path)
    bus = InternalEventBus(path)
    now = datetime.now(UTC)
    past = (now - timedelta(hours=1)).isoformat()
    future = (now + timedelta(hours=1)).isoformat()
    with connect_orchestration_db(path) as connection:
        connection.executemany(
            "INSERT INTO scheduled_internal_events"
            "(schedule_id,event_type,run_at,status,created_at,payload_json) "
            "VALUES (?, 'test.event', ?, 'pending', ?, '{}')",
            [(f"schedule-{index:04}", past, past) for index in range(405)]
            + [("future", future, past)],
        )
    monkeypatch.setattr(
        trigger_orchestrator, "build_scheduled_internal_event_store", lambda _config: store
    )
    monkeypatch.setattr(trigger_orchestrator, "build_internal_event_bus", lambda _config: bus)
    monkeypatch.setattr(
        scheduled,
        "_scheduled_from_row",
        lambda _row: pytest.fail("Publication must not hydrate scheduled payloads twice"),
    )
    def publish_batch():
        return trigger_orchestrator.TriggerOrchestratorWorker._publish_due_scheduled_internal_events(
            None, None
        )

    assert publish_batch() == 200
    assert store.list_due_ids(now, limit=2) == ["schedule-0200", "schedule-0201"]
    assert trigger_orchestrator._compute_trigger_cycle_sleep_seconds(
        events_published=0,
        poll_interval_seconds=30,
        jitter_seconds=0,
        next_scheduled_run_at=store.next_pending_run_at(),
    ) == 0
    original_publish = bus.publish_scheduled

    def failing_publish(schedule_id):
        if schedule_id == "schedule-0210":
            raise RuntimeError("temporary failure")
        return original_publish(schedule_id)

    monkeypatch.setattr(bus, "publish_scheduled", failing_publish)
    with pytest.raises(RuntimeError, match="temporary failure"):
        publish_batch()
    assert store.list_due_ids(now, limit=1) == ["schedule-0210"]
    monkeypatch.setattr(bus, "publish_scheduled", original_publish)
    assert publish_batch() == 195
    assert publish_batch() == 0
    events = bus.list_events()
    assert [event.idempotency_key for event in events] == [
        f"scheduled-event:schedule-{index:04}" for index in range(405)
    ]
    assert store.next_pending_run_at() == datetime.fromisoformat(future)


def _graph(edges, *, extra_nodes=()):
    nodes = dict.fromkeys(extra_nodes)
    inbound = {}
    for source, target, allow_cycle in edges:
        nodes[source] = nodes[target] = None
        inbound.setdefault(target, []).append(
            {"source_id": source, "target_id": target, "allow_cycle": allow_cycle}
        )
    return SimpleNamespace(nodes_by_id=nodes, inbound_index=inbound)


def test_merge_context_only_visits_ancestors_and_preserves_shared_path():
    edges = [("start", "shared", False)]
    edges.extend(("shared", branch, False) for branch in ("left", "right"))
    edges.extend((branch, "merge", False) for branch in ("left", "right"))
    edges.append(("merge", "shared", True))
    edges.extend((f"other-{i}", f"other-{i+1}", False) for i in range(1000))
    graph = _graph(edges)

    class AncestorIndex(dict):
        reads = 0

        def get(self, node_id, default=None):
            assert not node_id.startswith("other-")
            self.reads += 1
            return super().get(node_id, default)

        def values(self):
            pytest.fail("Merge context must not scan unrelated workflow graphs")

    graph.inbound_index = AncestorIndex(graph.inbound_index)
    assert _guaranteed_context_node_ids(graph=graph, node_id="merge") == {"start", "shared"}
    assert graph.inbound_index.reads == 5
    # A graph edit must affect the next result; no mutable graph cache is retained.
    graph.inbound_index["merge"].append({"source_id": "start", "target_id": "merge"})
    assert _guaranteed_context_node_ids(graph=graph, node_id="merge") == {"start"}


def test_merge_context_handles_long_chains_and_separate_roots():
    # Reverse lexical order made the old fixed-point implementation need one
    # whole-graph pass per node before the target's dominators converged.
    chain = [f"node-{index:04}" for index in reversed(range(1500))]
    graph = _graph([(source, target, False) for source, target in zip(chain, chain[1:])])
    assert _guaranteed_context_node_ids(graph=graph, node_id=chain[-1]) == set(chain[:-1])
    graph = _graph([("one", "merge", False), ("two", "merge", False)])
    assert _guaranteed_context_node_ids(graph=graph, node_id="merge") == set()
    assert _guaranteed_context_node_ids(graph=graph, node_id="missing") == set()


def test_merge_dominator_tree_matches_fixed_point_for_varied_dags():
    random = Random(421)
    for _ in range(40):
        nodes = [f"node-{index}" for index in range(20)]
        random.shuffle(nodes)
        edges = []
        for index, source in enumerate(nodes):
            for target in nodes[index + 1 :]:
                if random.random() < 0.15:
                    edges.append((source, target, False))
                if random.random() < 0.05:
                    edges.append((target, source, True))
        graph = _graph(edges, extra_nodes=nodes)
        for node_id in nodes:
            assert _guaranteed_context_node_ids(graph=graph, node_id=node_id) == (
                _cyclic_guaranteed_context_node_ids(graph=graph, node_id=node_id)
            )
