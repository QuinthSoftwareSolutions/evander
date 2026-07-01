from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.admin.services import (
    PrIntegrationPreflightAdminDependencies,
    PrIntegrationPreflightAdminService,
    VmAgentAdminSupport,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]


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
    _git(repo, "config", "user.name", "Truman Test")
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


def _resolve_repo_source_support(project_dir: Path) -> VmAgentAdminSupport:
    return VmAgentAdminSupport(
        config=SimpleNamespace(),
        runtime=SimpleNamespace(),
        project_dir=project_dir,
        local_issue_store=SimpleNamespace(),
        local_issue_identity_resolver=SimpleNamespace(),
        vm_inventory_admin=SimpleNamespace(),
        workflow_config_admin=SimpleNamespace(),
        directory_admin=SimpleNamespace(),
        get_privileged_helper=lambda: None,
        get_use_privileged_helper=lambda: False,
    )


def _build_service(
    *,
    project_dir: Path,
    repo_source_resolver: Any,
    compute_directory_effective_config: Any,
    provider_config_resolver: Any,
) -> PrIntegrationPreflightAdminService:
    runtime = SimpleNamespace(provider_config=provider_config_resolver)
    return PrIntegrationPreflightAdminService(
        PrIntegrationPreflightAdminDependencies(
            runtime=runtime,
            compute_directory_effective_config=compute_directory_effective_config,
            resolve_repo_source=repo_source_resolver,
        )
    )


def test_run_pr_integration_preflight_clean_single_pr_uses_directory_repo_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    main_commit, feature_commit = _init_repo(repo)
    support = _resolve_repo_source_support(PROJECT_DIR)
    provider_config_resolver = _ProviderConfigResolver(
        SimpleNamespace(
            azure_devops_organization_url="https://dev.azure.com/org",
            azure_devops_project="proj",
            azure_devops_pr_pat_env="ADO_PAT",
            email_provider_timeout_seconds=5.0,
        )
    )
    compute_calls: list[tuple[str, str]] = []

    def compute_directory_effective_config(directory_path: str, domain: str) -> Any:
        compute_calls.append((directory_path, domain))
        return SimpleNamespace(
            ok=True,
            data=SimpleNamespace(
                resolution=SimpleNamespace(
                    effective_config={
                        "ado_repo_source": str(repo),
                    }
                )
            ),
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
                "createdBy": {"displayName": "Agent"},
                "_links": {
                    "web": {"href": "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"}
                },
            },
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("ADO_PAT", "secret")
    service = _build_service(
        project_dir=PROJECT_DIR,
        repo_source_resolver=support.resolve_repo_source,
        compute_directory_effective_config=compute_directory_effective_config,
        provider_config_resolver=provider_config_resolver,
    )

    report = service.run_pr_integration_preflight(
        provider="ado",
        repo="repo-1",
        pr_ids=["17"],
        mode="single",
        directory_path="root/team-a",
    )

    assert compute_calls == [("root/team-a", "ado_worker")]
    assert provider_config_resolver.calls == ["root/team-a"]
    assert len(requests) == 1
    assert report.status == "clean"
    assert report.repo_path == str(repo)
    assert report.participants[0].pr_id == "17"
    assert report.participants[0].source_commit == feature_commit
    assert report.participants[0].target_commit == main_commit
    assert report.participants[0].url == "https://dev.azure.com/org/proj/_git/repo-1/pullrequest/17"
    assert report.checks[0].status == "clean"
    assert report.summary_text is not None
    assert "clean" in report.summary_text
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert not (repo / ".git" / "MERGE_HEAD").exists()


def test_run_pr_integration_preflight_prefers_explicit_repo_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    main_commit, feature_commit = _init_repo(repo)
    support = _resolve_repo_source_support(PROJECT_DIR)
    provider_config_resolver = _ProviderConfigResolver(
        SimpleNamespace(
            azure_devops_organization_url="https://dev.azure.com/org",
            azure_devops_project="proj",
            azure_devops_pr_pat_env="ADO_PAT",
        )
    )

    def compute_directory_effective_config(directory_path: str, domain: str) -> Any:
        raise AssertionError("directory config should not be consulted when repo_source is set")

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
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
        project_dir=PROJECT_DIR,
        repo_source_resolver=support.resolve_repo_source,
        compute_directory_effective_config=compute_directory_effective_config,
        provider_config_resolver=provider_config_resolver,
    )

    report = service.run_pr_integration_preflight(
        provider="azure-devops",
        repo="repo-1",
        pr_ids=["17"],
        mode="single",
        directory_path="root/team-a",
        repo_source=str(repo),
    )

    assert len(requests) == 1
    assert report.status == "clean"
    assert report.repo_path == str(repo)
    assert report.participants[0].source_commit == feature_commit
    assert report.participants[0].target_commit == main_commit


