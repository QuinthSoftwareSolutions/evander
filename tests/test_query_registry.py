from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.admin.workflow_catalog import _WORKFLOW_EDITOR_NODE_CATALOG
from evander.config import GatewayConfig
from evander.events.models import materialize_node_definition
from evander.events.provider_models import (
    AgentArtifactTextQueryDefinition,
    GitMergeBaseQueryDefinition,
    GitPrNoteQueryDefinition,
    IssueGetQueryDefinition,
    IssueListQueryDefinition,
    ModelUsageListQueryDefinition,
    PullRequestGetQueryDefinition,
    PullRequestListQueryDefinition,
    PullRequestWorkItemsQueryDefinition,
    RemoteIssuesQueryDefinition,
    SessionContextQueryDefinition,
    VmRunStateQueryDefinition,
)
from evander.models import ListPullRequestsResponse, PullRequestItem, VmSummary
from evander.queries.providers.base import QueryProviderBase
from evander.queries.registry import (
    DEFAULT_QUERY_PROVIDER_REGISTRY,
    QueryProviderFactory,
    QueryProviderRegistry,
)
from evander.workflow.contracts import WorkflowNodeEnvelope


class _ResolverStub:
    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        return GatewayConfig(
            runtime_dir=(GatewayConfig().runtime_dir / (directory_path or "root")),
            ado_organization="directory-org",
        )


@dataclass
class _AdminServiceStub:
    vms: list[VmSummary]
    pr_note_result: dict[str, object] | None = None
    pr_note_calls: list[dict[str, object]] | None = None
    merge_base_result: dict[str, object] | None = None
    merge_base_calls: list[dict[str, object]] | None = None
    issue_result: dict[str, object] | None = None
    issue_calls: list[dict[str, object]] | None = None
    issue_list_result: list[dict[str, object]] | None = None
    issue_list_calls: list[dict[str, object]] | None = None
    artifact_result: dict[str, object] | None = None
    artifact_calls: list[dict[str, object]] | None = None

    def list_vms(self, *, include_inactive: bool = True, omit_state: bool = False):
        del include_inactive, omit_state
        return list(self.vms)

    def git_pr_note_query(self, **kwargs):
        if self.pr_note_calls is not None:
            self.pr_note_calls.append(kwargs)
        if self.pr_note_result is None:
            raise RuntimeError("pr_note_result not configured")
        return dict(self.pr_note_result)

    def resolve_managed_git_merge_base(self, **kwargs):
        if self.merge_base_calls is not None:
            self.merge_base_calls.append(kwargs)
        if self.merge_base_result is None:
            raise RuntimeError("merge_base_result not configured")
        return dict(self.merge_base_result)

    def get_issue(self, **kwargs):
        if self.issue_calls is not None:
            self.issue_calls.append(kwargs)
        if self.issue_result is None:
            raise RuntimeError("issue_result not configured")
        return dict(self.issue_result)

    def list_issues(self, **kwargs):
        if self.issue_list_calls is not None:
            self.issue_list_calls.append(kwargs)
        if self.issue_list_result is None:
            raise RuntimeError("issue_list_result not configured")
        return [dict(issue) for issue in self.issue_list_result]

    def read_session_artifact_text(self, **kwargs):
        if self.artifact_calls is not None:
            self.artifact_calls.append(kwargs)
        if self.artifact_result is None:
            raise RuntimeError("artifact_result not configured")
        return dict(self.artifact_result)


class _RecordingProvider(QueryProviderBase):
    pass


def test_default_registry_includes_known_query_types() -> None:
    assert set(DEFAULT_QUERY_PROVIDER_REGISTRY) == {
        "query:ado:builds",
        "query:text:regex",
        "query:vm:run-state",
        "query:artifact-space:list",
        "query:artifact-space:latest",
        "query:git:merge-base",
        "query:git:pr-note",
        "query:session-context",
        "query:website-session:resolve-profile",
        "query:session:list",
        "query:model-usage:list",
        "query:codex:subscription-quota",
        "query:security:scan",
        "query:agent:artifact-text",
        "query:issue:get",
        "query:issue:list",
        "query:ado:work-items",
        "query:remote-issues",
        "query:classification:evaluate",
        "query:pull-request:list",
        "query:pull-request:feedback",
        "query:communication:messages",
        "query:pull-request:get",
        "query:pull-request:work-items",
        "query:semantic:store-status",
        "query:semantic:search",
        "query:semantic:get",
        "query:code-index:status",
        "query:code-index:search",
        "query:code-index:navigate",
        "query:state:get",
    }


