from __future__ import annotations

from pathlib import Path

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

    @staticmethod
    def _with_request(response: httpx.Response, *, method: str, url: str) -> httpx.Response:
        try:
            response.request
            return response
        except RuntimeError:
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=response.content,
                request=httpx.Request(method, url),
                extensions=response.extensions,
            )

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        self._calls.append({"method": "POST", "url": url, **kwargs})
        return self._with_request(self._responses.pop(0), method="POST", url=url)

    def patch(self, url: str, **kwargs: object) -> httpx.Response:
        self._calls.append({"method": "PATCH", "url": url, **kwargs})
        return self._with_request(self._responses.pop(0), method="PATCH", url=url)

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        self._calls.append({"method": "GET", "url": url, **kwargs})
        return self._with_request(self._responses.pop(0), method="GET", url=url)


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


def test_create_issue_uploads_description_artifact_and_links_relation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"png-bytes")
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            200,
            json={
                "id": "att-1",
                "url": "https://dev.azure.com/example/project-x/_apis/wit/attachments/att-1",
            },
        ),
        httpx.Response(
            200,
            json={
                "id": 77,
                "fields": {
                    "System.WorkItemType": "Task",
                    "System.TeamProject": "project-x",
                    "System.Title": "T",
                    "System.Description": "<p>See screenshot</p>",
                },
                "_links": {
                    "web": {
                        "href": "https://dev.azure.com/example/project-x/_workitems/edit/77"
                    }
                },
            },
        ),
    ]
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.create_issue(
        directory_path="root",
        issue_type="task",
        title="T",
        description="See screenshot",
        description_attachments=[
            {
                "artifact_id": "comm_abc",
                "filename": "screen.png",
                "mime_type": "image/png",
                "byte_count": len(b"png-bytes"),
                "path": str(image_path),
            }
        ],
    )

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://dev.azure.com/example/project-x/_apis/wit/attachments"
    assert calls[0]["content"] == b"png-bytes"
    operations = calls[1]["json"]
    assert any(
        item["path"] == "/fields/System.Description"
        and '<img src="https://dev.azure.com/example/project-x/_apis/wit/attachments/att-1"'
        in item["value"]
        for item in operations
    )
    assert operations[-1] == {
        "op": "add",
        "path": "/relations/-",
        "value": {
            "rel": "AttachedFile",
            "url": "https://dev.azure.com/example/project-x/_apis/wit/attachments/att-1",
            "attributes": {"name": "screen.png"},
        },
    }
    assert result["uploaded_attachments"][0]["filename"] == "screen.png"


def test_post_comment_uploads_artifact_appends_image_and_links_relation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    image_path = tmp_path / "comment.png"
    image_path.write_bytes(b"comment-png")
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            200,
            json={
                "id": "att-2",
                "url": "https://dev.azure.com/example/project-x/_apis/wit/attachments/att-2",
            },
        ),
        httpx.Response(
            200,
            json={
                "id": 5,
                "text": "Comment",
                "_links": {"web": {"href": "https://dev.azure.com/comment/5"}},
            },
        ),
        httpx.Response(200, json={"id": 77}),
    ]
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.post_comment(
        directory_path="root",
        remote_issue_id="77",
        comment="Comment",
        attachments=[
            {
                "artifact_id": "comm_def",
                "filename": "comment.png",
                "mime_type": "image/png",
                "byte_count": len(b"comment-png"),
                "path": str(image_path),
            }
        ],
    )

    assert calls[0]["url"] == "https://dev.azure.com/example/project-x/_apis/wit/attachments"
    assert calls[1]["url"] == "https://dev.azure.com/example/project-x/_apis/wit/workItems/77/comments"
    assert (
        '<img src="https://dev.azure.com/example/project-x/_apis/wit/attachments/att-2"'
        in calls[1]["json"]["text"]
    )
    assert calls[2]["method"] == "PATCH"
    assert calls[2]["json"][0]["value"]["rel"] == "AttachedFile"
    assert result["uploaded_attachments"][0]["filename"] == "comment.png"


