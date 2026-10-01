from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evander.events.provider_models import AdoWorkItemCommentCreatedTriggerDefinition
from evander.issues.service import AzureDevOpsIssueAdapter, IssueService
from evander.triggers.providers.ado_work_item_comment_created import (
    AdoWorkItemCommentCreatedTriggerProvider,
)
from evander.triggers.providers.base import TriggerRuntimeContext


def _poll_server(monkeypatch, *, item_count=3):
    requests: list[httpx.Request] = []
    clients: list[httpx.Client] = []
    resolved_directories: list[str] = []
    state = {"fail_comments": False, "comments": [_comment(1)]}
    items = {
        i: {
            "id": i,
            "rev": 7,
            "url": f"https://dev.azure.com/example/_apis/wit/workitems/{i}",
            "_links": {"web": {"href": f"https://dev.azure.com/example/App/_workitems/edit/{i}"}},
            "fields": {
                "System.Id": i,
                "System.TeamProject": "App",
                "System.Title": f"Work item {i}",
                "System.State": "Active",
                "System.ChangedDate": "2026-10-01T10:00:00Z",
                "Custom.Preserved": {"value": ["all fields"]},
            },
            "relations": [
                {
                    "rel": "System.LinkTypes.Hierarchy-Reverse",
                    "url": "https://dev.azure.com/example/_apis/wit/workitems/900",
                }
            ],
        }
        for i in range(1, item_count + 1)
    }

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/wiql"):
            return httpx.Response(
                200, json={"workItems": [{"id": i} for i in range(1, item_count + 2)]}
            )
        if request.url.path.endswith("/workitemsbatch"):
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"value": [items[i] for i in body["ids"] if i in items]}
            )
        if request.url.path.endswith("/comments"):
            if state["fail_comments"]:
                return httpx.Response(503)
            return httpx.Response(
                200, json={"comments": state["comments"], "continuationToken": "next-page"}
            )
        if request.method == "GET" and "/workitems/" in request.url.path:
            return httpx.Response(200, json=items[int(request.url.path.rsplit("/", 1)[1])])
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    client_type = httpx.Client

    def client_factory(*, timeout):
        client = client_type(timeout=timeout, transport=httpx.MockTransport(handle))
        clients.append(client)
        return client

    def resolve_connection(self, directory_path):
        del self
        resolved_directories.append(directory_path)
        organization = directory_path.rsplit("/", 1)[-1]
        return f"https://dev.azure.com/{organization}", "App", f"pat-{organization}"

    monkeypatch.setattr("evander.issues.service.httpx.Client", client_factory)
    monkeypatch.setattr(AzureDevOpsIssueAdapter, "_resolve_connection", resolve_connection)
    return IssueService(object()), requests, clients, resolved_directories, state


def _comment(comment_id, *, created_at="2026-10-01T10:01:00Z"):
    return {
        "id": comment_id,
        "text": "Please review",
        "createdDate": created_at,
        "createdBy": {"displayName": "Reviewer", "id": "reviewer"},
    }


def _provider(tmp_path: Path, *, current_iteration=False):
    provider = AdoWorkItemCommentCreatedTriggerProvider()
    provider.initialize(
        AdoWorkItemCommentCreatedTriggerDefinition(
            id="comment-poll",
            display_name="Comment poll",
            type="trigger:ado:work-item-comment-created",
            directory_path="root/team-a",
            include_existing=True,
            current_iteration=current_iteration,
        ),
        TriggerRuntimeContext(
            provider_config={"runtime_dir": str(tmp_path)}, provider_config_resolver=object()
        ),
    )
    return provider