def test_pull_request_get_query_definition_materializes_and_normalizes_provider() -> None:
    node = WorkflowNodeEnvelope(
        id="query-pr-get-1",
        display_name="PR get",
        type="query:pull-request:get",
        config={
            "provider": "ado",
            "repo": "repo-1",
            "pr_id": "17",
        },
    )

    materialized = materialize_node_definition(node)

    assert isinstance(materialized, PullRequestGetQueryDefinition)
    assert materialized.provider == "azure_devops"
    assert materialized.repo == "repo-1"
    assert materialized.pr_id == "17"


def test_git_merge_base_query_definition_materializes() -> None:
    node = WorkflowNodeEnvelope(
        id="query-merge-base",
        display_name="Git merge base",
        type="query:git:merge-base",
        config={
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "target_branch": "main",
            "candidate_commit_sha": "a" * 40,
            "directory_path": "root/team-a",
        },
    )

    materialized = materialize_node_definition(node)

    assert isinstance(materialized, GitMergeBaseQueryDefinition)
    assert materialized.target_branch == "main"
    assert materialized.candidate_commit_sha == "a" * 40


def test_git_merge_base_query_has_editor_catalog_metadata() -> None:
    entry = next(
        item for item in _WORKFLOW_EDITOR_NODE_CATALOG if item.type == "query:git:merge-base"
    )

    assert entry.label == "Git merge base"
    assert [field.key for field in entry.fields] == [
        "managed_repo_id",
        "target_branch",
        "candidate_commit_sha",
        "directory_path",
    ]


def test_pull_request_work_items_query_definition_materializes_and_normalizes_provider() -> None:
    node = WorkflowNodeEnvelope(
        id="query-pr-work-items-1",
        display_name="PR work items",
        type="query:pull-request:work-items",
        config={
            "provider": "ado",
            "repo": "repo-1",
            "pr_id": "17",
        },
    )

    materialized = materialize_node_definition(node)

    assert isinstance(materialized, PullRequestWorkItemsQueryDefinition)
    assert materialized.provider == "azure_devops"
    assert materialized.repo == "repo-1"
    assert materialized.pr_id == "17"


def test_model_usage_list_query_definition_materializes() -> None:
    node = WorkflowNodeEnvelope(
        id="query-model-usage-1",
        display_name="Model usage",
        type="query:model-usage:list",
        config={
            "session_id": "session-1",
            "provider": "openai",
            "include_costs": True,
            "cost_currency": "usd",
        },
    )

    materialized = materialize_node_definition(node)

    assert isinstance(materialized, ModelUsageListQueryDefinition)
    assert materialized.session_id == "session-1"
    assert materialized.provider == "openai"
    assert materialized.include_costs is True
    assert materialized.cost_currency == "usd"


def test_factory_initializes_query_provider_with_runtime_context() -> None:
    query = VmRunStateQueryDefinition(
        id="query-1",
        display_name="VM run state",
        type="query:vm:run-state",
        vm_name="vm-1",
    )
    registry = QueryProviderRegistry()
    registry.register("query:vm:run-state", _RecordingProvider)
    admin_service = _AdminServiceStub(vms=[])

    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(),
        registry=registry,
        admin_service=admin_service,
        env={"API_TOKEN": "top-secret"},
    )
    provider = factory.build(query, directory_path="root/team-a", graph_id="graph-1")

    assert isinstance(provider, _RecordingProvider)
    assert provider.runtime_context is not None
    assert provider.runtime_context.resolve_secret("API_TOKEN") == "top-secret"
    assert provider.runtime_context.metadata["directory_path"] == "root/team-a"
    assert provider.runtime_context.metadata["graph_id"] == "graph-1"


def test_vm_run_state_query_provider_returns_expected_state_code() -> None:
    query = VmRunStateQueryDefinition(
        id="query-state",
        display_name="VM state",
        type="query:vm:run-state",
        session_id="session-1",
    )
    admin_service = _AdminServiceStub(
        vms=[
            VmSummary(
                vm_name="vm-1",
                session_ids=["session-1"],
                libvirt_state="running",
                agent_state="omitted",
            )
        ]
    )

    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )
    provider = factory.build(query)
    output = provider.execute(resolved_config={"session_id": "session-1"})

    assert output["state_code"] == "running"
    assert output["is_running"] is True


