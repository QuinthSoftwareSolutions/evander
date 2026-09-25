from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from evander.ado_reports import DEFAULT_EFFORT_FIELDS
from evander.issues.service import (
    AzureDevOpsIssueAdapter,
    IssueService,
    extract_ado_field_inline_media_refs,
    extract_ado_html_image_refs,
)


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
    def __init__(
        self,
        calls: list[dict[str, object]],
        responses: list[httpx.Response],
        *,
        get_responses: dict[str, httpx.Response] | None = None,
    ) -> None:
        self._calls = calls
        self._responses = responses
        self._get_responses = get_responses

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
        response = (
            self._get_responses[url] if self._get_responses is not None else self._responses.pop(0)
        )
        return self._with_request(response, method="GET", url=url)

    def delete(self, url: str, **kwargs: object) -> httpx.Response:
        self._calls.append({"method": "DELETE", "url": url, **kwargs})
        return self._with_request(self._responses.pop(0), method="DELETE", url=url)


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
        "evander.issues.service.httpx.Client",
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
        "evander.issues.service.httpx.Client",
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
                    "web": {"href": "https://dev.azure.com/example/project-x/_workitems/edit/77"}
                },
            },
        ),
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
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


def test_post_comment_formats_markdownish_body_as_explicit_ado_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            200,
            json={
                "id": 5,
                "text": "<p>Summary</p><ul><li>First item</li></ul>",
                "_links": {"web": {"href": "https://dev.azure.com/comment/5"}},
            },
        ),
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    adapter.post_comment(
        directory_path="root",
        remote_issue_id="77",
        comment="Summary\n- First item",
    )

    assert (
        calls[0]["url"] == "https://dev.azure.com/example/project-x/_apis/wit/workItems/77/comments"
    )
    assert calls[0]["params"] == {"api-version": "7.1-preview.4", "format": "html"}
    assert calls[0]["json"]["text"] == "<p>Summary</p><ul><li>First item</li></ul>"


def test_post_comment_appends_correlation_marker_after_rendering_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            200,
            json={
                "id": 5,
                "text": "Comment",
                "_links": {"web": {"href": "https://dev.azure.com/comment/5"}},
            },
        ),
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )
    marker = f"<!-- evander:ado-work-item-comment:v1:key={'a' * 64};payload={'b' * 64} -->"

    adapter.post_comment(
        directory_path="root",
        remote_issue_id="77",
        comment="Summary\n- First item",
        correlation_marker=marker,
    )

    assert calls[0]["json"]["text"] == ("<p>Summary</p><ul><li>First item</li></ul>\n" + marker)


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
        "evander.issues.service.httpx.Client",
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
    assert (
        calls[1]["url"] == "https://dev.azure.com/example/project-x/_apis/wit/workItems/77/comments"
    )
    assert calls[1]["params"] == {"api-version": "7.1-preview.4", "format": "html"}
    assert calls[1]["json"]["text"].startswith("<p>Comment</p>")
    assert (
        '<img src="https://dev.azure.com/example/project-x/_apis/wit/attachments/att-2"'
        in calls[1]["json"]["text"]
    )
    assert calls[2]["method"] == "PATCH"
    assert calls[2]["json"][0]["value"]["rel"] == "AttachedFile"
    assert result["uploaded_attachments"][0]["filename"] == "comment.png"


def test_post_comment_resolves_ado_mentions_before_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "providerDisplayName": "Ada Lovelace",
                        "uniqueName": "ada@example.com",
                    }
                ]
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
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.post_comment(
        directory_path="root",
        remote_issue_id="77",
        comment="Comment",
        mentions=["ada@example.com"],
    )

    assert calls[0]["method"] == "GET"
    assert calls[0]["url"] == "https://vssps.dev.azure.com/example/_apis/identities"
    assert calls[0]["params"]["filterValue"] == "ada@example.com"
    assert (
        calls[1]["url"] == "https://dev.azure.com/example/project-x/_apis/wit/workItems/77/comments"
    )
    assert calls[1]["params"] == {"api-version": "7.1-preview.4", "format": "html"}
    assert calls[1]["json"]["text"] == (
        '<p><a href="#" data-vss-mention="version:2.0,'
        '11111111-1111-1111-1111-111111111111">@Ada Lovelace</a></p>\n'
        "<p>Comment</p>"
    )
    assert result["resolved_mentions"] == [
        {
            "input": "ada@example.com",
            "id": "11111111-1111-1111-1111-111111111111",
            "display_name": "Ada Lovelace",
            "unique_name": "ada@example.com",
        }
    ]


