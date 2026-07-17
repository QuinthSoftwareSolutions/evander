from __future__ import annotations

import json
from pathlib import Path

from evander.events.provider_models import (
    AdoWorkItemCommentCreatedTriggerDefinition,
)
from evander.triggers.providers.ado_work_item_comment_created import (
    AdoWorkItemCommentCreatedTriggerProvider,
)
from evander.triggers.providers.base import TriggerRuntimeContext


class _IssueServiceStub:
    query_result: dict[str, object] = {}
    detail_by_id: dict[str, dict[str, object]] = {}
    calls: list[dict[str, object]] = []

    def __init__(self, resolver: object) -> None:
        del resolver

    def query_remote_backlog_items(self, **kwargs: object) -> dict[str, object]:
        self.calls.append({"method": "query", **kwargs})
        return self.query_result

    def list_remote_current_iteration_backlog_items(self, **kwargs: object) -> dict[str, object]:
        self.calls.append({"method": "current_iteration", **kwargs})
        return self.query_result

    def get_remote_backlog_item_by_id(self, **kwargs: object) -> dict[str, object]:
        self.calls.append({"method": "get", **kwargs})
        return self.detail_by_id[str(kwargs["backlog_item_id"])]


def _provider(
    tmp_path: Path,
    **config: object,
) -> AdoWorkItemCommentCreatedTriggerProvider:
    trigger = AdoWorkItemCommentCreatedTriggerDefinition(
        id="ado-comment",
        display_name="ADO comment",
        type="trigger:ado:work-item-comment-created",
        directory_path="root/team-a",
        **config,
    )
    provider = AdoWorkItemCommentCreatedTriggerProvider()
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
        "provider": "azure_devops",
        "id": "101",
        "project": "APP",
        "title": "Refine spec",
        "state": "New",
        "changed_at": "2026-06-01T10:00:00Z",
        "fields": {
            "System.Id": 101,
            "System.TeamProject": "APP",
            "System.Tags": "spec-tag",
            "System.ChangedDate": "2026-06-01T10:00:00Z",
        },
        "comments": comments,
    }


def test_ado_work_item_comment_trigger_baselines_existing_comments(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.ado_work_item_comment_created.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.query_result = {
        "project": "APP",
        "items": [{"id": "101", "fields": {"System.TeamProject": "APP"}}],
    }
    _IssueServiceStub.detail_by_id = {
        "101": _detail(
            comments=[
                {
                    "id": "c1",
                    "text": "Existing mention for @WorkflowBot.",
                    "created_by": "Alice",
                    "created_date": "2026-06-01T10:01:00Z",
                }
            ]
        )
    }
    provider = _provider(
        tmp_path,
        states=["New"],
        tags_any=["spec-tag"],
        comment_contains="@WorkflowBot",
    )

    assert provider.poll() == []
    assert _IssueServiceStub.calls[0]["states"] == ["New"]
    assert _IssueServiceStub.calls[0]["tags_any"] == ["spec-tag"]
    cursor_payload = json.loads(
        (tmp_path / "trigger-cursors-ado-work-item-comments.json").read_text(encoding="utf-8")
    )
    stream_state = cursor_payload["ado-comment"]["streams"]["azure_devops|root/team-a|APP|101"]
    assert stream_state["recent_comment_keys"] == ["c1"]

    _IssueServiceStub.detail_by_id = {
        "101": _detail(
            comments=[
                {
                    "id": "c1",
                    "text": "Existing mention for @WorkflowBot.",
                    "created_by": "Alice",
                    "created_date": "2026-06-01T10:01:00Z",
                },
                {
                    "id": "c2",
                    "text": "New mention for @WorkflowBot.",
                    "created_by": "Bob",
                    "created_date": "2026-06-01T10:02:00Z",
                },
            ]
        )
    }

    events = provider.poll()
    assert len(events) == 1
    assert events[0].payload["work_item_id"] == "101"
    assert events[0].payload["comment_id"] == "c2"
    assert events[0].payload["comment_body"] == "New mention for @WorkflowBot."
    assert events[0].payload["work_item"]["comments"][1]["id"] == "c2"

    provider.acknowledge_published_event(events[0])
    assert provider.poll() == []


def test_ado_work_item_comment_trigger_uses_workflow_configured_filters(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.ado_work_item_comment_created.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.query_result = {"project": "APP", "items": [{"id": "101"}]}
    _IssueServiceStub.detail_by_id = {
        "101": _detail(
            comments=[
                {
                    "id": "c1",
                    "text": "Please help, @SpecAssistant.",
                    "created_by": "Alice",
                    "created_date": "2026-06-01T10:01:00Z",
                },
                {
                    "id": "c2",
                    "text": "Reply from @SpecAssistant.",
                    "created_by": "Configured bot display",
                    "created_by_identity": {
                        "displayName": "Configured bot display",
                        "id": "configured-bot-id",
                    },
                    "created_date": "2026-06-01T10:02:00Z",
                },
                {
                    "id": "c3",
                    "text": "No configured mention here.",
                    "created_by": "Carol",
                    "created_date": "2026-06-01T10:03:00Z",
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
    assert events[0].payload["comment_author"]["names"] == ["Alice"]


def test_ado_work_item_comment_trigger_supports_current_iteration_query(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.ado_work_item_comment_created.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.query_result = {"project": "APP", "items": [{"id": "101"}]}
    _IssueServiceStub.detail_by_id = {"101": _detail(comments=[])}
    provider = _provider(
        tmp_path,
        current_iteration=True,
        team="Team A",
        types=["Bug"],
    )

    assert provider.poll() == []
    assert _IssueServiceStub.calls[0]["method"] == "current_iteration"
    assert _IssueServiceStub.calls[0]["team"] == "Team A"
    assert _IssueServiceStub.calls[0]["types"] == ["Bug"]
