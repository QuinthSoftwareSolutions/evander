from __future__ import annotations

from contextlib import contextmanager
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.services import (
    PrIntegrationPreflightAdminDependencies,
    PrIntegrationPreflightAdminService,
)
from evander.pr_merge_preflight import (
    MergePreflightCheck,
    MergePreflightReport,
)


MANAGED_REPO_ID = "mr_0123456789abcdefabcd"


def patch_httpx_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.BaseTransport) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "evander.services.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(repo: Path) -> tuple[str, str]:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Evander Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    main_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "-b", "feature/review", "main")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "feature")
    feature_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "main")
    return main_commit, feature_commit


class _ProviderConfigResolver:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.calls: list[str | None] = []

    def resolve_for_directory(self, directory_path: str | None) -> Any:
        self.calls.append(directory_path)
        return self.config


class _ManagedGitStub:
    def __init__(self, worktree: Path, *, lookup_error: Exception | None = None) -> None:
        self.worktree = worktree
        self.lookup_error = lookup_error
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def get_repository(self, managed_repo_id: str, directory_path: str) -> dict[str, object]:
        self.calls.append(("get", managed_repo_id, {"directory_path": directory_path}))
        if self.lookup_error is not None:
            raise self.lookup_error
        return {"managed_repo_id": managed_repo_id, "default_branch": "main"}

    def resolve_internal_repository(
        self,
        managed_repo_id: str,
        directory_path: str,
    ) -> tuple[Path, Path]:
        self.calls.append(("resolve", managed_repo_id, {"directory_path": directory_path}))
        return self.worktree / ".git", self.worktree.parent

    def refresh_repository(self, managed_repo_id: str) -> dict[str, object]:
        self.calls.append(("refresh", managed_repo_id, {}))
        return {"managed_repo_id": managed_repo_id}

    @contextmanager
    def temporary_worktree(self, managed_repo_id: str, **kwargs: object):
        self.calls.append(("worktree", managed_repo_id, dict(kwargs)))
        yield self.worktree


def _build_service(
    *,
    managed_git: _ManagedGitStub,
    provider_config_resolver: _ProviderConfigResolver,
) -> PrIntegrationPreflightAdminService:
    return PrIntegrationPreflightAdminService(
        PrIntegrationPreflightAdminDependencies(
            runtime=SimpleNamespace(provider_config=provider_config_resolver),
            managed_git_admin=managed_git,
        )
    )


