from __future__ import annotations
from pathlib import Path
import pytest
from evander.events.provider_models import (
    AdoWorkItemsQueryDefinition,
)
from evander.queries.registry import (
    QueryProviderFactory,
)

from test_query_registry import _ResolverStub, _AdminServiceStub


def test_ado_work_items_query_provider_uses_wiql_and_directory_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = AdoWorkItemsQueryDefinition(
        id="query-ado",
        display_name="ADO work items",
        type="query:ado:work-items",
        directory_path="root/team-a",
        project="example-project",
        wiql="SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'",
        states=["New"],
        top=25,
        skip=5,
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config_resolver: object) -> None:
            captured["provider_config_resolver"] = provider_config_resolver

        def query_remote_backlog_items(self, **kwargs: object) -> dict[str, object]:
            captured["request"] = kwargs
            return {
                "provider": "azure_devops",
                "project": "example-project",
                "items": [{"id": "101", "title": "Fix deploy"}],
                "count": 1,
                "paging": {
                    "skip": 5,
                    "top": 25,
                    "returned_count": 1,
                    "total_matches": 1,
                    "has_more": False,
                    "next_skip": None,
                },
            }

    monkeypatch.setattr("evander_azure_devops_addon.queries.ado_work_items.IssueService", StubIssueService)
    resolver = _ResolverStub()
    factory = QueryProviderFactory(
        provider_config_resolver=resolver,
        admin_service=_AdminServiceStub(vms=[]),
    )
    provider = factory.build(query)
    output = provider.execute()

    assert captured["provider_config_resolver"] is resolver
    assert captured["request"] == {
        "provider": "azure_devops",
        "directory_path": "root/team-a",
        "project": "example-project",
        "wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'",
        "iteration": None,
        "area_path": None,
        "states": [],
        "types": [],
        "tags_any": [],
        "text_search": None,
        "top": 25,
        "skip": 5,
    }
    assert output["items"][0]["id"] == "101"


def test_ado_work_items_query_provider_structured_mode_passes_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = AdoWorkItemsQueryDefinition(
        id="query-ado-structured",
        display_name="ADO work items",
        type="query:ado:work-items",
        query_mode="structured_filters",
        project="example-project",
        states=["New", "Active"],
        types=["Bug"],
        tags_any=["automation"],
        text_search="deploy",
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config_resolver: object) -> None:
            captured["provider_config_resolver"] = provider_config_resolver

        def query_remote_backlog_items(self, **kwargs: object) -> dict[str, object]:
            captured["request"] = kwargs
            return {"provider": "azure_devops", "items": [], "count": 0}

    monkeypatch.setattr("evander_azure_devops_addon.queries.ado_work_items.IssueService", StubIssueService)
    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=_AdminServiceStub(vms=[]),
    )
    provider = factory.build(query)
    provider.execute()

    assert captured["request"] == {
        "provider": "azure_devops",
        "directory_path": "root",
        "project": "example-project",
        "wiql": None,
        "iteration": None,
        "area_path": None,
        "states": ["New", "Active"],
        "types": ["Bug"],
        "tags_any": ["automation"],
        "text_search": "deploy",
        "top": 50,
        "skip": 0,
    }


def test_ado_work_items_query_provider_rejects_structured_mode_wiql() -> None:
    query = AdoWorkItemsQueryDefinition(
        id="query-ado-invalid",
        display_name="ADO work items",
        type="query:ado:work-items",
        query_mode="structured_filters",
        wiql="SELECT [System.Id] FROM WorkItems",
    )
    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=_AdminServiceStub(vms=[]),
    )
    provider = factory.build(query)

    with pytest.raises(ValueError, match="structured_filters cannot use wiql"):
        provider.execute()


def test_ado_work_items_query_provider_rejects_manual_mode_without_wiql() -> None:
    query = AdoWorkItemsQueryDefinition(
        id="query-ado-invalid",
        display_name="ADO work items",
        type="query:ado:work-items",
        query_mode="manual_wiql",
    )
    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=_AdminServiceStub(vms=[]),
    )
    provider = factory.build(query)

    with pytest.raises(ValueError, match="manual_wiql requires wiql"):
        provider.execute()


def test_ado_work_items_query_provider_can_query_current_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = AdoWorkItemsQueryDefinition(
        id="query-ado-current",
        display_name="ADO current iteration",
        type="query:ado:work-items",
        current_iteration=True,
        team="Platform Team",
        states=["Active"],
        types=["Task"],
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config_resolver: object) -> None:
            captured["provider_config_resolver"] = provider_config_resolver

        def list_remote_current_iteration_backlog_items(
            self, **kwargs: object
        ) -> dict[str, object]:
            captured["request"] = kwargs
            return {
                "provider": "azure_devops",
                "project": "example-project",
                "team": "Platform Team",
                "iteration_path": "Project\\Sprint 12",
                "items": [],
                "count": 0,
            }

    monkeypatch.setattr("evander_azure_devops_addon.queries.ado_work_items.IssueService", StubIssueService)
    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=_AdminServiceStub(vms=[]),
    )
    provider = factory.build(query)
    output = provider.execute()

    assert captured["request"] == {
        "provider": "azure_devops",
        "directory_path": "root",
        "project": None,
        "team": "Platform Team",
        "area_path": None,
        "states": ["Active"],
        "types": ["Task"],
        "tags_any": [],
        "text_search": None,
        "top": 50,
        "skip": 0,
    }
    assert output["iteration_path"] == "Project\\Sprint 12"