def test_git_pr_note_query_provider_returns_note_body(tmp_path: Path) -> None:
    query = GitPrNoteQueryDefinition(
        id="query-pr-note",
        display_name="PR note",
        type="query:git:pr-note",
        managed_repo_id="mr_0123456789abcdefabcd",
        commit_sha="a" * 40,
        notes_ref="refs/notes/agent-pr/session-1",
    )
    admin_service = _AdminServiceStub(
        vms=[],
        pr_note_result={
            "body": "Agent note body",
            "has_note": True,
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "notes_ref": "refs/notes/agent-pr/session-1",
            "commit_sha": "a" * 40,
        },
        pr_note_calls=[],
    )
    admin_service.managed_git_admin = SimpleNamespace(  # type: ignore[attr-defined]
        read_pr_note=admin_service.git_pr_note_query
    )

    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )
    provider = factory.build(query)
    output = provider.execute()

    assert admin_service.pr_note_calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "commit_sha": "a" * 40,
            "notes_ref": "refs/notes/agent-pr/session-1",
            "directory_path": "root",
        }
    ]
    assert output["has_note"] is True
    assert output["body"] == "Agent note body"


def test_git_merge_base_query_provider_returns_pinned_target_and_merge_base() -> None:
    target_commit = "b" * 40
    candidate_commit = "c" * 40
    merge_base_commit = "a" * 40
    query = GitMergeBaseQueryDefinition(
        id="query-merge-base",
        display_name="Git merge base",
        type="query:git:merge-base",
        managed_repo_id="mr_0123456789abcdefabcd",
        target_branch="main",
        candidate_commit_sha=candidate_commit,
        directory_path="root/team-a",
    )
    admin_service = _AdminServiceStub(
        vms=[],
        merge_base_result={
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "directory_path": "root/team-a",
            "target_branch": "main",
            "target_commit_sha": target_commit,
            "candidate_commit_sha": candidate_commit,
            "merge_base_commit_sha": merge_base_commit,
        },
        merge_base_calls=[],
    )
    admin_service.managed_git_admin = SimpleNamespace(  # type: ignore[attr-defined]
        resolve_merge_base=admin_service.resolve_managed_git_merge_base
    )

    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )
    output = factory.build(query).execute()

    assert admin_service.merge_base_calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "target_branch": "main",
            "candidate_commit_sha": candidate_commit,
            "directory_path": "root/team-a",
        }
    ]
    assert output["target_commit_sha"] == target_commit
    assert output["merge_base_commit_sha"] == merge_base_commit


def test_git_merge_base_query_provider_rejects_sibling_directory_scope() -> None:
    query = GitMergeBaseQueryDefinition(
        id="query-merge-base",
        display_name="Git merge base",
        type="query:git:merge-base",
        managed_repo_id="mr_0123456789abcdefabcd",
        target_branch="main",
        candidate_commit_sha="c" * 40,
        directory_path="root/team-b",
    )
    admin_service = _AdminServiceStub(
        vms=[],
        merge_base_result={"merge_base_commit_sha": "a" * 40},
        merge_base_calls=[],
    )
    admin_service.managed_git_admin = SimpleNamespace(  # type: ignore[attr-defined]
        resolve_merge_base=admin_service.resolve_managed_git_merge_base
    )
    provider = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    ).build(query, directory_path="root/team-a")

    with pytest.raises(
        ValueError,
        match="directory_path 'root/team-b' is outside workflow directory scope 'root/team-a'",
    ):
        provider.execute()

    assert admin_service.merge_base_calls == []


def test_session_context_query_provider_returns_metadata(tmp_path: Path) -> None:
    query = SessionContextQueryDefinition(
        id="query-session",
        display_name="Session context",
        type="query:session-context",
        session_id="session-1",
    )
    admin_service = _AdminServiceStub(
        vms=[],
        pr_note_result=None,
        pr_note_calls=[],
    )
    calls: list[dict[str, object]] = []
    admin_service.session_context_query = lambda **kwargs: (
        calls.append(kwargs)
        or {  # type: ignore[attr-defined]
            "session_id": "session-1",
            "vm_name": "vm-1",
            "directory_path": "root/team-a",
            "metadata": {"issue_number": "14084", "issue_title": "Fix App"},
            "created_at": "2026-04-07T10:00:00+00:00",
            "expires_at": "2026-04-09T10:00:00+00:00",
            "model": "gpt-5.4",
        }
    )

    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )
    provider = factory.build(query)
    output = provider.execute()

    assert calls == [{"target": "session-1"}]
    assert output["metadata"]["issue_number"] == "14084"


