import json
import os
import stat
import time
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier, Thread

import pytest
from pydantic import BaseModel

from evander.storage import JsonObjectStore, JsonStore, write_json_atomically


class SampleRecord(BaseModel):
    value: str


def _process_put_key(path: str, key: str, barrier, errors) -> None:
    try:
        barrier.wait(timeout=5)
        store = JsonStore(Path(path), SampleRecord)
        store.put(key, SampleRecord(value=key))
    except BaseException as exc:
        errors.put(repr(exc))
        raise


def _process_update_object_section(path: str, section: str, key: str, barrier, errors) -> None:
    try:
        barrier.wait(timeout=5)
        store = JsonObjectStore(Path(path), {"messages": {}, "cursors": {}})

        def mutate(payload: dict[str, object]) -> None:
            time.sleep(0.05)
            payload[section][key] = section

        store.update_object(mutate)
    except BaseException as exc:
        errors.put(repr(exc))
        raise


def _run_concurrently(*targets) -> None:
    threads = [Thread(target=target) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)
        assert not thread.is_alive()


def test_json_store_put_preserves_concurrent_updates(tmp_path: Path, monkeypatch) -> None:
    store = JsonStore(tmp_path / "store.json", SampleRecord)
    original_write_all = JsonStore.write_all

    def delayed_write(self, items):
        time.sleep(0.05)
        return original_write_all(self, items)

    monkeypatch.setattr(JsonStore, "write_all", delayed_write)
    start = Barrier(3)

    def put_key(key: str) -> None:
        start.wait()
        store.put(key, SampleRecord(value=key))

    _run_concurrently(lambda: put_key("a"), lambda: put_key("b"), lambda: start.wait())

    assert set(store.read_all()) == {"a", "b"}


def test_json_store_delete_preserves_concurrent_updates(tmp_path: Path, monkeypatch) -> None:
    store = JsonStore(tmp_path / "store.json", SampleRecord)
    store.write_all(
        {
            "a": SampleRecord(value="a"),
            "b": SampleRecord(value="b"),
            "c": SampleRecord(value="c"),
        }
    )
    original_write_all = JsonStore.write_all

    def delayed_write(self, items):
        time.sleep(0.05)
        return original_write_all(self, items)

    monkeypatch.setattr(JsonStore, "write_all", delayed_write)
    start = Barrier(3)

    def delete_key(key: str) -> None:
        start.wait()
        store.delete(key)

    _run_concurrently(lambda: delete_key("a"), lambda: delete_key("b"), lambda: start.wait())

    assert set(store.read_all()) == {"c"}


def test_json_store_update_all_preserves_concurrent_updates(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "store.json", SampleRecord)
    start = Barrier(3)

    def update_key(key: str) -> None:
        start.wait()

        def mutate(items: dict[str, SampleRecord]) -> None:
            time.sleep(0.05)
            items[key] = SampleRecord(value=key)

        store.update_all(mutate)

    _run_concurrently(lambda: update_key("a"), lambda: update_key("b"), lambda: start.wait())

    assert set(store.read_all()) == {"a", "b"}


def test_json_store_put_preserves_updates_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "store.json"
    process_count = 4
    context = get_context("spawn")
    start = context.Barrier(process_count + 1)
    errors = context.Queue()
    processes = [
        context.Process(
            target=_process_put_key,
            args=(str(path), f"key-{index}", start, errors),
        )
        for index in range(process_count)
    ]

    for process in processes:
        process.start()
    start.wait(timeout=5)
    for process in processes:
        process.join(timeout=5)

    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        assert process.exitcode == 0
    assert errors.empty()

    store = JsonStore(path, SampleRecord)

    assert set(store.read_all()) == {f"key-{index}" for index in range(process_count)}


def test_json_store_read_all_treats_empty_file_as_empty_store(tmp_path: Path) -> None:
    path = tmp_path / "store.json"
    store = JsonStore(path, SampleRecord)

    path.write_text("", encoding="utf-8")

    assert store.read_all() == {}