def test_resolve_comment_mentions_returns_audit_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "providerDisplayName": "Ada Lovelace",
                        "uniqueName": "ada@example.com",
                    }
                ]
            },
        )
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.resolve_comment_mentions(
        directory_path="root",
        mentions=["ada@example.com"],
    )

    assert calls[0]["url"] == "https://vssps.dev.azure.com/example/_apis/identities"
    assert result == [
        {
            "input": "ada@example.com",
            "id": "11111111-1111-1111-1111-111111111111",
            "display_name": "Ada Lovelace",
            "unique_name": "ada@example.com",
        }
    ]


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
        "evander.issues.service.httpx.Client",
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


def test_update_issue_adds_expected_state_test_before_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "project-x", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [httpx.Response(200, json={"id": 77})]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.update_issue(
        directory_path="root",
        project="override-project",
        remote_issue_id="77",
        state="In Progress",
        expected_state="New",
        fields={"Custom.Priority": 2},
    )

    assert calls[0]["url"] == (
        "https://dev.azure.com/example/override-project/_apis/wit/workitems/77"
    )
    assert calls[0]["json"] == [
        {"op": "test", "path": "/fields/System.State", "value": "New"},
        {"op": "add", "path": "/fields/System.State", "value": "In Progress"},
        {"op": "add", "path": "/fields/Custom.Priority", "value": 2},
    ]
    assert result["expected_state"] == "New"
    assert result["state"] == "In Progress"


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
        "evander.issues.service.httpx.Client",
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
        "evander.issues.service.httpx.Client",
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
        "evander.issues.service.httpx.Client",
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
        "evander.issues.service.httpx.Client",
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
        "evander.issues.service.httpx.Client",
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


def test_query_backlog_items_exposes_assigned_to_identity_and_revision() -> None:
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
                        "rev": 7,
                        "fields": {
                            "System.Title": "Backlog item",
                            "System.State": "To Do",
                            "System.WorkItemType": "Task",
                            "System.AssignedTo": {
                                "displayName": "Ada Lovelace",
                                "uniqueName": "ada@example.com",
                                "mailAddress": "ada@users.example.com",
                                "id": "identity-1",
                                "descriptor": "aad.identity-1",
                            },
                        },
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
    )

    assert result["items"][0]["rev"] == 7
    assert result["items"][0]["assigned_to"] == "Ada Lovelace"
    assert result["items"][0]["assigned_to_identity"] == {
        "display_name": "Ada Lovelace",
        "unique_name": "ada@example.com",
        "email": "ada@example.com",
        "id": "identity-1",
        "descriptor": "aad.identity-1",
    }


def test_read_issues_batch_uses_workitemsbatch_without_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "default-project", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 101,
                        "url": "https://dev.azure.com/example/_apis/wit/workitems/101",
                        "_links": {
                            "web": {
                                "href": "https://dev.azure.com/example/project-x/_workitems/edit/101"
                            }
                        },
                        "fields": {
                            "System.Title": "Remote issue",
                            "System.Description": "Remote body",
                            "System.State": "To Do",
                            "System.WorkItemType": "Bug",
                            "System.AreaPath": "project-x\\Area",
                            "System.IterationPath": "project-x\\Sprint",
                            "System.AssignedTo": {"displayName": "Alice"},
                            "System.Tags": "auto-dev; backend",
                            "System.ChangedDate": "2026-05-15T10:11:12Z",
                        },
                    }
                ]
            },
        )
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.read_issues_batch(
        directory_path="root/team",
        project="project-x",
        remote_issue_ids=["101", "999"],
    )

    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://dev.azure.com/example/_apis/wit/workitemsbatch"
    payload = calls[0]["json"]
    assert isinstance(payload, dict)
    assert payload["ids"] == [101, 999]
    assert payload["errorPolicy"] == "Omit"
    assert "fields" in payload
    assert "$expand" not in payload
    assert result["items"]["101"]["title"] == "Remote issue"
    assert result["items"]["101"]["description"] == "Remote body"
    assert result["items"]["101"]["tags"] == ["auto-dev", "backend"]
    assert result["items"]["101"]["assigned_to"] == "Alice"
    assert result["missing_ids"] == ["999"]


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
    assert isinstance(batch_payload["fields"], list)
    assert set(DEFAULT_EFFORT_FIELDS).issubset(batch_payload["fields"])
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
        "evander.issues.service.httpx.Client",
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
        "evander.issues.service.httpx.Client",
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
        "evander.issues.service.httpx.Client",
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
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    adapter.get_backlog_item_by_id(
        directory_path="root", backlog_item_id="101", include_comments=False
    )

    assert calls[0]["url"] == "https://dev.azure.com/example/My%20Project/_apis/wit/workitems/101"


