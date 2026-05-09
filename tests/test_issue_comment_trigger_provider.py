from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.models import TriggerDefinitionDiscriminated, materialize_node_definition
from agent_sandbox.models import LocalIssueRecord, RemoteIssueReference
from agent_sandbox.storage import JsonStore
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.issue_comment_created import (
    IssueCommentCreatedTriggerProvider,
)


def _runtime_context(runtime_dir: Path) -> TriggerRuntimeContext:
    return TriggerRuntimeContext(resolved_config=GatewayConfig(runtime_dir=runtime_dir))


def _trigger(**config):
    payload = {
        "id": "issue-comment",
        "display_name": "Issue comment",
        "type": "trigger:issue:comment-created",
        "provider": "ado",
        "organization_url": "https://dev.azure.com/example",
        "project": "APP",
        "directories": ["root/example-industry/app"],
    }
    payload.update(config)
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)
    return materialize_node_definition(adapter.validate_python(payload))


def _provider(runtime_dir: Path, **config) -> IssueCommentCreatedTriggerProvider:
    provider = IssueCommentCreatedTriggerProvider()
    provider.initialize(_trigger(**config), _runtime_context(runtime_dir))
    return provider


def _ado_issue(
    *,
    issue_id: str = "ado:APP:123",
    issue_number: str = "123",
    directory_path: str = "root/example-industry/app",
    project: str = "APP",
    comments: list[dict] | None = None,
) -> LocalIssueRecord:
    return LocalIssueRecord(
        issue_id=issue_id,
        issue_number=issue_number,
        title="Work item",
        body="Description",
        yaml_path=f"runtime/ado-work-items/{issue_number}.yaml",
        directory_path=directory_path,
        source_type="remote_proxy",
        source="ado_worker",
        remote_reference=RemoteIssueReference(
            provider="azure_devops",
            organization="https://dev.azure.com/example",
            project=project,
            work_item_id=issue_number,
            work_item_url=f"https://dev.azure.com/example/APP/_workitems/edit/{issue_number}",
        ),
        url=f"https://dev.azure.com/example/APP/_workitems/edit/{issue_number}",
        comments=comments or [],
        created_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 8, 10, 5, tzinfo=UTC),
    )


def _store_issue(runtime_dir: Path, issue: LocalIssueRecord) -> None:
    JsonStore(runtime_dir / "local-issues.json", LocalIssueRecord).put(issue.issue_id, issue)


def test_new_comment_creates_trigger_activation(tmp_path: Path) -> None:
    _store_issue(
        tmp_path,
        _ado_issue(
            comments=[
                {
                    "id": "c1",
                    "text": "Please investigate this regression.",
                    "createdBy": {"displayName": "Alice", "uniqueName": "alice@example.com"},
                    "createdDate": "2026-05-08T10:02:03Z",
                    "url": "https://dev.azure.com/example/comment/c1",
                }
            ]
        ),
    )

    events = _provider(tmp_path).poll()

    assert len(events) == 1
    assert events[0].trigger_type == "trigger:issue:comment-created"
    assert events[0].payload["issue_id"] == "ado:APP:123"
    assert events[0].payload["comment_id"] == "c1"


def test_output_payload_shape_includes_issue_comment_and_metadata(tmp_path: Path) -> None:
    _store_issue(
        tmp_path,
        _ado_issue(
            comments=[
                {
                    "id": "c2",
                    "text": "Wake the agent.",
                    "createdBy": {"displayName": "Bob"},
                    "createdDate": "2026-05-08T10:03:00Z",
                }
            ]
        ),
    )

    [event] = _provider(tmp_path).poll()

    assert set(event.payload) == {
        "issue_id",
        "issue_number",
        "comment_id",
        "comment_body",
        "author",
        "created_at",
        "url",
        "source",
        "source_type",
        "metadata",
    }
    assert event.payload["comment_body"] == "Wake the agent."
    assert event.payload["author"] == {"displayName": "Bob"}
    assert event.payload["created_at"] == "2026-05-08T10:03:00Z"
    assert event.payload["source"] == "ado_worker"
    assert event.payload["source_type"] == "remote_proxy"
    assert event.payload["metadata"]["directory_path"] == "root/example-industry/app"


def test_duplicate_comments_are_not_emitted_twice(tmp_path: Path) -> None:
    issue = _ado_issue(
        comments=[{"id": "c3", "text": "Run it", "createdDate": "2026-05-08T10:04:00Z"}]
    )
    _store_issue(tmp_path, issue)
    provider = _provider(tmp_path)

    assert [event.payload["comment_id"] for event in provider.poll()] == ["c3"]
    assert provider.poll() == []

    issue.comments.append(
        {"id": "c4", "text": "Any update?", "createdDate": "2026-05-08T10:06:00Z"}
    )
    _store_issue(tmp_path, issue)

    assert [event.payload["comment_id"] for event in provider.poll()] == ["c4"]


def test_provider_specific_edge_cases_are_handled_cleanly(tmp_path: Path) -> None:
    _store_issue(
        tmp_path,
        _ado_issue(
            comments=[
                {
                    "text": "ADO worker comments do not always include ids.",
                    "author": "Automation",
                    "created_date": "2026-05-08T10:07:00Z",
                },
                {"id": "empty-body", "text": "", "createdDate": "not-a-date"},
            ]
        ),
    )

    events = _provider(tmp_path).poll()

    assert len(events) == 2
    generated = events[0].payload if events[0].payload["comment_body"] else events[1].payload
    assert generated["comment_id"].startswith("generated:")
    assert generated["author"] == {"display_name": "Automation"}


def test_non_matching_directories_and_projects_are_ignored(tmp_path: Path) -> None:
    _store_issue(
        tmp_path,
        _ado_issue(
            issue_id="ado:OTHER:1",
            issue_number="1",
            directory_path="root/other",
            comments=[{"id": "wrong-dir", "text": "ignored"}],
        ),
    )
    _store_issue(
        tmp_path,
        _ado_issue(
            issue_id="ado:CRM:2",
            issue_number="2",
            project="CRM",
            comments=[{"id": "wrong-project", "text": "ignored"}],
        ),
    )
    _store_issue(
        tmp_path,
        _ado_issue(comments=[{"id": "match", "text": "emit me"}]),
    )

    events = _provider(tmp_path).poll()

    assert [event.payload["comment_id"] for event in events] == ["match"]


def test_alias_trigger_type_materializes_and_runs(tmp_path: Path) -> None:
    _store_issue(tmp_path, _ado_issue(comments=[{"id": "alias", "text": "hello"}]))

    events = _provider(tmp_path, type="trigger:issue:new-comment").poll()

    assert len(events) == 1
    assert events[0].trigger_type == "trigger:issue:new-comment"
