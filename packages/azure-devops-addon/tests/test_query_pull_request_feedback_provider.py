from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from evander.config import GatewayConfig
from evander.events.provider_models import PullRequestFeedbackQueryDefinition
from evander.optional_integrations import OptionalIntegrationUnavailable
from evander.pr_feedback import FeedbackEnvelope, ProviderPollResult
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.pull_request_feedback import (
    PullRequestFeedbackQueryProvider,
)


class _Resolver:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.calls: list[str | None] = []

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        self.calls.append(directory_path)
        return self.config


def _execute(
    query: PullRequestFeedbackQueryDefinition,
    *,
    resolver: _Resolver,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    provider = PullRequestFeedbackQueryProvider()
    provider.initialize(
        query,
        QueryRuntimeContext(
            provider_config={},
            provider_config_resolver=resolver,
            env=env or {},
            metadata={"directory_path": "root/team-a"},
        ),
    )
    return provider.execute()


@pytest.mark.parametrize("provider_name", ["ado", "azure-devops", "azure_devops"])
@pytest.mark.parametrize("directory_path", [None, "root/team-a/project"])
def test_disabled_ado_feedback_rejects_before_reading_credentials_or_making_requests(
    tmp_path: Path, monkeypatch, provider_name, directory_path,
) -> None:
    secret_reads = []
    requests = []
    client_type = httpx.Client

    def read_secret(context, name):
        secret_reads.append(name)
        return "fixture-pat"

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"value": []})

    monkeypatch.setattr(QueryRuntimeContext, "resolve_secret", read_secret)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs)
    )
    resolver = _Resolver(GatewayConfig(
        runtime_dir=tmp_path,
        addon_loader={"enabled_addon_ids": []},
        ado_organization="example",
        azure_devops_project="Project",
        azure_devops_pr_pat_env="FIXTURE_ADO_PAT",
    ))
    query = PullRequestFeedbackQueryDefinition(
        id="disabled-feedback", display_name="Feedback", type="query:pull-request:feedback",
        provider=provider_name, repo="repository", pr_id="17", directory_path=directory_path,
    )
    with pytest.raises(OptionalIntegrationUnavailable, match="enable 'azure-devops'"):
        _execute(query, resolver=resolver)
    assert resolver.calls == [directory_path or "root/team-a"]
    assert secret_reads == []
    assert requests == []


def test_pull_request_feedback_query_fetches_ado_feedback_with_cursor(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    class StubAdapter:
        def __init__(
            self,
            *,
            organization_url: str,
            project: str,
            pat: str | None,
            pat_env_name: str,
        ) -> None:
            captured["init"] = {
                "organization_url": organization_url,
                "project": project,
                "pat": pat,
                "pat_env_name": pat_env_name,
            }

        def poll(self, **kwargs: Any) -> ProviderPollResult:
            captured["poll"] = kwargs
            return ProviderPollResult(
                feedback=[
                    FeedbackEnvelope(
                        feedback_id="azure_devops:thread_comment:10:1",
                        provider="azure_devops",
                        repo="org/project/repo",
                        pr_id="17",
                        thread_id="10",
                        comment_id="1",
                        author="Reviewer",
                        body="Please add a regression test.",
                        created_at=datetime(2026, 5, 22, 9, 0, tzinfo=UTC),
                    )
                ],
                next_cursor="2026-05-22T09:00:00Z",
            )

    monkeypatch.setattr(
        "evander.queries.providers.pull_request_feedback.AzureDevOpsFeedbackAdapter",
        StubAdapter,
    )
    resolver = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
            addon_loader={"enabled_addon_ids": ["azure-devops"]},
            azure_devops_organization_url="https://dev.azure.com/example",
            azure_devops_project="Default Project",
            azure_devops_pr_pat_env="TEAM_ADO_PAT",
        )
    )

    output = _execute(
        PullRequestFeedbackQueryDefinition(
            id="query-feedback",
            display_name="Feedback",
            type="query:pull-request:feedback",
            provider="ado",
            repo="org/project/repo",
            pr_id="17",
            cursor="2026-05-22T08:00:00Z",
        ),
        resolver=resolver,
        env={"TEAM_ADO_PAT": "secret"},
    )

    assert captured["init"] == {
        "organization_url": "https://dev.azure.com/example",
        "project": "Default Project",
        "pat": "secret",
        "pat_env_name": "TEAM_ADO_PAT",
    }
    assert captured["poll"]["repo"] == "org/project/repo"
    assert captured["poll"]["pr_id"] == "17"
    assert captured["poll"]["cursor"] == "2026-05-22T08:00:00Z"
    assert output["provider"] == "azure_devops"
    assert output["project"] == "Default Project"
    assert output["count"] == 1
    assert output["feedback"][0]["feedback_id"] == "azure_devops:thread_comment:10:1"
    assert output["next_cursor"] == "2026-05-22T09:00:00Z"