@pytest.mark.parametrize("markdown", [False, True])
def test_get_backlog_item_extracts_inline_media_from_fields_comments_and_relations(
    monkeypatch: pytest.MonkeyPatch,
    markdown: bool,
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
                        "![main shot](https://dev.azure.com/example/_apis/wit/attachments/desc?fileName=desc.png)"
                        if markdown
                        else '<p>See <img alt="main shot" '
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
                            '![comment shot](https://dev.azure.com/example/_apis/wit/attachments/comment?fileName=comment.gif "comment shot")'
                            if markdown
                            else '<div>Comment <img title="comment shot" '
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
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    item = adapter.get_backlog_item_by_id(directory_path="root", backlog_item_id="101")

    assert calls[0]["url"] == "https://dev.azure.com/example/My%20Project/_apis/wit/workitems/101"
    assert (
        calls[1]["url"]
        == "https://dev.azure.com/example/My Project/_apis/wit/workItems/101/comments"
    )
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
    assert item["comments"][0]["html"].startswith("![" if markdown else "<div>Comment")
    assert item["comments"][0]["raw_text"].startswith("![" if markdown else "<div>Comment")
    assert item["comments"][0]["created_by_identity"]["displayName"] == "Ada"
    assert item["comments"][0]["inline_media"][0]["filename"] == "comment.gif"
    assert comment_media["comment_id"] == "7"


@pytest.mark.parametrize(
    "field",
    [
        "System.Description",
        "Microsoft.VSTS.TCM.ReproSteps",
        "Microsoft.VSTS.TCM.SystemInfo",
        "Microsoft.VSTS.Common.AcceptanceCriteria",
    ],
)
def test_ado_markdown_images_are_extracted_from_rich_text_fields(field: str) -> None:
    url = "https://dev.azure.com/example/_apis/wit/attachments/"
    text = (
        f"Operator dialog\n![image.png]({url}first?fileName=image.png)\n\n"
        f"Admin report\n![image.png]({url}second?fileName=image.png)"
    )
    refs = extract_ado_field_inline_media_refs(
        {field: text}, provider="azure_devops", resource_id="101"
    )
    assert len(refs) == 2
    assert [ref["url"] for ref in refs] == [
        f"{url}first?fileName=image.png",
        f"{url}second?fileName=image.png",
    ]
    assert len({ref["media_id"] for ref in refs}) == 2
    assert all(ref["filename"] == "image.png" for ref in refs)
    assert all(ref["metadata"]["field_reference_name"] == field for ref in refs)
    assert all(ref["metadata"]["source"] == "markdown_image" for ref in refs)


def test_ado_markdown_images_handle_references_escapes_html_and_code() -> None:
    text = r"""
![dialog][shot]

[shot]: <https://dev.azure.com/example/dialog(1).png?x=1&y=2> "Dialog title"

![report](https://dev.azure.com/example/report\(2\).png)

Inline <img src="https://dev.azure.com/example/legacy.png?a=1&amp;b=2" alt="Legacy">.

`![code](https://dev.azure.com/example/code.png)`

```markdown
![fenced](https://dev.azure.com/example/fenced.png)
<img src="https://dev.azure.com/example/fenced-html.png">
```

    ![indented](https://dev.azure.com/example/indented.png)

\![escaped](https://dev.azure.com/example/escaped.png)
[ordinary link](https://dev.azure.com/example/link.png)
![unresolved][missing]
"""
    refs = extract_ado_html_image_refs(
        text, provider="azure_devops", resource_id="101", media_id_prefix="test-image"
    )
    assert [ref["url"] for ref in refs] == [
        "https://dev.azure.com/example/dialog(1).png?x=1&y=2",
        "https://dev.azure.com/example/report(2).png",
        "https://dev.azure.com/example/legacy.png?a=1&b=2",
    ]
    assert [ref["media_id"] for ref in refs] == ["test-image-0", "test-image-1", "test-image-2"]
    assert refs[0]["metadata"]["title"] == "Dialog title"
    assert refs[2]["metadata"]["source"] == "html_image"


def test_query_work_item_activity_filters_created_comments_and_updates(
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
        httpx.Response(200, json={"workItems": [{"id": 101}]}),
        httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 101,
                        "fields": {
                            "System.Title": "Activity item",
                            "System.State": "Active",
                            "System.WorkItemType": "Task",
                            "System.CreatedDate": "2026-06-01T09:00:00Z",
                            "System.CreatedBy": {
                                "displayName": "Alex Example",
                                "uniqueName": "alex@example.com",
                            },
                            "System.ChangedDate": "2026-06-03T11:00:00Z",
                            "System.ChangedBy": {
                                "displayName": "Alex Example",
                                "uniqueName": "alex@example.com",
                            },
                        },
                        "_links": {
                            "web": {
                                "href": "https://dev.azure.com/example/My%20Project/_workitems/edit/101"
                            }
                        },
                    }
                ]
            },
        ),
        httpx.Response(
            200,
            json={
                "id": 101,
                "url": "https://dev.azure.com/example/_apis/wit/workItems/101",
                "fields": {
                    "System.TeamProject": "My Project",
                    "System.Title": "Activity item",
                    "System.State": "Active",
                    "System.WorkItemType": "Task",
                    "System.CreatedDate": "2026-06-01T09:00:00Z",
                    "System.CreatedBy": {
                        "displayName": "Alex Example",
                        "uniqueName": "alex@example.com",
                    },
                    "System.ChangedDate": "2026-06-03T11:00:00Z",
                    "System.ChangedBy": {
                        "displayName": "Alex Example",
                        "uniqueName": "alex@example.com",
                    },
                },
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
                        "text": "Documented reporting impact.",
                        "createdBy": {
                            "displayName": "Alex Example",
                            "uniqueName": "alex@example.com",
                        },
                        "createdDate": "2026-06-04T12:00:00Z",
                    },
                    {
                        "id": 8,
                        "text": "Other user's comment.",
                        "createdBy": {
                            "displayName": "Other User",
                            "uniqueName": "other@example.com",
                        },
                        "createdDate": "2026-06-04T13:00:00Z",
                    },
                ]
            },
        ),
        httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 5,
                        "rev": 5,
                        "revisedDate": "2026-06-02T10:00:00Z",
                        "revisedBy": {
                            "displayName": "Alex Example",
                            "uniqueName": "alex@example.com",
                        },
                        "fields": {
                            "System.Title": {
                                "oldValue": "Old title",
                                "newValue": "Activity item",
                            }
                        },
                    },
                    {
                        "id": 6,
                        "rev": 6,
                        "revisedDate": "2026-06-04T14:00:00Z",
                        "revisedBy": {
                            "displayName": "Other User",
                            "uniqueName": "other@example.com",
                        },
                        "fields": {"System.State": {"newValue": "Closed"}},
                    },
                ]
            },
        ),
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses),
    )

    result = adapter.query_work_item_activity(
        directory_path="root",
        actor="alex@example.com",
        since=datetime(2026, 6, 1, tzinfo=UTC),
        until=datetime(2026, 6, 5, tzinfo=UTC),
        states=["Active"],
        types=["Task"],
        tags_any=["reporting"],
    )

    wiql_payload = calls[0]["json"]
    assert isinstance(wiql_payload, dict)
    query = wiql_payload["query"]
    assert "[System.State] IN ('Active')" in query
    assert "[System.WorkItemType] IN ('Task')" in query
    assert "[System.Tags] CONTAINS 'reporting'" in query
    assert result["count"] == 1
    item = result["items"][0]
    assert item["id"] == "101"
    assert [activity["provenance"] for activity in item["activities"]] == [
        "comment",
        "changed_field",
        "update",
        "created_field",
    ]
    assert item["activities"][0]["comment_id"] == "7"
    assert item["activities"][0]["actor_identity"]["uniqueName"] == "alex@example.com"
    assert item["activities"][2]["fields"] == {
        "System.Title": {"oldValue": "Old title", "newValue": "Activity item"}
    }


