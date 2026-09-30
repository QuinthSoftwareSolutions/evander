from __future__ import annotations

from datetime import UTC, datetime
import json
from threading import Barrier, Lock

import httpx
import pytest

from evander.issues.service import AzureDevOpsIssueAdapter


def _activity_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    item_count: int,
    omitted_ids: set[int],
    history_barrier: Barrier | None = None,
    requests: list[tuple[str, str]] | None = None,
) -> tuple[AzureDevOpsIssueAdapter, list[list[int]], list[int]]:
    """Serve ADO responses by request identity so fetches may finish in any order."""
    batches: list[list[int]] = []
    history_ids: list[int] = []
    lock = Lock()

    def item(item_id: int) -> dict[str, object]:
        return {
            "id": item_id,
            "url": f"https://dev.azure.com/example/_apis/wit/workitems/{item_id}",
            "_links": {
                "web": {"href": f"https://dev.azure.com/example/Example/_workitems/edit/{item_id}"}
            },
            "fields": {
                "System.TeamProject": "Example",
                "System.Title": f"Work item {item_id}",
                "System.ChangedDate": "2026-09-25T08:00:00Z",
                "Custom.Example": {"nested": ["preserved"]},
            },
        }

    def handle(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            with lock:
                requests.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/wiql"):
            return httpx.Response(
                200, json={"workItems": [{"id": i} for i in range(1, item_count + 1)]}
            )
        if request.method == "POST" and request.url.path.endswith("/workitemsbatch"):
            ids = json.loads(request.content)["ids"]
            with lock:
                batches.append(ids)
            return httpx.Response(
                200, json={"value": [item(i) for i in ids if i not in omitted_ids]}
            )
        if request.method == "GET" and request.url.path.endswith("/comments"):
            return httpx.Response(200, json={"comments": []})
        if request.method == "GET" and request.url.path.endswith("/updates"):
            item_id = int(request.url.path.split("/")[-2])
            with lock:
                history_ids.append(item_id)
            if history_barrier is not None:
                history_barrier.wait(timeout=3)
            return httpx.Response(200, json={"value": []})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client_type = httpx.Client
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: client_type(timeout=timeout, transport=httpx.MockTransport(handle)),
    )
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "Example", "synthetic-pat"),
    )
    return adapter, batches, history_ids


@pytest.mark.parametrize(
    (
        "item_count",
        "omitted_ids",
        "exhaustive",
        "max_candidates",
        "expected_batches",
        "expected_ids",
        "complete",
    ),
    [
        pytest.param(3, {1}, False, 50, [[1, 2, 3]], [2, 3], True, id="deleted-candidate"),
        pytest.param(
            203,
            set(range(1, 201)),
            True,
            50,
            [list(range(1, 201)), [201, 202, 203]],
            [201, 202, 203],
            True,
            id="entire-first-page-omitted",
        ),
        pytest.param(3, {1}, False, 2, [[1, 2]], [2], False, id="omissions-consume-scan-budget"),
        pytest.param(
            203,
            {201, 202, 203},
            True,
            50,
            [list(range(1, 201)), [201, 202, 203]],
            list(range(1, 201)),
            True,
            id="entire-last-page-omitted",
        ),
    ],
)
def test_activity_scan_consumes_omitted_ids(
    monkeypatch: pytest.MonkeyPatch,
    item_count: int,
    omitted_ids: set[int],
    exhaustive: bool,
    max_candidates: int,
    expected_batches: list[list[int]],
    expected_ids: list[int],
    complete: bool,
) -> None:
    adapter, batches, history_ids = _activity_adapter(
        monkeypatch, item_count=item_count, omitted_ids=omitted_ids
    )

    result = adapter.query_work_item_activity(
        directory_path="root",
        since=datetime(2026, 9, 25, tzinfo=UTC),
        activity_types=["changed"],
        exhaustive=exhaustive,
        max_candidates=max_candidates,
        top=200,
    )

    assert batches == expected_batches
    assert sorted(history_ids) == expected_ids
    assert [item["id"] for item in result["items"]] == [str(i) for i in expected_ids]
    assert result["count"] == len(expected_ids)
    assert result["paging"]["total_matches"] == len(expected_ids)
    assert result["paging"]["candidate_scanned_count"] == len(expected_ids)
    assert result["paging"]["candidate_total_matches"] == item_count
    assert result["paging"]["candidate_scan_complete"] is complete
    assert result["paging"]["total_matches_complete"] is complete
    assert result["paging"]["has_more"] is not complete


@pytest.mark.parametrize("omitted_ids", [{1}, {1, 2}])
def test_backlog_next_page_advances_past_omitted_ids(
    monkeypatch: pytest.MonkeyPatch, omitted_ids: set[int]
) -> None:
    adapter, batches, _ = _activity_adapter(monkeypatch, item_count=3, omitted_ids=omitted_ids)

    first = adapter.query_backlog_items(directory_path="root", top=2)
    assert first["paging"]["next_skip"] == 2
    assert first["paging"]["has_more"] is True
    second = adapter.query_backlog_items(
        directory_path="root", top=2, skip=first["paging"]["next_skip"]
    )

    assert batches == [[1, 2], [3]]
    assert [item["id"] for item in second["items"]] == ["3"]
    assert second["paging"]["next_skip"] is None