def test_pull_request_feedback_query_uses_explicit_ado_project(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class StubAdapter:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def poll(self, **kwargs: Any) -> ProviderPollResult:
            return ProviderPollResult(feedback=[], next_cursor=kwargs["cursor"])

    monkeypatch.setattr(
        "evander.queries.providers.pull_request_feedback.AzureDevOpsFeedbackAdapter",
        StubAdapter,
    )
    resolver = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
            azure_devops_organization_url="https://dev.azure.com/example",
            azure_devops_project="Default Project",
            azure_devops_pr_pat_env="TEAM_ADO_PAT",
        )
    )

    output = _execute(
        PullRequestFeedbackQueryDefinition(
            id="query-feedback-explicit",
            display_name="Feedback",
            type="query:pull-request:feedback",
            provider="azure_devops",
            repo="repo-only",
            pr_id="18",
            project="Explicit Project",
            cursor="2026-05-22T08:00:00Z",
        ),
        resolver=resolver,
        env={"TEAM_ADO_PAT": "secret"},
    )

    assert captured["init"]["project"] == "Explicit Project"
    assert output["project"] == "Explicit Project"
    assert output["next_cursor"] == "2026-05-22T08:00:00Z"


def test_pull_request_feedback_query_rejects_an_arbitrary_organization_before_polling(
    tmp_path: Path, monkeypatch
) -> None:
    called = False

    class StubAdapter:
        def __init__(self, **kwargs: Any) -> None:
            nonlocal called
            called = True

    monkeypatch.setattr(
        "evander.queries.providers.pull_request_feedback.AzureDevOpsFeedbackAdapter",
        StubAdapter,
    )
    resolver = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
            ado_organization="directory-org",
            azure_devops_project="Default Project",
            azure_devops_pr_pat_env="TEAM_ADO_PAT",
        )
    )

    query = PullRequestFeedbackQueryDefinition(
        id="query-feedback-untrusted-org",
        display_name="Feedback",
        type="query:pull-request:feedback",
        provider="azure_devops",
        repo="repo-only",
        pr_id="18",
        ado_organization="https://attacker.example/organization",
    )

    with pytest.raises(ValueError, match="dev.azure.com or \\*\\.visualstudio.com"):
        _execute(query, resolver=resolver, env={"TEAM_ADO_PAT": "secret"})

    assert called is False


def test_pull_request_feedback_query_prefers_configured_project_for_ado_clone_url(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    class StubAdapter:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def poll(self, **kwargs: Any) -> ProviderPollResult:
            return ProviderPollResult(feedback=[], next_cursor=None)

    monkeypatch.setattr(
        "evander.queries.providers.pull_request_feedback.AzureDevOpsFeedbackAdapter",
        StubAdapter,
    )
    resolver = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
            azure_devops_organization_url="https://dev.azure.com/example",
            azure_devops_project="Configured Project",
            azure_devops_pr_pat_env="TEAM_ADO_PAT",
        )
    )

    output = _execute(
        PullRequestFeedbackQueryDefinition(
            id="query-feedback-clone-url",
            display_name="Feedback",
            type="query:pull-request:feedback",
            provider="azure_devops",
            repo="https://dev.azure.com/org/Project/_git/Repo",
            pr_id="18",
        ),
        resolver=resolver,
        env={"TEAM_ADO_PAT": "secret"},
    )

    assert captured["init"]["project"] == "Configured Project"
    assert output["project"] == "Configured Project"


def test_pull_request_feedback_query_infers_ado_project_from_shorthand_without_config(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    class StubAdapter:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def poll(self, **kwargs: Any) -> ProviderPollResult:
            return ProviderPollResult(feedback=[], next_cursor=None)

    monkeypatch.setattr(
        "evander.queries.providers.pull_request_feedback.AzureDevOpsFeedbackAdapter",
        StubAdapter,
    )
    resolver = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
            azure_devops_organization_url="https://dev.azure.com/example",
            azure_devops_project="",
            azure_devops_pr_pat_env="TEAM_ADO_PAT",
        )
    )

    output = _execute(
        PullRequestFeedbackQueryDefinition(
            id="query-feedback-shorthand",
            display_name="Feedback",
            type="query:pull-request:feedback",
            provider="azure_devops",
            repo="org/Shorthand Project/Repo",
            pr_id="18",
        ),
        resolver=resolver,
        env={"TEAM_ADO_PAT": "secret"},
    )

    assert captured["init"]["project"] == "Shorthand Project"
    assert output["project"] == "Shorthand Project"


def test_pull_request_feedback_query_resolves_github_token(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class StubAdapter:
        def __init__(self, *, token: str | None = None) -> None:
            captured["token"] = token

        def poll(self, **kwargs: Any) -> ProviderPollResult:
            captured["poll"] = kwargs
            return ProviderPollResult(feedback=[], next_cursor=None)

    monkeypatch.setattr(
        "evander.queries.providers.pull_request_feedback.GitHubFeedbackAdapter",
        StubAdapter,
    )
    resolver = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
            addon_loader={"enabled_addon_ids": []},
            github_token_env="TEAM_GITHUB_TOKEN",
        )
    )

    output = _execute(
        PullRequestFeedbackQueryDefinition(
            id="query-github-feedback",
            display_name="Feedback",
            type="query:pull-request:feedback",
            provider="github",
            repo="org/repo",
            pr_id="19",
        ),
        resolver=resolver,
        env={"TEAM_GITHUB_TOKEN": "gh-secret"},
    )

    assert resolver.calls == ["root/team-a"]
    assert captured["token"] == "gh-secret"
    assert captured["poll"]["repo"] == "org/repo"
    assert output["provider"] == "github"
    assert output["count"] == 0