def test_query_work_item_activity_uses_update_changed_by_for_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "My Project", "pat"),
    )

    def make_responses() -> list[httpx.Response]:
        return [
            httpx.Response(200, json={"workItems": [{"id": 101}]}),
            httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": 101,
                            "fields": {
                                "System.TeamProject": "My Project",
                                "System.Title": "Activity item",
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
                    "id": 101,
                    "fields": {
                        "System.TeamProject": "My Project",
                        "System.Title": "Activity item",
                        "System.State": "Active",
                        "System.WorkItemType": "Task",
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": 5,
                            "rev": 5,
                            "revisedDate": "2026-06-02T10:00:00Z",
                            "revisedBy": {
                                "displayName": "Jonathan Quinth",
                                "uniqueName": "jonathan@example.com",
                            },
                            "fields": {
                                "System.ChangedBy": {
                                    "newValue": {
                                        "displayName": "Other User",
                                        "uniqueName": "other@example.com",
                                    }
                                },
                                "System.ChangedDate": {"newValue": "2026-06-02T10:00:00Z"},
                                "System.Tags": {
                                    "oldValue": "old-tag",
                                    "newValue": "old-tag; review",
                                },
                            },
                        },
                        {
                            "id": 6,
                            "rev": 6,
                            "revisedDate": "2026-06-02T11:00:00Z",
                            "revisedBy": {
                                "displayName": "Jonathan Quinth",
                                "uniqueName": "jonathan@example.com",
                            },
                            "fields": {
                                "System.ChangedBy": {
                                    "newValue": {
                                        "displayName": "Other User",
                                        "uniqueName": "other@example.com",
                                    }
                                },
                                "System.ChangedDate": {"newValue": "2026-06-02T11:00:00Z"},
                                "System.Rev": {"newValue": 6},
                                "System.Watermark": {"newValue": 12345},
                            },
                        },
                    ]
                },
            ),
        ]

    calls: list[dict[str, object]] = []
    current_responses = {"items": make_responses()}
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, current_responses["items"]),
    )

    wrong_actor_result = adapter.query_work_item_activity(
        directory_path="root",
        actor="jonathan@example.com",
        since=datetime(2026, 6, 1, tzinfo=UTC),
        until=datetime(2026, 6, 3, tzinfo=UTC),
        activity_types=["changed"],
    )
    assert wrong_actor_result["count"] == 0

    current_responses["items"] = make_responses()
    result = adapter.query_work_item_activity(
        directory_path="root",
        actor="other@example.com",
        since=datetime(2026, 6, 1, tzinfo=UTC),
        until=datetime(2026, 6, 3, tzinfo=UTC),
        activity_types=["changed"],
    )

    assert result["count"] == 1
    activities = result["items"][0]["activities"]
    assert len(activities) == 1
    activity = activities[0]
    assert activity["actor"] == "Other User"
    assert activity["actor_identity"]["unique_name"] == "other@example.com"
    assert activity["summary"] == "Updated fields: System.Tags"
    assert activity["fields"] == {
        "System.Tags": {"oldValue": "old-tag", "newValue": "old-tag; review"}
    }


