from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from evander.issues.activity_cache import AdoActivityScanCache, ActivityScanResult


def scan(label: str = "initial", count: int = 3) -> ActivityScanResult:
    return ActivityScanResult(
        items=[{"id": str(i), "fields": {"label": label}} for i in range(1, count + 1)],
        metadata={
            "provider": "azure_devops",
            "project": "Example",
            "paging": {"total_matches": count, "candidate_scan_complete": True},
        },
    )


def query(cache, load, *, key="query", directory="root/a", top=2, skip=0):
    return cache.query(directory_path=directory, key_parts=key, load=load, top=top, skip=skip)


def test_pages_reuse_results_without_cross_caller_mutation() -> None:
    cache = AdoActivityScanCache()
    calls = []

    def load():
        calls.append(1)
        return scan()

    first = query(cache, load)
    first["items"][0]["fields"]["label"] = "modified"
    first["paging"]["total_matches"] = 99
    second = query(cache, load, skip=2)
    repeated = query(cache, load, top=1)
    assert calls == [1]
    assert [item["id"] for item in first["items"]] == ["1", "2"]
    assert [item["id"] for item in second["items"]] == ["3"]
    assert second["paging"]["total_matches"] == 3
    assert second["paging"]["next_skip"] is None
    assert repeated["items"][0]["fields"]["label"] == "initial"


def test_expiry_starts_after_completion_and_hits_do_not_extend_it() -> None:
    now = [0.0]
    cache = AdoActivityScanCache(clock=lambda: now[0])
    calls = []

    def load():
        calls.append(1)
        now[0] += 45
        return scan(str(len(calls)))

    query(cache, load)
    now[0] = 344
    assert query(cache, load)["items"][0]["fields"]["label"] == "1"
    now[0] = 345
    assert query(cache, load)["items"][0]["fields"]["label"] == "2"
    assert len(calls) == 2


def test_lru_entry_eviction_and_directory_invalidation() -> None:
    cache = AdoActivityScanCache(max_entries=2)
    calls = []

    def load():
        calls.append(1)
        return scan()

    query(cache, load, key="a")
    query(cache, load, key="b", directory="root/b")
    query(cache, load, key="a")
    query(cache, load, key="c")
    assert len(calls) == 3
    query(cache, load, key="b", directory="root/b")
    assert len(calls) == 4
    cache.invalidate_directory("root/a")
    query(cache, load, key="b", directory="root/b")
    assert len(calls) == 4
    query(cache, load, key="c")
    assert len(calls) == 5


def test_byte_budget_evicts_and_oversized_scans_are_not_retained() -> None:
    cache = AdoActivityScanCache(max_bytes=500)
    calls = []

    def load():
        calls.append(1)
        return scan("x" * 60)

    query(cache, load, key="a")
    query(cache, load, key="b")
    assert cache.stats()["retained_bytes"] <= 500
    assert cache.stats()["entries"] == 1
    query(cache, load, key="a")
    assert len(calls) == 3
    before = cache.stats()
    for _ in range(2):
        assert query(cache, lambda: scan("x" * 1000), key="huge")["count"] == 2
    assert cache.stats() == before


def test_empty_scans_are_cached_and_failed_scans_retry() -> None:
    cache = AdoActivityScanCache()
    calls = []

    def load():
        calls.append(1)
        return scan(count=0)

    assert query(cache, load)["count"] == 0
    assert query(cache, load)["count"] == 0
    assert len(calls) == 1

    def failed():
        raise ValueError("synthetic upstream failure")

    with pytest.raises(ValueError, match="upstream failure"):
        query(cache, failed, key="error")
    assert cache.stats()["inflight"] == 0
    assert query(cache, lambda: scan(), key="error")["count"] == 2


@pytest.mark.parametrize("failure", [False, True])
def test_overlapping_queries_share_scan_and_failure(failure: bool) -> None:
    cache = AdoActivityScanCache()
    entered, release, joined = Event(), Event(), Event()
    calls = []
    original_log = cache._log

    def log(outcome, **kwargs):
        if outcome == "shared":
            joined.set()
        original_log(outcome, **kwargs)

    cache._log = log

    def load():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        if failure:
            raise ValueError("synthetic upstream failure")
        return scan()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(query, cache, load)
        try:
            assert entered.wait(3)
            second = pool.submit(query, cache, load, skip=2)
            assert joined.wait(3)
        finally:
            release.set()
        if failure:
            for result in [first, second]:
                with pytest.raises(ValueError, match="upstream failure"):
                    result.result(timeout=3)
        else:
            assert first.result(timeout=3)["count"] == 2
            assert second.result(timeout=3)["items"][0]["id"] == "3"
    assert len(calls) == 1
    assert cache.stats()["inflight"] == 0


def test_invalidation_prevents_old_inflight_scan_from_repopulating_cache() -> None:
    cache = AdoActivityScanCache()
    entered, release = Event(), Event()

    def old():
        entered.set()
        assert release.wait(3)
        return scan("old")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(query, cache, old)
        try:
            assert entered.wait(3)
            cache.invalidate_directory("root/a")
            assert query(cache, lambda: scan("new"))["items"][0]["fields"]["label"] == "new"
        finally:
            release.set()
        assert pending.result(timeout=3)["items"][0]["fields"]["label"] == "old"
    assert (
        query(cache, lambda: pytest.fail("must reuse new scan"))["items"][0]["fields"]["label"]
        == "new"
    )


def test_distinct_inflight_limit_bypasses_without_disturbing_leader() -> None:
    cache = AdoActivityScanCache(max_inflight=1)
    entered, release = Event(), Event()

    def load():
        entered.set()
        assert release.wait(3)
        return scan()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(query, cache, load)
        try:
            assert entered.wait(3)
            assert query(cache, lambda: scan("bypass"), key="other")["count"] == 2
            assert cache.stats() == {"retained_bytes": 0, "entries": 0, "inflight": 1}
        finally:
            release.set()
        first.result(timeout=3)
    assert cache.stats()["entries"] == 1


def test_cancelled_async_waiter_does_not_cancel_shared_scan() -> None:
    import asyncio

    cache = AdoActivityScanCache()
    entered, joined, release = Event(), Event(), Event()
    calls = []
    original_log = cache._log

    def log(outcome, **kwargs):
        if outcome == "shared":
            joined.set()
        original_log(outcome, **kwargs)

    cache._log = log

    def load():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return scan()

    async def exercise():
        leader = asyncio.create_task(asyncio.to_thread(query, cache, load))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            waiter = asyncio.create_task(asyncio.to_thread(query, cache, load, skip=2))
            assert await asyncio.to_thread(joined.wait, 3)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        finally:
            release.set()
        assert (await leader)["count"] == 2
        assert query(cache, load, skip=2)["items"][0]["id"] == "3"

    asyncio.run(exercise())
    assert len(calls) == 1