def test_run_pr_integration_preflight_uses_temporary_managed_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    main_commit, feature_commit = _init_repo(repo)
    managed_git = _ManagedGitStub(repo)
    provider_config_resolver = _ProviderConfigResolver(
        SimpleNamespace(
            azure_devops_organization_url="https://dev.azure.com/org",
            azure_devops_project="proj",
            azure_devops_pr_pat_env="ADO_PAT",
            email_provider_timeout_seconds=5.0,
        )
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith("/_apis/git/repositories/repo-1/pullRequests/17")
        assert request.url.params["api-version"] == "7.1"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "pullRequestId": 17,
                "title": "Fix race",
                "status": "active",
                "sourceRefName": "refs/heads/feature/review",
                "targetRefName": "refs/heads/main",
                "lastMergeSourceCommit": {"commitId": feature_commit},
                "lastMergeTargetCommit": {"commitId": main_commit},
                "_links": {
                    "web": {"href": "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"}
                },
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("ADO_PAT", "secret")
    service = _build_service(
        managed_git=managed_git,
        provider_config_resolver=provider_config_resolver,
    )

    report = service.run_pr_integration_preflight(
        provider="ado",
        repo="repo-1",
        pr_ids=["17"],
        mode="single",
        directory_path="root/team-a",
        managed_repo_id=MANAGED_REPO_ID,
    )

    assert provider_config_resolver.calls == ["root/team-a"]
    assert len(requests) == 1
    assert report.status == "clean"
    assert report.repo_path == MANAGED_REPO_ID
    assert report.participants[0].source_commit == feature_commit
    assert report.participants[0].target_commit == main_commit
    assert report.checks[0].status == "clean"
    assert [call[0] for call in managed_git.calls] == [
        "get",
        "resolve",
        "refresh",
        "worktree",
    ]
    worktree_call = managed_git.calls[-1]
    assert worktree_call[2]["commit_sha"] == main_commit
    assert worktree_call[2]["purpose"] == "pr-integration-preflight"
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_run_pr_integration_preflight_reports_provider_error_when_pat_missing(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    managed_git = _ManagedGitStub(repo)
    provider_config_resolver = _ProviderConfigResolver(
        SimpleNamespace(
            azure_devops_organization_url="https://dev.azure.com/org",
            azure_devops_project="proj",
            azure_devops_pr_pat_env="MISSING_PAT",
        )
    )
    service = _build_service(
        managed_git=managed_git,
        provider_config_resolver=provider_config_resolver,
    )

    report = service.run_pr_integration_preflight(
        provider="ado",
        repo="repo-1",
        pr_ids=["17"],
        mode="single",
        directory_path="root",
        managed_repo_id=MANAGED_REPO_ID,
    )

    assert report.status == "provider_error"
    assert report.repo_path == MANAGED_REPO_ID
    assert report.participants[0].status == "provider_error"
    assert "PAT" in report.errors[0] or "configured" in report.errors[0]


def test_run_pr_integration_preflight_reports_managed_repo_lookup_error(
    tmp_path: Path,
) -> None:
    managed_git = _ManagedGitStub(
        tmp_path / "unused",
        lookup_error=LookupError("managed repository is not visible in this directory"),
    )
    provider_config_resolver = _ProviderConfigResolver(SimpleNamespace())
    service = _build_service(
        managed_git=managed_git,
        provider_config_resolver=provider_config_resolver,
    )

    report = service.run_pr_integration_preflight(
        provider="ado",
        repo="repo-1",
        pr_ids=["17"],
        mode="single",
        directory_path="root/team-b",
        managed_repo_id=MANAGED_REPO_ID,
    )

    assert report.status == "repo_error"
    assert report.repo_path == MANAGED_REPO_ID
    assert "not visible" in report.errors[0]
    assert provider_config_resolver.calls == []


def test_admin_service_delegates_run_pr_integration_preflight() -> None:
    admin = object.__new__(AdminRuntimeComponents)
    admin.pr_integration_preflight_admin = SimpleNamespace(
        run_pr_integration_preflight=lambda **kwargs: {"kwargs": kwargs}
    )

    result = admin.pr_integration_preflight_admin.run_pr_integration_preflight(
        provider="ado",
        repo="repo-1",
        pr_ids=["17"],
        mode="single",
        directory_path="root",
        managed_repo_id=MANAGED_REPO_ID,
    )

    assert result["kwargs"]["managed_repo_id"] == MANAGED_REPO_ID
    assert result["kwargs"]["provider"] == "ado"
    assert result["kwargs"]["pr_ids"] == ["17"]


def test_managed_preflight_report_scrubs_temporary_worktree_paths(tmp_path: Path) -> None:
    checkout = tmp_path / "runtime" / "git-worktrees" / MANAGED_REPO_ID / "op-1" / "checkout"
    report = MergePreflightReport(
        repo_path=str(checkout),
        mode="single",
        status="repo_error",
        checks=[
            MergePreflightCheck(
                index=1,
                kind="single",
                status="repo_error",
                explanation=f"git failed in {checkout}",
            )
        ],
        warnings=[f"operation directory: {checkout.parent}"],
        errors=[f"checkout directory: {checkout}"],
        summary_text=f"Could not inspect {checkout}",
    )

    sanitized = PrIntegrationPreflightAdminService._sanitize_managed_worktree_report(
        report,
        checkout,
    )
    payload = sanitized.model_dump_json()

    assert str(checkout) not in payload
    assert str(checkout.parent) not in payload
    assert "<managed-worktree>" in payload