def test_issue_get_query_provider_returns_current_issue_payload(tmp_path: Path) -> None:
    query = IssueGetQueryDefinition(
        id="query-issue",
        display_name="Issue get",
        type="query:issue:get",
        issue_id="root:issue-42",
    )
    admin_service = _AdminServiceStub(
        vms=[],
        issue_result={
            "issue_id": "root:issue-42",
            "issue_number": "42",
            "title": "Fix planner resume",
            "metadata": {"steps": ["analyze", "implement"]},
        },
        issue_calls=[],
    )

    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )
    provider = factory.build(query)
    output = provider.execute()

    assert admin_service.issue_calls == [{"issue_id": "root:issue-42"}]
    assert output["metadata"]["steps"] == ["analyze", "implement"]


def test_issue_list_query_provider_returns_issue_collection_payload() -> None:
    query = IssueListQueryDefinition(
        id="query-issues",
        display_name="Issue list",
        type="query:issue:list",
        directory_path="root/team-a",
        direct_only=False,
        states=["Open"],
        exclude_states=["Done"],
    )
    admin_service = _AdminServiceStub(
        vms=[],
        issue_list_result=[
            {
                "issue_id": "root::local::1",
                "issue_number": "1",
                "title": "Fix planner resume",
                "body": "Body",
                "description": "Body",
                "reason": None,
                "rejection_reason": None,
                "yaml_path": "/tmp/1.yaml",
                "directory_path": "root/team-a",
                "repo_source": "repo-source",
                "source_type": "local_manual",
                "state": "Open",
                "url": "https://example.test/issues/1",
                "tags": ["triage"],
                "comments": [],
                "source": "local_gui",
                "metadata": {"steps": ["analyze", "implement"]},
                "created_at": "2026-05-18T12:00:00+00:00",
                "updated_at": "2026-05-18T12:00:00+00:00",
                "remote_reference": None,
                "can_edit": True,
                "can_delete": True,
                "can_create_vm": True,
            }
        ],
        issue_list_calls=[],
    )

    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )
    provider = factory.build(query)
    output = provider.execute()

    assert admin_service.issue_list_calls == [
        {
            "directory_path": None,
            "direct_only": False,
            "states": {"Open"},
            "exclude_states": {"Done"},
        }
    ]
    assert output["count"] == 1
    assert output["issues"][0]["issue_id"] == "root::local::1"


def test_agent_artifact_text_query_provider_returns_artifact_content() -> None:
    query = AgentArtifactTextQueryDefinition(
        id="query-artifact",
        display_name="Artifact text",
        type="query:agent:artifact-text",
        session_id="session-1",
        path="reports/summary.txt",
    )
    admin_service = _AdminServiceStub(
        vms=[],
        artifact_result={
            "session_id": "session-1",
            "path": "reports/summary.txt",
            "content": "daily summary\n",
            "byte_count": 14,
            "artifacts_root": "/artifacts",
            "execution_backend": "container_worker_vm",
        },
        artifact_calls=[],
    )

    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )
    provider = factory.build(query)
    output = provider.execute()

    assert admin_service.artifact_calls == [
        {"session_id": "session-1", "path": "reports/summary.txt", "vm_user": "agent"}
    ]
    assert output["content"] == "daily summary\n"
    assert output["byte_count"] == 14












def test_remote_issues_query_provider_passes_github_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = RemoteIssuesQueryDefinition(
        id="query-remote",
        display_name="Remote issues",
        type="query:remote-issues",
        provider="github",
        directory_path="root/github",
        repo="owner/repo",
        state="all",
        labels_any=["bug", "triage"],
        assignee="octocat",
        text_search="crash",
        top=20,
        skip=5,
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config_resolver: object) -> None:
            captured["provider_config_resolver"] = provider_config_resolver

        def query_remote_issues(self, **kwargs: object) -> dict[str, object]:
            captured["request"] = kwargs
            return {
                "provider": "github",
                "repo": "owner/repo",
                "items": [{"remote_issue_id": "42", "title": "Crash"}],
                "count": 1,
            }

    monkeypatch.setattr("evander.queries.providers.remote_issues.IssueService", StubIssueService)
    resolver = _ResolverStub()
    factory = QueryProviderFactory(
        provider_config_resolver=resolver,
        admin_service=_AdminServiceStub(vms=[]),
    )
    provider = factory.build(query)
    output = provider.execute()

    assert captured["provider_config_resolver"] is resolver
    assert captured["request"] == {
        "provider": "github",
        "directory_path": "root/github",
        "project": None,
        "repo": "owner/repo",
        "wiql": None,
        "current_iteration": False,
        "team": None,
        "iteration": None,
        "area_path": None,
        "states": [],
        "types": [],
        "tags_any": [],
        "text_search": "crash",
        "state": "all",
        "labels_any": ["bug", "triage"],
        "labels_all": [],
        "assignee": "octocat",
        "author": None,
        "mentioned": None,
        "milestone": None,
        "since": None,
        "top": 20,
        "skip": 5,
    }
    assert output["items"][0]["remote_issue_id"] == "42"


