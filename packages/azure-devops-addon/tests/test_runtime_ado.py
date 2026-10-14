from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest
from evander.directories import DirectoryService, DirectoryStore
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime
from evander_azure_devops_addon.pull_requests import AzureDevOpsPrReplyProvider, AzureDevOpsPrQueryProvider, AzureDevOpsPrPublishProvider
from evander.services import GitHubPrReplyProvider, GitHubPrPublishProvider

from test_runtime import make_config, write_provider_overlay


def test_runtime_pr_reply_service_for_session_uses_directory_provider_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEAM_GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("TEAM_ADO_PR_PAT", "ado-token")
    config = make_config(tmp_path)
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    write_provider_overlay(
        config,
        team.path,
        {
            "github_token_env": "TEAM_GITHUB_TOKEN",
            "azure_devops_pr_pat_env": "TEAM_ADO_PR_PAT",
            "azure_devops_organization_url": "https://dev.azure.com/team-org",
            "azure_devops_project": "team-project",
            "email_provider_timeout_seconds": 9.0,
        },
    )
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-team",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    pr_reply_service = runtime.pr_reply_service_for_session("session-team")

    github_provider = pr_reply_service.providers["github"]
    azure_provider = pr_reply_service.providers["azure_devops"]
    assert isinstance(github_provider, GitHubPrReplyProvider)
    assert github_provider.token == "gh-token"
    assert isinstance(azure_provider, AzureDevOpsPrReplyProvider)
    assert azure_provider.organization_url == "https://dev.azure.com/team-org"
    assert azure_provider.project == "team-project"
    assert azure_provider.pat == "ado-token"
    assert azure_provider.timeout_seconds == 9.0


def test_runtime_pr_publish_service_for_session_uses_directory_provider_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEAM_GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("TEAM_ADO_PR_PAT", "ado-token")
    config = make_config(tmp_path)
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    write_provider_overlay(
        config,
        team.path,
        {
            "github_token_env": "TEAM_GITHUB_TOKEN",
            "azure_devops_pr_pat_env": "TEAM_ADO_PR_PAT",
            "azure_devops_organization_url": "https://dev.azure.com/team-org",
            "azure_devops_project": "team-project",
            "email_provider_timeout_seconds": 9.0,
        },
    )
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-team",
            allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    pr_publish_service = runtime.pr_publish_service_for_session("session-team")

    github_provider = pr_publish_service.providers["github"]
    azure_provider = pr_publish_service.providers["azure_devops"]
    assert isinstance(github_provider, GitHubPrPublishProvider)
    assert github_provider.token == "gh-token"
    assert isinstance(azure_provider, AzureDevOpsPrPublishProvider)
    assert azure_provider.organization_url == "https://dev.azure.com/team-org"
    assert azure_provider.project == "team-project"
    assert azure_provider.pat == "ado-token"
    assert azure_provider.timeout_seconds == 9.0


def test_runtime_pr_query_service_for_session_uses_directory_provider_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEAM_ADO_PR_PAT", "ado-token")
    config = make_config(tmp_path)
    directories = DirectoryService(DirectoryStore(config.directories_store_path))
    team = directories.create(parent="root", name="team")
    write_provider_overlay(
        config,
        team.path,
        {
            "azure_devops_pr_pat_env": "TEAM_ADO_PR_PAT",
            "azure_devops_organization_url": "https://dev.azure.com/team-org",
            "azure_devops_project": "team-project",
            "email_provider_timeout_seconds": 9.0,
        },
    )
    runtime = build_runtime(config)
    runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-team",
            allowed_tools=[ToolName.LIST_PULL_REQUESTS],
            directory_path=team.path,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    pr_query_service = runtime.pr_query_service_for_session("session-team")

    azure_provider = pr_query_service.providers["azure_devops"]
    assert isinstance(azure_provider, AzureDevOpsPrQueryProvider)
    assert azure_provider.organization_url == "https://dev.azure.com/team-org"
    assert azure_provider.project == "team-project"
    assert azure_provider.pat == "ado-token"
    assert azure_provider.timeout_seconds == 9.0
