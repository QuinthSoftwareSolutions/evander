from __future__ import annotations

import httpx
import pytest

from agent_sandbox.issues.service import AzureDevOpsIssueAdapter


class _DummyClient:
    def __init__(self, response: httpx.Response, calls: list[dict[str, object]]) -> None:
        self._response = response
        self._calls = calls

    def __enter__(self) -> _DummyClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def patch(self, url: str, **kwargs: object) -> httpx.Response:
        self._calls.append({"url": url, **kwargs})
        return self._response


class _RecordingClient:
    def __init__(self, calls: list[dict[str, object]], responses: list[httpx.Response]) -> None:
        self._calls = calls
        self._responses = responses

    def __enter__(self) -> _RecordingClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        self._calls.append({"method": "POST", "url": url, **kwargs})
        return self._responses.pop(0)

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        self._calls.append({"method": "GET", "url": url, **kwargs})
        return self._responses.pop(0)


def test_create_issue_appends_parent_relation_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    payload = {
        "id": 77,
        "url": "https://dev.azure.com/example/project-x/_apis/wit/workitems/77",
        "fields": {
            "System.WorkItemType": "Task",
            "System.TeamProject": "project-x",
            "System.Title": "T",
        },
        "_links": {"web": {"href": "https://dev.azure.com/example/project-x/_workitems/edit/77"}},
    }
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _DummyClient(httpx.Response(200, json=payload), calls),
    )

    adapter.create_issue(
        directory_path="root",
        issue_type="task",
        title="T",
        parent_backlog_item_id="1234",
    )

    assert len(calls) == 1
    operations = calls[0]["json"]
    assert isinstance(operations, list)
    assert operations[-1] == {
        "op": "add",
        "path": "/relations/-",
        "value": {
            "rel": "System.LinkTypes.Hierarchy-Reverse",
            "url": "https://dev.azure.com/example/project-x/_apis/wit/workItems/1234",
        },
    }


def test_create_issue_uses_explicit_project_override(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "default-project", "pat"),
    )
    calls: list[dict[str, object]] = []
    payload = {
        "id": 101,
        "fields": {
            "System.WorkItemType": "Task",
            "System.TeamProject": "other-project",
            "System.Title": "T",
        },
        "_links": {
            "web": {"href": "https://dev.azure.com/example/other-project/_workitems/edit/101"}
        },
    }
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _DummyClient(httpx.Response(200, json=payload), calls),
    )

    result = adapter.create_issue(
        directory_path="root",
        project="other-project",
        issue_type="task",
        title="T",
        parent_backlog_item_id="1234",
    )

    assert (
        calls[0]["url"] == "https://dev.azure.com/example/other-project/_apis/wit/workitems/$Task"
    )
    operations = calls[0]["json"]
    assert isinstance(operations, list)
    assert (
        operations[-1]["value"]["url"]
        == "https://dev.azure.com/example/other-project/_apis/wit/workItems/1234"
    )
    assert result["project"] == "other-project"


def test_create_issue_rejects_invalid_parent_relation_id_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )

    with pytest.raises(ValueError, match="Parent relation validation failed"):
        adapter.create_issue(
            directory_path="root",
            issue_type="task",
            title="T",
            parent_backlog_item_id="abc-123",
        )


def test_create_issue_surfaces_parent_linkage_failure_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    error_response = httpx.Response(
        400, json={"message": "The parent does not exist in this project."}
    )
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _DummyClient(error_response, calls),
    )

    with pytest.raises(RuntimeError, match="link parent relation during create work item failed"):
        adapter.create_issue(
            directory_path="root",
            issue_type="task",
            title="T",
            parent_backlog_item_id="1234",
        )


def test_create_issue_composes_json_patch_for_backlog_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    payload = {
        "id": 88,
        "url": "https://dev.azure.com/example/project-x/_apis/wit/workitems/88",
        "fields": {
            "System.WorkItemType": "Task",
            "System.TeamProject": "project-x",
            "System.Title": "Implement API",
            "System.Description": "<p>desc</p>",
            "System.IterationPath": "project-x\\Sprint 1",
            "Microsoft.VSTS.Common.AcceptanceCriteria": "Tests pass",
            "System.Tags": "api; backend",
            "System.AssignedTo": "ada@example.com",
        },
        "_links": {"web": {"href": "https://dev.azure.com/example/project-x/_workitems/edit/88"}},
    }
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _DummyClient(httpx.Response(200, json=payload), calls),
    )

    result = adapter.create_issue(
        directory_path="root",
        issue_type="task",
        title="Implement API",
        description="<p>desc</p>",
        iteration_path="project-x\\Sprint 1",
        acceptance_criteria="Tests pass",
        tags=["api", "backend"],
        assigned_to="ada@example.com",
    )

    assert len(calls) == 1
    operations = calls[0]["json"]
    assert operations == [
        {"op": "add", "path": "/fields/System.Title", "value": "Implement API"},
        {"op": "add", "path": "/fields/System.Description", "value": "<p>desc</p>"},
        {"op": "add", "path": "/fields/System.IterationPath", "value": "project-x\\Sprint 1"},
        {
            "op": "add",
            "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria",
            "value": "<p>Tests pass</p>",
        },
        {"op": "add", "path": "/fields/System.Tags", "value": "api; backend"},
        {"op": "add", "path": "/fields/System.AssignedTo", "value": "ada@example.com"},
    ]
    assert result["fields"]["Microsoft.VSTS.Common.AcceptanceCriteria"] == "Tests pass"
    assert result["fields"]["System.Tags"] == "api; backend"
    assert result["fields"]["System.AssignedTo"] == "ada@example.com"