def test_remote_issues_query_provider_rejects_github_query_mode() -> None:
    query = RemoteIssuesQueryDefinition(
        id="query-remote",
        display_name="Remote issues",
        type="query:remote-issues",
        provider="github",
        repo="owner/repo",
        query_mode="structured_filters",
    )
    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=_AdminServiceStub(vms=[]),
    )
    provider = factory.build(query)

    with pytest.raises(ValueError, match="query_mode is only valid for Azure DevOps"):
        provider.execute()


def test_remote_issues_query_provider_passes_ado_query_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = RemoteIssuesQueryDefinition(
        id="query-remote-ado",
        display_name="Remote ADO issues",
        type="query:remote-issues",
        provider="ado",
        project="App",
        query_mode="structured_filters",
        states=["Committed"],
    )
    captured: dict[str, object] = {}

    class StubIssueService:
        def __init__(self, provider_config_resolver: object) -> None:
            captured["provider_config_resolver"] = provider_config_resolver

        def query_remote_issues(self, **kwargs: object) -> dict[str, object]:
            captured["request"] = kwargs
            return {"provider": "azure_devops", "items": [], "count": 0}

    monkeypatch.setattr("evander.queries.providers.remote_issues.IssueService", StubIssueService)
    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=_AdminServiceStub(vms=[]),
    )
    provider = factory.build(query)
    provider.execute()

    assert captured["request"]["query_mode"] == "structured_filters"
    assert captured["request"]["states"] == ["Committed"]


def test_pull_request_list_query_provider_resolves_ado_config_and_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = PullRequestListQueryDefinition(
        id="query-prs",
        display_name="PR list",
        type="query:pull-request:list",
        provider="ado",
        repo="repo-1",
        directory_path="root/team-a",
        status="active",
        source_ref_name="refs/heads/feature",
        target_ref_name="refs/heads/main",
        include_links="true",
        tags="needs-review and agent",
        top="25",
        skip="5",
    )
    assert query.provider == "azure_devops"
    captured: dict[str, object] = {}

    class StubPrProvider:
        def __init__(self, **kwargs: object) -> None:
            captured["init"] = kwargs

        def list_pull_requests(self, **kwargs: object) -> ListPullRequestsResponse:
            captured["request"] = kwargs
            return ListPullRequestsResponse(
                provider="azure_devops",
                repo="repo-1",
                project="default",
                pull_requests=[
                    PullRequestItem(
                        provider="azure_devops",
                        repo="repo-1",
                        pr_id="17",
                        title="Fix race",
                        status="active",
                        tags=["needs-review", "agent"],
                    )
                ],
                count=1,
                paging={"returned_count": 1},
            )

    monkeypatch.setattr(
        "evander.pull_request_automation.AzureDevOpsPrQueryProvider", StubPrProvider
    )
    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=_AdminServiceStub(vms=[]),
        env={"AZURE_DEVOPS_PR_PAT": "secret"},
    )
    provider = factory.build(query)
    output = provider.execute(resolved_config={"cursor": "cursor-1"})

    assert captured["init"] == {
        "organization_url": "https://dev.azure.com/directory-org",
        "project": "default",
        "pat": "secret",
        "timeout_seconds": 30.0,
    }
    assert captured["request"] == {
        "repo": "repo-1",
        "project": "default",
        "status": "active",
        "creator_id": None,
        "reviewer_id": None,
        "source_ref_name": "refs/heads/feature",
        "target_ref_name": "refs/heads/main",
        "source_repository_id": None,
        "repository_id": None,
        "min_time": None,
        "max_time": None,
        "query_time_range_type": None,
        "include_links": True,
        "max_comment_length": None,
        "tags": "needs-review and agent",
        "top": 25,
        "skip": 5,
        "cursor": "cursor-1",
    }
    assert output["pull_requests"][0]["tags"] == ["needs-review", "agent"]