@pytest.mark.parametrize("current_iteration", [False, True])
@pytest.mark.parametrize("item_count", [3, 19])
def test_poll_fetches_full_items_in_one_batch_and_reuses_one_scoped_client(
    monkeypatch,
    tmp_path,
    current_iteration,
    item_count,
):
    service, requests, clients, directories, _ = _poll_server(monkeypatch, item_count=item_count)
    monkeypatch.setattr(
        AzureDevOpsIssueAdapter,
        "_resolve_current_iteration",
        lambda *a, **kw: SimpleNamespace(
            team="Team A", path="App\\Current", start_date=None, finish_date=None
        ),
    )
    monkeypatch.setattr(AzureDevOpsIssueAdapter, "_resolve_team_field_values", lambda *a, **kw: [])
    expected = service.get_remote_backlog_item_by_id(
        provider="azure_devops",
        directory_path="root/team-a",
        backlog_item_id="1",
    )
    requests.clear()
    clients.clear()
    directories.clear()

    events = _provider(tmp_path, current_iteration=current_iteration).poll()

    assert len(events) == item_count  # The omitted batch item is not individually fetched.
    item_one = next(
        event.payload["work_item"] for event in events if event.payload["work_item_id"] == "1"
    )
    assert item_one == expected
    assert item_one["fields"]["Custom.Preserved"] == {"value": ["all fields"]}
    assert item_one["relations"][0]["url"] == (
        "https://dev.azure.com/example/_apis/wit/workitems/900"
    )
    assert item_one["comments_paging"]["has_more"] is True
    assert len(requests) == item_count + 2  # WIQL + full batch + comments; no detail GETs.
    assert [request.method for request in requests] == ["POST", "POST", *["GET"] * item_count]
    batch = json.loads(requests[1].content)
    assert batch == {"ids": list(range(1, item_count + 2)), "$expand": "All", "errorPolicy": "Omit"}
    assert all(request.url.path.endswith("/comments") for request in requests[2:])
    assert directories == ["root/team-a"]
    assert len(clients) == 1 and clients[0].is_closed


def test_comment_failures_close_client_and_do_not_acknowledge_or_cache_across_polls(
    monkeypatch,
    tmp_path,
):
    _, requests, clients, _, state = _poll_server(monkeypatch)
    provider = _provider(tmp_path)
    state["fail_comments"] = True
    with pytest.raises(httpx.HTTPStatusError):
        provider.poll()
    assert clients[-1].is_closed
    cursor = json.loads((tmp_path / "trigger-cursors-ado-work-item-comments.json").read_text())
    assert not cursor.get("comment-poll", {}).get("streams")

    state["fail_comments"] = False
    events = provider.poll()
    assert [event.event_id for event in provider.poll()] == [event.event_id for event in events]
    for event in events:
        provider.acknowledge_published_event(event)
    assert provider.poll() == []

    # New equal-timestamp comments still arrive; older comments retain the
    # original cursor behavior even when first observed in a later poll.
    state["comments"] = [_comment(1), _comment(2), _comment(0, created_at="2026-10-01T10:00:00Z")]
    late_events = provider.poll()
    assert [event.payload["comment_id"] for event in late_events] == ["2", "2", "2"]
    assert all(client.is_closed for client in clients)
    assert len(clients) == 5
    assert sum(request.url.path.endswith("/comments") for request in requests) == 13


def test_poll_reader_rejects_other_directory_and_provider_and_deduplicates_comment_reads(
    monkeypatch,
):
    service, requests, clients, directories, _ = _poll_server(monkeypatch)
    with service.ado_comment_poll(directory_path="root/team-a") as reader:
        with pytest.raises(ValueError, match="directory"):
            reader.query_remote_backlog_items(provider="azure_devops", directory_path="root/team-b")
        with pytest.raises(ValueError, match="Azure DevOps"):
            reader.query_remote_backlog_items(provider="github", directory_path="root/team-a")
        reader.query_remote_backlog_items(provider="azure_devops", directory_path="root/team-a")
        first = reader.get_remote_backlog_item_by_id(
            provider="azure_devops", directory_path="root/team-a", backlog_item_id="1"
        )
        assert (
            reader.get_remote_backlog_item_by_id(
                provider="azure_devops", directory_path="root/team-a", backlog_item_id="1"
            )
            == first
        )
    assert len(requests) == 3
    assert clients[-1].is_closed
    first_auth = requests[0].headers["Authorization"]

    with service.ado_comment_poll(directory_path="root/team-b") as reader:
        reader.query_remote_backlog_items(provider="azure_devops", directory_path="root/team-b")
    assert directories == ["root/team-a", "root/team-b"]
    assert len(clients) == 2 and all(client.is_closed for client in clients)
    assert requests[-1].headers["Authorization"] != first_auth
    assert requests[-1].url.path.startswith("/team-b/")