def test_json_store_write_all_recovers_from_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "store.json"
    store = JsonStore(path, SampleRecord)
    path.write_text("", encoding="utf-8")

    store.write_all({"a": SampleRecord(value="a")})

    assert store.read_all() == {"a": SampleRecord(value="a")}


def test_json_store_file_mode_applies_to_initial_and_replacement_files(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secret-store.json"
    old_umask = os.umask(0)
    try:
        store = JsonStore(path, SampleRecord, file_mode=0o600)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

        store.write_all({"a": SampleRecord(value="a")})

        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert store.read_all() == {"a": SampleRecord(value="a")}
    finally:
        os.umask(old_umask)


def test_json_object_store_preserves_concurrent_section_updates(tmp_path: Path) -> None:
    path = tmp_path / "object-store.json"
    process_count = 2
    context = get_context("spawn")
    start = context.Barrier(process_count + 1)
    errors = context.Queue()
    processes = [
        context.Process(
            target=_process_update_object_section,
            args=(str(path), section, key, start, errors),
        )
        for section, key in [("messages", "message-a"), ("cursors", "cursor-b")]
    ]

    for process in processes:
        process.start()
    start.wait(timeout=5)
    for process in processes:
        process.join(timeout=5)

    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        assert process.exitcode == 0
    assert errors.empty()

    store = JsonObjectStore(path, {"messages": {}, "cursors": {}})
    assert store.read_object() == {
        "messages": {"message-a": "messages"},
        "cursors": {"cursor-b": "cursors"},
    }


def test_json_object_store_empty_file_behavior_is_configurable(tmp_path: Path) -> None:
    path = tmp_path / "object-store.json"
    store = JsonObjectStore(
        path,
        {"messages": {}, "cursors": {}},
        empty_file_factory=lambda: {"messages": {}, "cursors": {}},
    )

    path.write_text("", encoding="utf-8")

    assert store.read_object() == {"messages": {}, "cursors": {}}

    store.update_object(lambda payload: payload["messages"].__setitem__("a", {"text": "a"}))

    assert store.read_object() == {
        "messages": {"a": {"text": "a"}},
        "cursors": {},
    }


def test_json_object_store_empty_file_without_override_raises(tmp_path: Path) -> None:
    path = tmp_path / "object-store.json"
    store = JsonObjectStore(path, {"messages": {}, "cursors": {}})

    path.write_text("", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        store.read_object()


def test_json_object_store_file_mode_applies_to_initial_and_replacement_files(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secret-object-store.json"
    old_umask = os.umask(0)
    try:
        store = JsonObjectStore(path, {"messages": {}, "cursors": {}}, file_mode=0o600)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

        store.write_object({"messages": {"a": {"text": "a"}}, "cursors": {}})

        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert store.read_object() == {"messages": {"a": {"text": "a"}}, "cursors": {}}
    finally:
        os.umask(old_umask)


def test_atomic_json_writers_can_replace_same_file_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "shared.json"
    write_json_atomically(path, {"writer": "initial"})
    real_replace = os.replace
    both_ready = Barrier(2)
    errors: list[Exception] = []

    def replace_after_both_are_ready(source: Path, destination: Path) -> None:
        if destination == path:
            both_ready.wait(timeout=5)
        real_replace(source, destination)

    def write(writer: str) -> None:
        try:
            write_json_atomically(path, {"writer": writer})
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(os, "replace", replace_after_both_are_ready)
    writers = [Thread(target=write, args=(name,)) for name in ("first", "second")]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(timeout=6)
        assert not writer.is_alive()

    assert errors == []
    assert json.loads(path.read_text(encoding="utf-8")) in (
        {"writer": "first"},
        {"writer": "second"},
    )
    assert list(tmp_path.glob(".shared.json.*.tmp")) == []


def test_atomic_json_writer_respects_umask_and_retains_existing_mode(tmp_path: Path) -> None:
    path = tmp_path / "shared.json"
    original_umask = os.umask(0o027)
    try:
        write_json_atomically(path, {"revision": 1})
    finally:
        os.umask(original_umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640

    original_umask = os.umask(0o077)
    try:
        write_json_atomically(path, {"revision": 2})
    finally:
        os.umask(original_umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