@pytest.mark.parametrize(
    ("skip", "expected_ids", "expected_next_skip"),
    [
        (0, ["101"], 1),
        (1, ["102"], None),
    ],
)
def test_query_work_item_activity_paginates_after_activity_filtering(
    monkeypatch: pytest.MonkeyPatch,
    skip: int,
    expected_ids: list[str],
    expected_next_skip: int | None,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "My Project", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(200, json={"workItems": [{"id": 202}, {"id": 101}, {"id": 102}]}),
        httpx.Response(
            200,
            json={
                "value": [
                    {"id": 202, "fields": {"System.Title": "Other user created"}},
                    {"id": 101, "fields": {"System.Title": "Alex created newest"}},
                    {"id": 102, "fields": {"System.Title": "Alex created older"}},
                ]
            },
        ),
        httpx.Response(
            200,
            json={
                "id": 202,
                "fields": {
                    "System.TeamProject": "My Project",
                    "System.Title": "Other user created",
                    "System.State": "Active",
                    "System.WorkItemType": "Task",
                    "System.CreatedDate": "2026-06-04T12:00:00Z",
                    "System.CreatedBy": {
                        "displayName": "Other User",
                        "uniqueName": "other@example.com",
                    },
                },
            },
        ),
        httpx.Response(
            200,
            json={
                "id": 101,
                "fields": {
                    "System.TeamProject": "My Project",
                    "System.Title": "Alex created newest",
                    "System.State": "Active",
                    "System.WorkItemType": "Task",
                    "System.CreatedDate": "2026-06-03T12:00:00Z",
                    "System.CreatedBy": {
                        "displayName": "Alex Example",
                        "uniqueName": "alex@example.com",
                    },
                },
            },
        ),
        httpx.Response(
            200,
            json={
                "id": 102,
                "fields": {
                    "System.TeamProject": "My Project",
                    "System.Title": "Alex created older",
                    "System.State": "Active",
                    "System.WorkItemType": "Task",
                    "System.CreatedDate": "2026-06-02T12:00:00Z",
                    "System.CreatedBy": {
                        "displayName": "Alex Example",
                        "uniqueName": "alex@example.com",
                    },
                },
            },
        ),
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(
            calls,
            responses[:2],
            get_responses={
                f"https://dev.azure.com/example/My%20Project/_apis/wit/workitems/{response.json()['id']}": response
                for response in responses[2:]
            },
        ),
    )

    result = adapter.query_work_item_activity(
        directory_path="root",
        actor="alex@example.com",
        since=datetime(2026, 6, 1, tzinfo=UTC),
        until=datetime(2026, 6, 5, tzinfo=UTC),
        activity_types=["created"],
        top=1,
        skip=skip,
    )

    assert [item["id"] for item in result["items"]] == expected_ids
    assert result["paging"]["skip"] == skip
    assert result["paging"]["top"] == 1
    assert result["paging"]["total_matches"] == 2
    assert result["paging"]["next_skip"] == expected_next_skip
    batch_body = calls[1]["json"]
    assert isinstance(batch_body, dict)
    assert batch_body["ids"] == [202, 101, 102]


