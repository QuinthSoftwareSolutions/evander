from pathlib import Path
from threading import Barrier, Thread
import time

from pydantic import BaseModel

from agent_sandbox.storage import JsonStore


class SampleRecord(BaseModel):
    value: str


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
