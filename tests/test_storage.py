import os
import stat
import time
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier, Thread

from pydantic import BaseModel

from agent_sandbox.storage import JsonStore


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