def test_run_pr_integration_preflight_requires_repo_source_when_missing(
    tmp_path: Path,
) -> None:
    provider_config_resolver = _ProviderConfigResolver(
        SimpleNamespace(
            azure_devops_organization_url="https://dev.azure.com/org",
            azure_devops_project="proj",
            azure_devops_pr_pat_env="ADO_PAT",
        )
    )

    def compute_directory_effective_config(directory_path: str, domain: str) -> Any:
        return SimpleNamespace(ok=True, data=SimpleNamespace(resolution=SimpleNamespace(effective_config={})))

    support = _resolve_repo_source_support(PROJECT_DIR)
    service = _build_service(
        project_dir=PROJECT_DIR,
        repo_source_resolver=support.resolve_repo_source,
        compute_directory_effective_config=compute_directory_effective_config,
        provider_config_resolver=provider_config_resolver,
    )

    with pytest.raises(
        ValueError,
        match=r"repo_source or ado_worker\.ado_repo_source",
    ):
        service.run_pr_integration_preflight(
            provider="ado",
            repo="repo-1",
            pr_ids=["17"],
            mode="single",
            directory_path="root/team-a",
        )


def test_run_pr_integration_preflight_reports_provider_error_when_pat_missing(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    support = _resolve_repo_source_support(PROJECT_DIR)
    provider_config_resolver = _ProviderConfigResolver(
        SimpleNamespace(
            azure_devops_organization_url="https://dev.azure.com/org",
            azure_devops_project="proj",
            azure_devops_pr_pat_env="MISSING_PAT",
        )
    )

    def compute_directory_effective_config(directory_path: str, domain: str) -> Any:
        return SimpleNamespace(
            ok=True,
            data=SimpleNamespace(
                resolution=SimpleNamespace(
                    effective_config={"ado_repo_source": str(repo)}
                )
            ),
        )

    service = _build_service(
        project_dir=PROJECT_DIR,
        repo_source_resolver=support.resolve_repo_source,
        compute_directory_effective_config=compute_directory_effective_config,
        provider_config_resolver=provider_config_resolver,
    )

    report = service.run_pr_integration_preflight(
        provider="ado",
        repo="repo-1",
        pr_ids=["17"],
        mode="single",
        directory_path="root",
    )

    assert report.status == "provider_error"
    assert report.participants[0].pr_id == "17"
    assert report.participants[0].status == "provider_error"
    assert report.checks[0].status == "provider_error"
    assert "PAT" in report.errors[0] or "configured" in report.errors[0]


def test_run_pr_integration_preflight_reports_repo_error_when_resolution_fails(
    tmp_path: Path,
) -> None:
    support = _resolve_repo_source_support(PROJECT_DIR)
    provider_config_resolver = _ProviderConfigResolver(
        SimpleNamespace(
            azure_devops_organization_url="https://dev.azure.com/org",
            azure_devops_project="proj",
            azure_devops_pr_pat_env="ADO_PAT",
        )
    )

    def compute_directory_effective_config(directory_path: str, domain: str) -> Any:
        raise AssertionError("repo_source is explicit, so directory config should not be used")

    service = _build_service(
        project_dir=PROJECT_DIR,
        repo_source_resolver=support.resolve_repo_source,
        compute_directory_effective_config=compute_directory_effective_config,
        provider_config_resolver=provider_config_resolver,
    )

    missing_repo = tmp_path / "missing-repo"
    report = service.run_pr_integration_preflight(
        provider="ado",
        repo="repo-1",
        pr_ids=["17"],
        mode="single",
        directory_path="root",
        repo_source=str(missing_repo),
    )

    assert report.status == "repo_error"
    assert report.repo_path == str(missing_repo)
    assert report.participants[0].pr_id == "17"
    assert report.participants[0].status == "repo_error"
    assert report.checks[0].status == "repo_error"
    assert report.errors[0]


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
    )

    assert result["kwargs"]["provider"] == "ado"
    assert result["kwargs"]["repo"] == "repo-1"
    assert result["kwargs"]["pr_ids"] == ["17"]
    assert result["kwargs"]["mode"] == "single"