def test_create_issue_formats_plain_text_ado_html_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    payload = {
        "id": 90,
        "url": "https://dev.azure.com/example/project-x/_apis/wit/workitems/90",
        "fields": {
            "System.WorkItemType": "Task",
            "System.TeamProject": "project-x",
            "System.Title": "Implement API",
            "System.Description": "<p>Line one</p><ul><li>Line two</li></ul>",
            "System.IterationPath": "project-x\\Sprint 1",
            "Microsoft.VSTS.Common.AcceptanceCriteria": "<ol><li>Tests pass</li></ol>",
        },
        "_links": {"web": {"href": "https://dev.azure.com/example/project-x/_workitems/edit/90"}},
    }
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _DummyClient(httpx.Response(200, json=payload), calls),
    )

    adapter.create_issue(
        directory_path="root",
        issue_type="task",
        title="Implement API",
        description="Line one\n- Line two",
        iteration_path="project-x\\Sprint 1",
        acceptance_criteria="1. Tests pass",
    )

    operations = calls[0]["json"]
    assert operations[1] == {
        "op": "add",
        "path": "/fields/System.Description",
        "value": "<p>Line one</p><ul><li>Line two</li></ul>",
    }
    assert operations[3] == {
        "op": "add",
        "path": "/fields/Microsoft.VSTS.Common.AcceptanceCriteria",
        "value": "<ol><li>Tests pass</li></ol>",
    }


def test_create_issue_normalizes_assigned_to_identity_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    payload = {
        "id": 89,
        "fields": {
            "System.WorkItemType": "Task",
            "System.TeamProject": "project-x",
            "System.Title": "Implement API",
            "System.AssignedTo": {
                "displayName": "Ada Lovelace",
                "uniqueName": "ada@example.com",
            },
        },
        "_links": {"web": {"href": "https://dev.azure.com/example/project-x/_workitems/edit/89"}},
    }
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _DummyClient(httpx.Response(200, json=payload), calls),
    )

    result = adapter.create_issue(
        directory_path="root",
        issue_type="task",
        title="Implement API",
    )

    assert result["fields"]["System.AssignedTo"] == "Ada Lovelace"


def test_create_issue_propagates_ado_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    error_response = httpx.Response(
        403,
        json={
            "message": "TF401403: You do not have permission to create work items in this project."
        },
        headers={"Content-Type": "application/json"},
    )
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _DummyClient(error_response, calls),
    )

    with pytest.raises(
        RuntimeError, match="Azure DevOps create work item failed \\(status 403\\): TF401403"
    ):
        adapter.create_issue(
            directory_path="root",
            issue_type="task",
            title="T",
        )


def test_query_backlog_items_url_encodes_project_name() -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json={"workItems": [{"id": 101}]}),
        httpx.Response(200, json={"value": []}),
    ]

    adapter._query_backlog_items_core(
        _RecordingClient(calls, responses.copy()),
        organization_url="https://dev.azure.com/example",
        project="My Project",
        auth_header="Basic secret",
    )

    assert calls[0]["url"] == "https://dev.azure.com/example/My%20Project/_apis/wit/wiql"


def test_query_backlog_items_batch_uses_fields_without_expand() -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json={"workItems": [{"id": 101}]}),
        httpx.Response(200, json={"value": []}),
    ]

    adapter._query_backlog_items_core(
        _RecordingClient(calls, responses.copy()),
        organization_url="https://dev.azure.com/example",
        project="project-x",
        auth_header="Basic secret",
    )

    batch_payload = calls[1]["json"]
    assert isinstance(batch_payload, dict)
    assert "fields" in batch_payload
    assert "$expand" not in batch_payload


def test_get_backlog_item_by_id_url_encodes_project_name(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "My Project", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            200,
            json={
                "id": 101,
                "fields": {"System.TeamProject": "My Project", "System.Title": "Backlog item"},
                "_links": {
                    "web": {
                        "href": "https://dev.azure.com/example/My%20Project/_workitems/edit/101"
                    }
                },
            },
        )
    ]
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    adapter.get_backlog_item_by_id(
        directory_path="root", backlog_item_id="101", include_comments=False
    )

    assert calls[0]["url"] == "https://dev.azure.com/example/My%20Project/_apis/wit/workitems/101"
