from __future__ import annotations

import json
from pathlib import Path

from evander.events.provider_models import RemoteIssueCommentCreatedTriggerDefinition
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.remote_issue_comment_created import (
    RemoteIssueCommentCreatedTriggerProvider,
)


class _IssueServiceStub:
    query_result: dict[str, object] = {}
    detail_by_id: dict[str, dict[str, object]] = {}
    calls: list[dict[str, object]] = []

    def __init__(self, resolver: object) -> None:
        del resolver

    def query_remote_issues(self, **kwargs: object) -> dict[str, object]:
        self.calls.append({"method": "query", **kwargs})
        return self.query_result

    def read_remote_issue(self, **kwargs: object) -> dict[str, object]:
        self.calls.append({"method": "read", **kwargs})
        return self.detail_by_id[str(kwargs["remote_issue_id"])]


def _provider(
    tmp_path: Path,
    **config: object,
) -> RemoteIssueCommentCreatedTriggerProvider:
    trigger = RemoteIssueCommentCreatedTriggerDefinition(
        id="remote-comment",
        display_name="Remote issue comment",
        type="trigger:remote-issue-comment-created",
        provider="github",
        directory_path="root/github",
        repo="owner/repo",
        **config,
    )
    provider = RemoteIssueCommentCreatedTriggerProvider()
    provider.initialize(
        trigger,
        TriggerRuntimeContext(
            provider_config={"runtime_dir": str(tmp_path)},
            provider_config_resolver=object(),
            metadata={"directory_path": "root"},
        ),
    )
    return provider


def _detail(*, comments: list[dict[str, object]]) -> dict[str, object]:
    return {
        "provider": "github",
        "repo": "owner/repo",
        "remote_issue_id": "42",
        "issue_number": "42",
        "title": "Refine spec",
        "state": "open",
        "updated_at": "2026-06-01T10:00:00Z",
        "comments": comments,
    }


def test_remote_issue_comment_trigger_baselines_existing_comments(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.remote_issue_comment_created.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.query_result = {
        "provider": "github",
        "repo": "owner/repo",
        "items": [{"remote_issue_id": "42", "issue_number": "42", "repo": "owner/repo"}],
    }
    _IssueServiceStub.detail_by_id = {
        "42": _detail(
            comments=[
                {
                    "id": "c1",
                    "body": "Existing mention for @WorkflowBot.",
                    "author": {"login": "alice", "id": "u1"},
                    "created_at": "2026-06-01T10:01:00Z",
                }
            ]
        )
    }
    provider = _provider(tmp_path, labels_any=["triage"], comment_contains="@WorkflowBot")

    assert provider.poll() == []
    assert _IssueServiceStub.calls[0]["labels_any"] == ["triage"]
    cursor_payload = json.loads(
        (tmp_path / "trigger-cursors-remote-issue-comments.json").read_text(
            encoding="utf-8"
        )
    )
    stream_state = cursor_payload["remote-comment"]["streams"]["github|root/github|owner/repo|42"]
    assert stream_state["recent_comment_keys"] == ["c1"]

    _IssueServiceStub.detail_by_id = {
        "42": _detail(
            comments=[
                {
                    "id": "c1",
                    "body": "Existing mention for @WorkflowBot.",
                    "author": {"login": "alice", "id": "u1"},
                    "created_at": "2026-06-01T10:01:00Z",
                },
                {
                    "id": "c2",
                    "body": "New mention for @WorkflowBot.",
                    "author": {"login": "bob", "id": "u2"},
                    "created_at": "2026-06-01T10:02:00Z",
                },
            ]
        )
    }

    events = provider.poll()
    assert len(events) == 1
    assert events[0].payload["provider"] == "github"
    assert events[0].payload["remote_issue_id"] == "42"
    assert events[0].payload["comment_id"] == "c2"
    assert events[0].payload["comment_body"] == "New mention for @WorkflowBot."
    assert events[0].payload["comment_author"]["names"] == ["bob"]

    provider.acknowledge_published_event(events[0])
    assert provider.poll() == []


def test_remote_issue_comment_trigger_filters_excluded_authors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.remote_issue_comment_created.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.query_result = {
        "provider": "github",
        "repo": "owner/repo",
        "items": [{"remote_issue_id": "42", "issue_number": "42", "repo": "owner/repo"}],
    }
    _IssueServiceStub.detail_by_id = {
        "42": _detail(
            comments=[
                {
                    "id": "c1",
                    "body": "Please help, @SpecAssistant.",
                    "author": {"login": "alice", "id": "u1"},
                    "created_at": "2026-06-01T10:01:00Z",
                },
                {
                    "id": "c2",
                    "body": "Reply from @SpecAssistant.",
                    "author": {"login": "bot", "id": "configured-bot-id"},
                    "created_at": "2026-06-01T10:02:00Z",
                },
            ]
        )
    }
    provider = _provider(
        tmp_path,
        include_existing=True,
        comment_contains="@SpecAssistant",
        comment_author_exclude_ids=["configured-bot-id"],
    )

    events = provider.poll()
    assert [event.payload["comment_id"] for event in events] == ["c1"]
