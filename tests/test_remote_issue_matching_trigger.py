from __future__ import annotations

from pathlib import Path

from evander.events.provider_models import RemoteIssueMatchingTriggerDefinition
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.remote_issue_matching import (
    RemoteIssueMatchingTriggerProvider,
)


class _IssueServiceStub:
    result: dict[str, object] = {}
    calls: list[dict[str, object]] = []

    def __init__(self, resolver: object) -> None:
        del resolver

    def query_remote_issues(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return self.result


def _provider(tmp_path: Path) -> RemoteIssueMatchingTriggerProvider:
    trigger = RemoteIssueMatchingTriggerDefinition(
        id="remote-match",
        display_name="Remote issue match",
        type="trigger:remote-issue-matching",
        provider="github",
        directory_path="root/github",
        repo="owner/repo",
        labels_any=["bug"],
        state="all",
    )
    provider = RemoteIssueMatchingTriggerProvider()
    provider.initialize(
        trigger,
        TriggerRuntimeContext(
            provider_config={"runtime_dir": str(tmp_path)},
            provider_config_resolver=object(),
            metadata={"directory_path": "root"},
        ),
    )
    return provider


def test_remote_issue_matching_trigger_fires_first_match_then_update(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.remote_issue_matching.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.result = {
        "provider": "github",
        "repo": "owner/repo",
        "items": [
            {
                "remote_issue_id": "42",
                "issue_number": "42",
                "title": "Fix picker",
                "updated_at": "2026-06-01T10:00:00Z",
            }
        ],
    }
    provider = _provider(tmp_path)

    events = provider.poll()
    assert len(events) == 1
    assert events[0].payload["provider"] == "github"
    assert events[0].payload["repo"] == "owner/repo"
    assert events[0].payload["remote_issue_id"] == "42"
    assert events[0].payload["issue"]["title"] == "Fix picker"
    assert _IssueServiceStub.calls[0]["labels_any"] == ["bug"]

    provider.acknowledge_published_event(events[0])
    assert provider.poll() == []

    _IssueServiceStub.result = {
        "provider": "github",
        "repo": "owner/repo",
        "items": [
            {
                "remote_issue_id": "42",
                "issue_number": "42",
                "title": "Fix picker",
                "updated_at": "2026-06-01T11:00:00Z",
            }
        ],
    }
    changed_events = provider.poll()
    assert len(changed_events) == 1
    assert changed_events[0].payload["updated_at"] == "2026-06-01T11:00:00Z"


def test_remote_issue_matching_trigger_emits_ado_compat_aliases(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.remote_issue_matching.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.result = {
        "provider": "azure_devops",
        "project": "APP",
        "items": [
            {
                "remote_issue_id": "101",
                "title": "Fix deploy",
                "changed_at": "2026-06-01T10:00:00Z",
            }
        ],
    }
    trigger = RemoteIssueMatchingTriggerDefinition(
        id="remote-ado-match",
        display_name="Remote ADO issue match",
        type="trigger:remote-issue-matching",
        provider="ado",
        project="APP",
    )
    provider = RemoteIssueMatchingTriggerProvider()
    provider.initialize(
        trigger,
        TriggerRuntimeContext(
            provider_config={"runtime_dir": str(tmp_path)},
            provider_config_resolver=object(),
            metadata={"directory_path": "root/team-a"},
        ),
    )

    events = provider.poll()
    assert len(events) == 1
    assert events[0].payload["provider"] == "azure_devops"
    assert events[0].payload["project"] == "APP"
    assert events[0].payload["work_item_id"] == "101"
    assert events[0].payload["changed_at"] == "2026-06-01T10:00:00Z"
    assert events[0].payload["work_item"]["title"] == "Fix deploy"