def test_query_work_item_activity_bounds_candidate_scan(
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
        httpx.Response(200, json={"workItems": [{"id": 101}, {"id": 102}, {"id": 103}]}),
        httpx.Response(
            200,
            json={
                "value": [
                    {"id": 101, "fields": {"System.Title": "First"}},
                    {"id": 102, "fields": {"System.Title": "Second"}},
                ]
            },
        ),
        httpx.Response(
            200,
            json={
                "id": 101,
                "fields": {
                    "System.TeamProject": "My Project",
                    "System.Title": "First",
                    "System.CreatedDate": "2026-06-02T10:00:00Z",
                    "System.CreatedBy": {
                        "displayName": "Alex Example",
                        "uniqueName": "alex@example.com",
                    },
                },
            },
        ),
        httpx.Response(
            200,
            json={
                "id": 102,
                "fields": {
                    "System.TeamProject": "My Project",
                    "System.Title": "Second",
                    "System.CreatedDate": "2026-06-02T11:00:00Z",
                    "System.CreatedBy": {
                        "displayName": "Alex Example",
                        "uniqueName": "alex@example.com",
                    },
                },
            },
        ),
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(
            calls,
            responses[:2],
            get_responses={
                f"https://dev.azure.com/example/My%20Project/_apis/wit/workitems/{response.json()['id']}": response
                for response in responses[2:]
            },
        ),
    )

    result = adapter.query_work_item_activity(
        directory_path="root",
        actor="alex@example.com",
        since=datetime(2026, 6, 1, tzinfo=UTC),
        until=datetime(2026, 6, 3, tzinfo=UTC),
        activity_types=["created"],
        max_candidates=2,
    )

    assert [item["id"] for item in result["items"]] == ["101", "102"]
    assert result["paging"]["candidate_scanned_count"] == 2
    assert result["paging"]["candidate_scan_complete"] is False
    assert result["paging"]["total_matches_complete"] is False
    assert result["paging"]["total_activity_matches_complete"] is False
    assert result["paging"]["has_more"] is True
    batch_body = calls[1]["json"]
    assert isinstance(batch_body, dict)
    assert batch_body["ids"] == [101, 102]
    assert all("103" not in str(call["url"]) for call in calls)