@pytest.mark.parametrize("item_count", [2, 7])
def test_activity_partial_pages_fetch_histories_concurrently(
    monkeypatch: pytest.MonkeyPatch, item_count: int
) -> None:
    adapter, _, history_ids = _activity_adapter(
        monkeypatch,
        item_count=item_count,
        omitted_ids=set(),
        history_barrier=Barrier(item_count),
    )

    result = adapter.query_work_item_activity(
        directory_path="root",
        since=datetime(2026, 9, 25, tzinfo=UTC),
        activity_types=["changed"],
    )

    assert sorted(history_ids) == list(range(1, item_count + 1))
    assert [item["id"] for item in result["items"]] == [str(i) for i in range(1, item_count + 1)]


def test_activity_cache_serves_401_items_with_one_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    from evander.issues.activity_cache import AdoActivityScanCache

    requests = []
    adapter, batches, history_ids = _activity_adapter(
        monkeypatch,
        item_count=401,
        omitted_ids=set(),
        requests=requests,
    )
    cache = AdoActivityScanCache()
    pages = [
        adapter.query_work_item_activity(
            directory_path="root",
            since=datetime(2026, 9, 25, tzinfo=UTC),
            exhaustive=True,
            top=200,
            skip=skip,
            _activity_cache=cache,
            _cache_scope="session-a",
        )
        for skip in [0, 200, 400]
    ]
    assert [page["count"] for page in pages] == [200, 200, 1]
    assert [item["id"] for page in pages for item in page["items"]] == [
        str(i) for i in range(1, 402)
    ]
    assert [len(batch) for batch in batches] == [200, 200, 1]
    assert sorted(history_ids) == list(range(1, 402))
    assert sum(path.endswith("/wiql") for _, path in requests) == 1
    assert len(requests) == 806
    assert pages[0]["items"][0]["fields"]["Custom.Example"] == {"nested": ["preserved"]}
    assert (
        pages[0]["items"][0]["web_url"] == "https://dev.azure.com/example/Example/_workitems/edit/1"
    )
    assert pages[-1]["items"][0]["fields"]["Custom.Example"] == {"nested": ["preserved"]}
    assert all(page["paging"]["total_matches"] == 401 for page in pages)
    assert all(page["paging"]["candidate_scan_complete"] for page in pages)


@pytest.mark.parametrize(
    "change",
    [
        "session",
        "directory",
        "project",
        "actor",
        "since",
        "until",
        "activity_types",
        "states",
        "types",
        "tags_any",
        "exhaustive",
        "max_candidates",
        "organization",
        "credential",
    ],
)
def test_activity_cache_separates_changed_scope_and_filters(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from datetime import timedelta
    from evander.issues.activity_cache import AdoActivityScanCache

    adapter, batches, _ = _activity_adapter(monkeypatch, item_count=1, omitted_ids=set())
    kwargs = dict(
        directory_path="root",
        since=datetime(2026, 9, 25, tzinfo=UTC),
        _activity_cache=AdoActivityScanCache(),
        _cache_scope="session-a",
    )
    adapter.query_work_item_activity(**kwargs)
    if change == "session":
        kwargs["_cache_scope"] = "session-b"
    elif change == "directory":
        kwargs["directory_path"] = "root/other"
    elif change == "since":
        kwargs["since"] -= timedelta(days=1)
    elif change == "until":
        kwargs["until"] = datetime(2026, 9, 26, tzinfo=UTC)
    elif change == "activity_types":
        kwargs["activity_types"] = ["changed"]
    elif change in ["states", "types", "tags_any"]:
        kwargs[change] = ["Example"]
    elif change == "exhaustive":
        kwargs[change] = True
    elif change == "max_candidates":
        kwargs[change] = 2
    elif change in ["organization", "credential"]:
        monkeypatch.setattr(
            adapter,
            "_resolve_connection",
            lambda directory: (
                "https://dev.azure.com/other"
                if change == "organization"
                else "https://dev.azure.com/example",
                "Example",
                "rotated-pat" if change == "credential" else "synthetic-pat",
            ),
        )
    else:
        kwargs[change] = "Other"
    adapter.query_work_item_activity(**kwargs)
    assert len(batches) == 2


def test_activity_cache_checks_connection_on_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    from evander.issues.activity_cache import AdoActivityScanCache

    adapter, _, _ = _activity_adapter(monkeypatch, item_count=1, omitted_ids=set())
    kwargs = dict(
        directory_path="root",
        since=datetime(2026, 9, 25, tzinfo=UTC),
        _activity_cache=AdoActivityScanCache(),
        _cache_scope="session-a",
    )
    adapter.query_work_item_activity(**kwargs)

    def missing(directory):
        raise ValueError("provider credentials are unavailable")

    monkeypatch.setattr(adapter, "_resolve_connection", missing)
    with pytest.raises(ValueError, match="credentials are unavailable"):
        adapter.query_work_item_activity(**kwargs)
