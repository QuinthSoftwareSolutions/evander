from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evander.config import GatewayConfig
from evander.events.provider_models import PullRequestGetQueryDefinition
from evander.models import GetPullRequestResponse, PullRequestCommit, PullRequestReviewer
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.pull_request_get import PullRequestGetQueryProvider
from evander.queries.registry import QueryProviderFactory


class _Resolver:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.calls: list[str | None] = []

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        self.calls.append(directory_path)
        return self.config


def _execute(
    query: PullRequestGetQueryDefinition,
    *,
    resolver: _Resolver,
    env: dict[str, str] | None = None,
    resolved_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = PullRequestGetQueryProvider()
    provider.initialize(
        query,
        QueryRuntimeContext(
            provider_config={},
            provider_config_resolver=resolver,
            env=env or {},
            metadata={"directory_path": "root/team-a"},
        ),
    )
    return provider.execute(resolved_config=resolved_config)


def test_pull_request_get_query_builds_through_factory_and_uses_ado_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    class StubProvider:
        def get_pull_request(self, **kwargs: Any) -> GetPullRequestResponse:
            captured["get"] = kwargs
            return GetPullRequestResponse(
                provider="azure_devops",
                repo=kwargs["repo"],
                pr_id=kwargs["pr_id"],
                title="Fix race",
                status="active",
                url="https://dev.azure.test/org/proj/_git/repo-1/pullrequest/17",
                source_branch="refs/heads/feature/fix-race",
                target_branch="refs/heads/main",
                source_commit="abc123",
                target_commit="def456",
                created_by="Agent",
                reviewers=[
                    PullRequestReviewer(
                        reviewer_id="reviewer-1",
                        display_name="Reviewer One",
                        vote=10,
                        status="approved",
                        is_required=True,
                    )
                ],
                commits=[
                    PullRequestCommit(
                        commit_id="abc123",
                        url="https://example.invalid/commit/abc123",
                        comment="Fix race",
                    )
                ],
                raw={"custom_field": "value"},
            )

    def stub_builder(**kwargs: Any) -> tuple[str, str, StubProvider]:
        captured["build"] = kwargs
        return ("azure_devops", "Resolved Project", StubProvider())

    monkeypatch.setattr(
        "evander.queries.providers.pull_request_get.build_pull_request_provider",
        stub_builder,
    )

    factory = QueryProviderFactory(
        provider_config_resolver=_Resolver(
            GatewayConfig(
                runtime_dir=tmp_path,
                azure_devops_organization_url="https://dev.azure.test/org",
                azure_devops_project="Configured Project",
                azure_devops_pr_pat_env="TEAM_ADO_PAT",
            )
        ),
        env={"TEAM_ADO_PAT": "secret"},
    )

    query = PullRequestGetQueryDefinition(
        id="query-pr-get",
        display_name="Get PR",
        type="query:pull-request:get",
        provider="ado",
        repo="repo-1",
        pr_id="17",
        organization_url="https://dev.azure.test/override",
        project="Explicit Project",
        include_reviewers="false",
        include_commits="true",
    )

    provider = factory.build(query)
    output = provider.execute()

    assert captured["build"]["provider"] == "azure_devops"
    assert captured["build"]["directory_path"] == "root"
    assert captured["build"]["organization_url"] == "https://dev.azure.test/override"
    assert captured["build"]["project"] == "Explicit Project"
    assert captured["get"] == {
        "repo": "repo-1",
        "pr_id": "17",
        "include_reviewers": False,
        "include_commits": True,
    }
    assert output["provider"] == "azure_devops"
    assert output["title"] == "Fix race"
    assert output["reviewers"][0]["reviewer_id"] == "reviewer-1"
    assert output["commits"][0]["commit_id"] == "abc123"
    assert output["raw"]["custom_field"] == "value"


def test_pull_request_get_query_resolves_github_token_and_falls_back_without_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    class StubProvider:
        def __init__(
            self,
            *,
            token: str | None = None,
            timeout_seconds: float = 30.0,
        ) -> None:
            captured["token"] = token
            captured["timeout_seconds"] = timeout_seconds

        def get_pull_request(self, **kwargs: Any) -> GetPullRequestResponse:
            captured["get"] = kwargs
            return GetPullRequestResponse(
                provider="github",
                repo=kwargs["repo"],
                pr_id=kwargs["pr_id"],
                title="Fix race",
                status="open",
                url="https://github.test/org/repo/pull/19",
                source_branch="feature/fix-race",
                target_branch="main",
                source_commit="abc123",
                target_commit="def456",
                created_by="agent-user",
                reviewers=[],
                commits=[],
                raw={"note": "payload"},
            )

    monkeypatch.setattr(
        "evander.queries.providers.pull_request_get.GitHubPrQueryProvider",
        StubProvider,
    )
    resolver = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
            github_token_env="TEAM_GITHUB_TOKEN",
            email_provider_timeout_seconds=9.5,
        )
    )

    output = _execute(
        PullRequestGetQueryDefinition(
            id="query-pr-get-github",
            display_name="Get PR",
            type="query:pull-request:get",
            provider="github",
            repo="org/repo",
            pr_id="19",
            include_reviewers="yes",
            include_commits="no",
        ),
        resolver=resolver,
        env={"TEAM_GITHUB_TOKEN": "gh-secret"},
    )

    assert resolver.calls == ["root/team-a"]
    assert captured["token"] == "gh-secret"
    assert captured["timeout_seconds"] == 9.5
    assert captured["get"] == {
        "repo": "org/repo",
        "pr_id": "19",
        "include_reviewers": True,
        "include_commits": False,
    }
    assert output["provider"] == "github"
    assert output["created_by"] == "agent-user"
    assert output["raw"]["note"] == "payload"

    resolver_no_token = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
            github_token_env="",
        )
    )

    output_no_token = _execute(
        PullRequestGetQueryDefinition(
            id="query-pr-get-github-no-token",
            display_name="Get PR",
            type="query:pull-request:get",
            provider="github",
            repo="org/repo",
            pr_id="20",
        ),
        resolver=resolver_no_token,
    )

    assert captured["token"] is None
    assert output_no_token["provider"] == "github"


def test_pull_request_get_query_rejects_invalid_boolean_values(
    tmp_path: Path,
) -> None:
    resolver = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
            github_token_env="TEAM_GITHUB_TOKEN",
        )
    )

    with pytest.raises(ValueError, match=r"query:pull-request:get .*include_commits"):
        _execute(
            PullRequestGetQueryDefinition(
                id="query-pr-get-invalid",
                display_name="Get PR",
                type="query:pull-request:get",
                provider="github",
                repo="org/repo",
                pr_id="21",
                include_commits="maybe",
            ),
            resolver=resolver,
        )