def test_delete_issue_soft_delete_uses_destroy_false_and_returns_payload_project(
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
                "project": "My Project",
            },
        )
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.delete_issue(directory_path="root", remote_issue_id="101")

    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"] == "https://dev.azure.com/example/My%20Project/_apis/wit/workItems/101"
    assert calls[0]["params"] == {"api-version": "7.1", "destroy": "false"}
    assert calls[0]["headers"] == {"Authorization": "Basic OnBhdA=="}
    assert result == {
        "provider": "azure_devops",
        "work_item_id": "101",
        "project": "My Project",
        "deleted": True,
        "destroy": False,
        "delete_kind": "soft_delete",
    }


def test_delete_issue_permanent_destroy_sets_destroy_true_and_falls_back_to_requested_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "Default Project", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [httpx.Response(204)]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    result = adapter.delete_issue(
        directory_path="root",
        remote_issue_id="102",
        project="Example Project",
        destroy=True,
    )

    assert (
        calls[0]["url"] == "https://dev.azure.com/example/Example%20Project/_apis/wit/workItems/102"
    )
    assert calls[0]["params"] == {"api-version": "7.1", "destroy": "true"}
    assert result["work_item_id"] == "102"
    assert result["project"] == "Example Project"
    assert result["destroy"] is True
    assert result["delete_kind"] == "permanent_destroy"


def test_delete_issue_raises_clear_error_for_ado_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AzureDevOpsIssueAdapter(config_resolver=object())
    monkeypatch.setattr(
        adapter,
        "_resolve_connection",
        lambda directory_path: ("https://dev.azure.com/example", "Project", "pat"),
    )
    calls: list[dict[str, object]] = []
    responses = [
        httpx.Response(
            404,
            json={"message": "Work item does not exist"},
            headers={"Content-Type": "application/json"},
        )
    ]
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda timeout: _RecordingClient(calls, responses.copy()),
    )

    with pytest.raises(
        RuntimeError,
        match=r"Azure DevOps delete work item failed \(status 404\): Work item does not exist",
    ):
        adapter.delete_issue(directory_path="root", remote_issue_id="404")


def test_delete_remote_issue_forwards_to_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    service = IssueService(config_resolver=object())
    calls: list[dict[str, object]] = []

    class _Adapter:
        def delete_issue(self, **kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {"provider": "azure_devops", "deleted": True}

    service.adapters["azure_devops"] = _Adapter()  # type: ignore[assignment]

    result = service.delete_remote_issue(
        provider="azure_devops",
        remote_issue_id="101",
        directory_path="root/team-a",
        project="example-project",
        destroy=False,
    )

    assert calls == [
        {
            "remote_issue_id": "101",
            "directory_path": "root/team-a",
            "project": "example-project",
            "destroy": False,
        }
    ]
    assert result == {"provider": "azure_devops", "deleted": True}


def test_delete_remote_issue_rejects_unsupported_github_delete_operation() -> None:
    service = IssueService(config_resolver=object())

    with pytest.raises(ValueError, match="GitHub issue deletion is not supported"):
        service.delete_remote_issue(
            provider="github",
            remote_issue_id="101",
            directory_path="root",
        )