def test_update_issue_uploads_field_artifact_appends_image_and_links_relation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    image_path = tmp_path / "field.png"
    image_path.write_bytes(b"field-png")
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            200,
            json={
                "id": "att-3",
                "url": "https://dev.azure.com/example/project-x/_apis/wit/attachments/att-3",
            },
        ),
        httpx.Response(200, json={"id": 77}),
    ]
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.update_issue(
        directory_path="root",
        remote_issue_id="77",
        state=None,
        fields={"System.Description": "Updated"},
        field_attachments={
            "System.Description": [
                {
                    "artifact_id": "comm_ghi",
                    "filename": "field.png",
                    "mime_type": "image/png",
                    "byte_count": len(b"field-png"),
                    "path": str(image_path),
                }
            ]
        },
    )

    assert calls[0]["url"] == "https://dev.azure.com/example/project-x/_apis/wit/attachments"
    assert calls[1]["method"] == "PATCH"
    operations = calls[1]["json"]
    assert any(
        item["path"] == "/fields/System.Description"
        and '<img src="https://dev.azure.com/example/project-x/_apis/wit/attachments/att-3"'
        in item["value"]
        for item in operations
    )
    assert operations[-1]["value"]["rel"] == "AttachedFile"
    assert result["uploaded_attachments"][0]["filename"] == "field.png"


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


def test_query_backlog_items_can_use_explicit_wiql() -> None:
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
        wiql="SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'",
        states=["New"],
        types=["Bug"],
    )

    wiql_payload = calls[0]["json"]
    assert isinstance(wiql_payload, dict)
    assert wiql_payload == {
        "query": "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"
    }


def test_query_backlog_items_can_include_relations_for_pr_coverage() -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json={"workItems": [{"id": 101}]}),
        httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 101,
                        "fields": {
                            "System.Title": "Backlog item",
                            "System.State": "Active",
                            "System.WorkItemType": "Task",
                        },
                    }
                ]
            },
        ),
        httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 101,
                        "relations": [
                            {
                                "rel": "ArtifactLink",
                                "url": "vstfs:///Git/PullRequestId/project%2Frepo%2F42",
                                "attributes": {"name": "Pull Request"},
                            }
                        ],
                    }
                ]
            },
        ),
    ]

    result = adapter._query_backlog_items_core(
        _RecordingClient(calls, responses.copy()),
        organization_url="https://dev.azure.com/example",
        project="project-x",
        auth_header="Basic secret",
        include_relations=True,
    )

    batch_payload = calls[1]["json"]
    assert isinstance(batch_payload, dict)
    assert "$expand" not in batch_payload
    assert "fields" in batch_payload
    relation_batch_payload = calls[2]["json"]
    assert isinstance(relation_batch_payload, dict)
    assert relation_batch_payload["$expand"] == "Relations"
    assert "fields" not in relation_batch_payload
    assert result["items"][0]["relations"][0]["attributes"]["name"] == "Pull Request"


def test_query_backlog_items_can_exclude_work_item_types() -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json={"workItems": []}),
    ]

    adapter._query_backlog_items_core(
        _RecordingClient(calls, responses.copy()),
        organization_url="https://dev.azure.com/example",
        project="project-x",
        auth_header="Basic secret",
        exclude_types=["Task"],
    )

    wiql_payload = calls[0]["json"]
    assert isinstance(wiql_payload, dict)
    assert "[System.WorkItemType] <> 'Task'" in wiql_payload["query"]


def test_list_current_iteration_uses_project_default_team_when_team_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "My Project", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json={"defaultTeam": {"name": "TechForce"}}),
        httpx.Response(
            200,
            json={
                "value": [
                    {
                        "path": "My Project\\Sprint 1",
                        "attributes": {
                            "startDate": "2026-05-04T00:00:00Z",
                            "finishDate": "2026-05-18T00:00:00Z",
                        },
                    }
                ]
            },
        ),
        httpx.Response(
            200,
            json={
                "field": {"referenceName": "System.AreaPath"},
                "values": [{"value": "My Project\\TechForce", "includeChildren": True}],
            },
        ),
        httpx.Response(200, json={"workItems": []}),
    ]
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.list_current_iteration_backlog_items(directory_path="root/team-a")

    assert calls[0]["url"] == "https://dev.azure.com/example/_apis/projects/My%20Project"
    assert calls[0]["params"] == {"includeCapabilities": "true", "api-version": "7.1-preview.4"}
    assert (
        calls[1]["url"]
        == "https://dev.azure.com/example/My%20Project/TechForce/_apis/work/teamsettings/iterations"
    )
    assert (
        calls[2]["url"]
        == "https://dev.azure.com/example/My%20Project/TechForce/_apis/work/teamsettings/teamfieldvalues"
    )
    assert result["iteration_path"] == "My Project\\Sprint 1"
    assert result["team"] == "TechForce"
    assert result["iteration_start_date"] == "2026-05-04T00:00:00Z"
    assert result["iteration_finish_date"] == "2026-05-18T00:00:00Z"


