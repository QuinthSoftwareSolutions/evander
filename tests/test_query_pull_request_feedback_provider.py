from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.provider_models import PullRequestFeedbackQueryDefinition
from agent_sandbox.pr_feedback import FeedbackEnvelope, ProviderPollResult
from agent_sandbox.queries.providers.base import QueryRuntimeContext
from agent_sandbox.queries.providers.pull_request_feedback import (
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
        "agent_sandbox.queries.providers.pull_request_feedback.AzureDevOpsFeedbackAdapter",
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
        "project": "project",
        "pat": "secret",
        "pat_env_name": "TEAM_ADO_PAT",
    }
    assert captured["poll"]["repo"] == "org/project/repo"
    assert captured["poll"]["pr_id"] == "17"
    assert captured["poll"]["cursor"] == "2026-05-22T08:00:00Z"
    assert output["provider"] == "azure_devops"
    assert output["project"] == "project"
    assert output["count"] == 1
    assert output["feedback"][0]["feedback_id"] == "azure_devops:thread_comment:10:1"
    assert output["next_cursor"] == "2026-05-22T09:00:00Z"


def test_pull_request_feedback_query_uses_explicit_ado_project(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    class StubAdapter:
        def __init__(self, **kwargs: Any) -> None:
            captured["init"] = kwargs

        def poll(self, **kwargs: Any) -> ProviderPollResult:
            return ProviderPollResult(feedback=[], next_cursor=kwargs["cursor"])

    monkeypatch.setattr(
        "agent_sandbox.queries.providers.pull_request_feedback.AzureDevOpsFeedbackAdapter",
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


def test_pull_request_feedback_query_resolves_github_token(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class StubAdapter:
        def __init__(self, *, token: str | None = None) -> None:
            captured["token"] = token

        def poll(self, **kwargs: Any) -> ProviderPollResult:
            captured["poll"] = kwargs
            return ProviderPollResult(feedback=[], next_cursor=None)

    monkeypatch.setattr(
        "agent_sandbox.queries.providers.pull_request_feedback.GitHubFeedbackAdapter",
        StubAdapter,
    )
    resolver = _Resolver(
        GatewayConfig(
            runtime_dir=tmp_path,
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
