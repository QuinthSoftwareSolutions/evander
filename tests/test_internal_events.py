from __future__ import annotations

import json
from pathlib import Path
from threading import Barrier, Thread

from pytest import MonkeyPatch

from agent_sandbox.events.internal import InternalEventBus
from agent_sandbox.storage import JsonStore


def test_internal_event_publish_uses_single_store_update(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    bus = InternalEventBus(tmp_path / "internal-events.json")

    def fail_write_all(*args, **kwargs) -> None:
        raise AssertionError("publish must not use split read/write store updates")

    monkeypatch.setattr(bus._store, "write_all", fail_write_all)

    event = bus.publish(event_type="custom.operator_event", payload={"value": "ok"})

    assert [stored.event_id for stored in bus.list_events()] == [event.event_id]


def test_concurrent_internal_event_publish_preserves_both_events(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    event_path = tmp_path / "internal-events.json"
    journal_path = tmp_path / "workflow-events.jsonl"
    start = Barrier(3)
    read_barrier = Barrier(2)
    failures: list[str] = []
    original_read_all = JsonStore.read_all

    def delayed_read_all(self):
        items = original_read_all(self)
        if self.path == event_path:
            read_barrier.wait(timeout=2)
        return items

    def publish(value: str) -> None:
        try:
            bus = InternalEventBus(event_path, journal_path=journal_path)
            start.wait(timeout=2)
            bus.publish(event_type="custom.concurrent", payload={"value": value})
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            failures.append(repr(exc))

    with monkeypatch.context() as context:
        context.setattr(JsonStore, "read_all", delayed_read_all)
        threads = [
            Thread(target=publish, args=("one",)),
            Thread(target=publish, args=("two",)),
        ]
        for thread in threads:
            thread.start()
        start.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=3)
            assert not thread.is_alive()

    assert failures == []
    events = InternalEventBus(event_path, journal_path=journal_path).list_events()
    assert sorted(event.payload["value"] for event in events) == ["one", "two"]

    journal_events = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [event["event_type"] for event in journal_events].count(
        "workflow.internal_event.published"
    ) == 2
