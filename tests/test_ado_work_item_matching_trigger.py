from __future__ import annotations

from pathlib import Path

from evander.events.provider_models import AdoWorkItemMatchingTriggerDefinition
from evander.triggers.providers.ado_work_item_matching import (
    AdoWorkItemMatchingTriggerProvider,
)
from evander.triggers.providers.base import TriggerRuntimeContext


class _IssueServiceStub:
    result: dict[str, object] = {}
    calls: list[dict[str, object]] = []

    def __init__(self, resolver: object) -> None:
        del resolver

    def query_remote_backlog_items(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return self.result

    def list_remote_current_iteration_backlog_items(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return self.result


def _provider(tmp_path: Path) -> AdoWorkItemMatchingTriggerProvider:
    trigger = AdoWorkItemMatchingTriggerDefinition(
        id="ado-match",
        display_name="ADO match",
        type="trigger:ado:work-item-matching",
        directory_path="root/team-a",
        states=["Committed"],
        tags_any=["truman"],
    )
    provider = AdoWorkItemMatchingTriggerProvider()
    provider.initialize(
        trigger,
        TriggerRuntimeContext(
            provider_config={"runtime_dir": str(tmp_path)},
            provider_config_resolver=object(),
            metadata={"directory_path": "root"},
        ),
    )
    return provider


def test_ado_work_item_matching_trigger_fires_first_match_then_changed_date(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.ado_work_item_matching.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.result = {
        "project": "Project",
        "items": [
            {
                "id": "101",
                "title": "Fix picker",
                "fields": {"System.ChangedDate": "2026-05-22T10:00:00Z"},
            }
        ],
    }
    provider = _provider(tmp_path)

    events = provider.poll()
    assert len(events) == 1
    assert events[0].payload["work_item_id"] == "101"
    assert events[0].payload["directory_path"] == "root/team-a"
    assert _IssueServiceStub.calls[0]["states"] == ["Committed"]
    assert _IssueServiceStub.calls[0]["tags_any"] == ["truman"]

    provider.acknowledge_published_event(events[0])
    assert provider.poll() == []

    _IssueServiceStub.result = {
        "project": "Project",
        "items": [
            {
                "id": "101",
                "title": "Fix picker",
                "fields": {"System.ChangedDate": "2026-05-22T11:00:00Z"},
            }
        ],
    }
    changed_events = provider.poll()
    assert len(changed_events) == 1
    assert changed_events[0].payload["changed_at"] == "2026-05-22T11:00:00Z"


def test_ado_work_item_matching_trigger_supports_current_iteration_query(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.ado_work_item_matching.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.result = {"project": "Project", "items": []}
    trigger = AdoWorkItemMatchingTriggerDefinition(
        id="ado-match",
        display_name="ADO match",
        type="trigger:ado:work-item-matching",
        current_iteration=True,
        team="Team A",
        types=["Bug"],
    )
    provider = AdoWorkItemMatchingTriggerProvider()
    provider.initialize(
        trigger,
        TriggerRuntimeContext(
            provider_config={"runtime_dir": str(tmp_path)},
            provider_config_resolver=object(),
            metadata={"directory_path": "root/team-a"},
        ),
    )

    assert provider.poll() == []
    assert _IssueServiceStub.calls[0]["team"] == "Team A"
    assert _IssueServiceStub.calls[0]["types"] == ["Bug"]


def test_ado_work_item_matching_trigger_manual_wiql_ignores_filters(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "evander.triggers.providers.ado_work_item_matching.IssueService",
        _IssueServiceStub,
    )
    _IssueServiceStub.calls = []
    _IssueServiceStub.result = {"project": "Project", "items": []}
    trigger = AdoWorkItemMatchingTriggerDefinition(
        id="ado-match",
        display_name="ADO match",
        type="trigger:ado:work-item-matching",
        query_mode="manual_wiql",
        wiql="SELECT [System.Id] FROM WorkItems",
        states=["New"],
        tags_any=["automation"],
    )
    provider = AdoWorkItemMatchingTriggerProvider()
    provider.initialize(
        trigger,
        TriggerRuntimeContext(
            provider_config={"runtime_dir": str(tmp_path)},
            provider_config_resolver=object(),
            metadata={"directory_path": "root/team-a"},
        ),
    )

    assert provider.poll() == []
    assert _IssueServiceStub.calls[0]["wiql"] == "SELECT [System.Id] FROM WorkItems"
    assert _IssueServiceStub.calls[0]["states"] == []
    assert _IssueServiceStub.calls[0]["tags_any"] == []
