from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evander.config import GatewayConfig
from evander.events.provider_models import PullRequestWorkItemsQueryDefinition
from evander.models import AdoIdentitySummary, PullRequestWorkItem, PullRequestWorkItemsResponse
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.pull_request_work_items import PullRequestWorkItemsQueryProvider
from evander.queries.registry import QueryProviderFactory


class _Resolver:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.calls: list[str | None] = []

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        self.calls.append(directory_path)
        return self.config


def _execute(
    query: PullRequestWorkItemsQueryDefinition,
    *,
    resolver: _Resolver,
    resolved_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = PullRequestWorkItemsQueryProvider()
    provider.initialize(
        query,
        QueryRuntimeContext(
            provider_config={},
            provider_config_resolver=resolver,
            metadata={"directory_path": "root/team-a"},
        ),
    )
    return provider.execute(resolved_config=resolved_config)


def test_pull_request_work_items_query_builds_through_factory_and_uses_ado_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    class StubProvider:
        def get_pull_request_work_items(self, **kwargs: Any) -> PullRequestWorkItemsResponse:
            captured["get_work_items"] = kwargs
            return PullRequestWorkItemsResponse(
                provider="azure_devops",
                repo=kwargs["repo"],
                pr_id=kwargs["pr_id"],
                work_items=[
                    PullRequestWorkItem(
                        work_item_id="123",
                        title="Fix race",
                        assignee=AdoIdentitySummary(
                            display_name="Ada Lovelace",
                            unique_name="ada@example.com",
                            email="ada@example.com",
                        ),
                    )
                ],
                count=1,
            )

    def stub_builder(**kwargs: Any) -> tuple[str, str, StubProvider]:
        captured["build"] = kwargs
        return ("azure_devops", "Resolved Project", StubProvider())

    monkeypatch.setattr(
        "evander.queries.providers.pull_request_work_items.build_pull_request_provider",
        stub_builder,
    )
    factory = QueryProviderFactory(
        provider_config_resolver=_Resolver(
            GatewayConfig(
                runtime_dir=tmp_path,
                azure_devops_organization_url="https://dev.azure.com/configured-org",
                azure_devops_project="Configured Project",
                azure_devops_pr_pat_env="TEAM_ADO_PAT",
            )
        ),
        env={"TEAM_ADO_PAT": "secret"},
    )
    query = PullRequestWorkItemsQueryDefinition(
        id="query-pr-work-items",
        display_name="Get PR work items",
        type="query:pull-request:work-items",
        provider="ado",
        repo="repo-1",
        pr_id="17",
        organization_url="https://dev.azure.com/override-org",
        project="Explicit Project",
    )

    output = factory.build(query).execute()

    assert captured["build"]["provider"] == "azure_devops"
    assert captured["build"]["directory_path"] == "root"
    assert captured["build"]["ado_organization"] == "https://dev.azure.com/override-org"
    assert captured["build"]["project"] == "Explicit Project"
    assert captured["build"]["node_type"] == "query:pull-request:work-items"
    assert captured["get_work_items"] == {"repo": "repo-1", "pr_id": "17"}
    assert output["count"] == 1
    assert output["work_items"][0]["assignee"]["email"] == "ada@example.com"


def test_pull_request_work_items_query_rejects_non_ado_provider(tmp_path: Path) -> None:
    resolver = _Resolver(GatewayConfig(runtime_dir=tmp_path))

    with pytest.raises(ValueError, match=r"query:pull-request:work-items provider 'github'"):
        _execute(
            PullRequestWorkItemsQueryDefinition(
                id="query-pr-work-items",
                display_name="Get PR work items",
                type="query:pull-request:work-items",
                provider="github",
                repo="org/repo",
                pr_id="17",
            ),
            resolver=resolver,
        )
