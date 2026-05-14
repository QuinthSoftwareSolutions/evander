from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.provider_models import (
    AdoWorkItemsQueryDefinition,
    AgentArtifactTextQueryDefinition,
    GitPrNoteQueryDefinition,
    IssueGetQueryDefinition,
    SessionContextQueryDefinition,
    VmRunStateQueryDefinition,
)
from agent_sandbox.models import VmSummary
from agent_sandbox.queries.providers.base import QueryProviderBase
from agent_sandbox.queries.registry import (
    DEFAULT_QUERY_PROVIDER_REGISTRY,
    QueryProviderFactory,
    QueryProviderRegistry,
)


class _ResolverStub:
    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        return GatewayConfig(runtime_dir=(GatewayConfig().runtime_dir / (directory_path or "root")))


@dataclass
class _AdminServiceStub:
    vms: list[VmSummary]
    pr_note_result: dict[str, object] | None = None
    pr_note_calls: list[dict[str, object]] | None = None
    issue_result: dict[str, object] | None = None
    issue_calls: list[dict[str, object]] | None = None
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

    def get_issue(self, **kwargs):
        if self.issue_calls is not None:
            self.issue_calls.append(kwargs)
        if self.issue_result is None:
            raise RuntimeError("issue_result not configured")
        return dict(self.issue_result)

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
        "query:vm:run-state",
        "query:git:pr-note",
        "query:session-context",
        "query:agent:artifact-text",
        "query:issue:get",
        "query:ado:work-items",
    }


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
    provider = factory.build(query)

    assert isinstance(provider, _RecordingProvider)
    assert provider.runtime_context is not None
    assert provider.runtime_context.resolve_secret("API_TOKEN") == "top-secret"


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
        host_repo_path="/repo",
        local_branch="review/session-1/topic",
    )
    admin_service = _AdminServiceStub(
        vms=[],
        pr_note_result={
            "body": "Agent note body",
            "has_note": True,
            "local_branch": "review/session-1/topic",
            "host_repo_path": "/repo",
            "notes_ref": "refs/notes/agent-pr",
            "local_notes_ref": "refs/notes/agent-pr-cache/repo",
            "remote_name": "agent-dropbox",
            "commit_sha": "a" * 40,
        },
        pr_note_calls=[],
    )

    factory = QueryProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )
    provider = factory.build(query)
    output = provider.execute(resolved_config={"remote_name": " agent-dropbox "})

    assert admin_service.pr_note_calls == [
        {
            "host_repo_path": "/repo",
            "local_branch": "review/session-1/topic",
            "remote_name": "agent-dropbox",
            "notes_ref": None,
        }
    ]
    assert output["has_note"] is True
    assert output["body"] == "Agent note body"


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
            "metadata": {"issue_number": "14084", "issue_title": "Fix APP"},
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

    monkeypatch.setattr(
        "agent_sandbox.queries.providers.ado_work_items.IssueService", StubIssueService
    )
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

    monkeypatch.setattr(
        "agent_sandbox.queries.providers.ado_work_items.IssueService", StubIssueService
    )
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