def test_list_current_iteration_explicit_team_skips_project_default_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "My Project", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json={"value": [{"path": "My Project\\Sprint 1"}]}),
        httpx.Response(
            200,
            json={
                "field": {"referenceName": "System.AreaPath"},
                "values": [{"value": "My Project\\Platform", "includeChildren": True}],
            },
        ),
        httpx.Response(200, json={"workItems": []}),
    ]
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    adapter.list_current_iteration_backlog_items(
        directory_path="root/team-a",
        team="Platform Team",
    )

    assert len(calls) == 3
    assert (
        calls[0]["url"]
        == "https://dev.azure.com/example/My%20Project/Platform%20Team/_apis/work/teamsettings/iterations"
    )
    assert (
        calls[1]["url"]
        == "https://dev.azure.com/example/My%20Project/Platform%20Team/_apis/work/teamsettings/teamfieldvalues"
    )


def test_list_current_iteration_scopes_wiql_to_team_field_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "My Project", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json={"value": [{"path": "My Project\\Sprint 1"}]}),
        httpx.Response(
            200,
            json={
                "field": {"referenceName": "System.AreaPath"},
                "values": [
                    {"value": "My Project\\TechForce", "includeChildren": True},
                    {"value": "My Project\\Shared Intake", "includeChildren": False},
                ],
            },
        ),
        httpx.Response(200, json={"workItems": []}),
    ]
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    adapter.list_current_iteration_backlog_items(
        directory_path="root/team-a",
        team="TechForce",
        area_path="My Project\\TechForce",
    )

    wiql_payload = calls[2]["json"]
    assert isinstance(wiql_payload, dict)
    query = wiql_payload["query"]
    assert isinstance(query, str)
    assert "[System.IterationPath] UNDER 'My Project\\Sprint 1'" in query
    assert "[System.AreaPath] UNDER 'My Project\\TechForce'" in query
    assert (
        "([System.AreaPath] UNDER 'My Project\\TechForce' "
        "OR [System.AreaPath] = 'My Project\\Shared Intake')"
    ) in query


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


def test_get_backlog_item_extracts_inline_media_from_fields_comments_and_relations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
                "rev": 3,
                "url": "https://dev.azure.com/example/_apis/wit/workItems/101",
                "fields": {
                    "System.TeamProject": "My Project",
                    "System.Title": "Bug with screenshots",
                    "System.Description": (
                        '<p>See <img alt="main shot" '
                        'src="https://dev.azure.com/example/_apis/wit/attachments/desc?fileName=desc.png" /></p>'
                    ),
                },
                "relations": [
                    {
                        "rel": "AttachedFile",
                        "url": "https://dev.azure.com/example/_apis/wit/attachments/rel?fileName=attached.jpg",
                        "attributes": {"name": "attached.jpg"},
                    }
                ],
                "_links": {
                    "web": {
                        "href": "https://dev.azure.com/example/My%20Project/_workitems/edit/101"
                    }
                },
            },
        ),
        httpx.Response(
            200,
            json={
                "comments": [
                    {
                        "id": 7,
                        "text": (
                            '<div>Comment <img title="comment shot" '
                            'src="https://dev.azure.com/example/_apis/wit/attachments/comment?fileName=comment.gif" /></div>'
                        ),
                        "createdBy": {"displayName": "Ada"},
                        "createdDate": "2026-01-02T03:04:05Z",
                        "_links": {"web": {"href": "https://dev.azure.com/comment/7"}},
                    }
                ]
            },
        ),
    ]
    monkeypatch.setattr(
        "agent_sandbox.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    item = adapter.get_backlog_item_by_id(directory_path="root", backlog_item_id="101")

    assert calls[0]["url"] == "https://dev.azure.com/example/My%20Project/_apis/wit/workitems/101"
    assert calls[1]["url"] == "https://dev.azure.com/example/My Project/_apis/wit/workItems/101/comments"
    assert len(item["inline_media"]) == 3
    assert [media["filename"] for media in item["inline_media"]] == [
        "desc.png",
        "attached.jpg",
        "comment.gif",
    ]
    field_media, relation_media, comment_media = item["inline_media"]
    assert field_media["source_kind"] == "ado_backlog"
    assert field_media["resource_id"] == "101"
    assert field_media["metadata"]["field_reference_name"] == "System.Description"
    assert relation_media["metadata"]["source"] == "work_item_relation"
    assert item["relations"][0]["inline_media"]["filename"] == "attached.jpg"
    assert item["comments"][0]["html"].startswith("<div>Comment")
    assert item["comments"][0]["raw_text"].startswith("<div>Comment")
    assert item["comments"][0]["inline_media"][0]["filename"] == "comment.gif"
    assert comment_media["comment_id"] == "7"
