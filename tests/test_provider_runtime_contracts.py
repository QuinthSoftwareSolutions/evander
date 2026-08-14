from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.provider_models import (
    AgentEnqueuePromptEffectDefinition,
    IssueGetQueryDefinition,
    IssueListQueryDefinition,
    VmRunStateQueryDefinition,
)
from evander.models import LocalIssueRecord, SessionManifest, ToolName, VmSummary
from evander.provider_runtime.adapters import (
    AdminRuntimeProviderAdapter,
    build_provider_runtime_ports,
)
from evander.queries.providers.issue_get import IssueGetQueryProvider
from evander.queries.providers.issue_list import IssueListQueryProvider
from evander.queries.providers.vm_run_state import VmRunStateQueryProvider
from evander.queries.providers.base import QueryRuntimeContext


@dataclass
class _FakeEffectServices:
    resumed: list[dict[str, Any]] = field(default_factory=list)

    def resume_agent(self, **kwargs: Any) -> Any:
        self.resumed.append(kwargs)
        return type("ResumeResult", (), {"success": True, "thread_id": "thread-1"})()

    def list_vms(self, **kwargs: Any) -> list[VmSummary]:
        del kwargs
        return [
            VmSummary(
                vm_name="vm-1",
                session_ids=["session-1"],
                libvirt_state="shut off",
                agent_state="stopped",
            )
        ]

    def read_all_sessions(self):
        return {
            "session-1": SessionManifest(
                session_id="session-1",
                vm_id="vm-1",
                allowed_tools=[ToolName.GET_AGENT_STATUS],
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        }


def test_effect_provider_can_run_with_only_contract_interfaces(tmp_path: Path) -> None:
    services = _FakeEffectServices()
    provider = AgentEnqueuePromptEffectProvider()
    provider.initialize(
        AgentEnqueuePromptEffectDefinition(
            id="effect-1",
            display_name="enqueue",
            type="effect:agent:enqueue-prompt",
            session_id="session-1",
            prompt="hello",
        ),
        EffectRuntimeContext(
            provider_config={"runtime_dir": tmp_path},
            agent_resumer=services,
            vm_inventory=services,
            session_lookup=services,
        ),
    )

    output = provider.execute()

    assert output["success"] is True
    assert services.resumed and services.resumed[0]["target"] == "session-1"


def test_admin_runtime_adapter_hides_skill_validation_without_skill_admin() -> None:
    adapter = AdminRuntimeProviderAdapter(SimpleNamespace())

    assert not hasattr(adapter, "validate_managed_codex_skills")
    assert not hasattr(adapter, "resolve_container_codex_skill_files")


def test_admin_runtime_adapter_delegates_skill_validation_when_backed() -> None:
    class _SkillAdmin:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def validate_managed_codex_skills(self, skill_names):
            self.calls.append(list(skill_names))
            return ["managed:" + name for name in skill_names]

        def resolve_container_codex_skill_files(self, skill_names):
            return [{"name": name, "path": f"/skills/{name}/SKILL.md"} for name in skill_names]

    skill_admin = _SkillAdmin()
    adapter = AdminRuntimeProviderAdapter(SimpleNamespace(skill_admin=skill_admin))

    assert adapter.validate_managed_codex_skills([" skill-a ", "skill-a"]) == ["managed:skill-a"]
    assert skill_admin.calls == [["skill-a"]]
    assert adapter.resolve_container_codex_skill_files(["skill-a"]) == [
        {"name": "skill-a", "path": "/skills/skill-a/SKILL.md"}
    ]


def test_provider_runtime_ports_preserve_original_admin_service_context() -> None:
    admin_service = SimpleNamespace(runtime="original-runtime")

    ports = build_provider_runtime_ports(admin_service=admin_service)

    assert ports.admin_service is admin_service
    assert isinstance(ports.vm_creator, AdminRuntimeProviderAdapter)


def test_admin_runtime_adapter_exposes_branch_aware_managed_git_refresh() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class _ManagedGit:
        def refresh_repository_if_branch_changed(
            self,
            managed_repo_id: str,
            **kwargs: object,
        ) -> dict[str, object]:
            calls.append((managed_repo_id, kwargs))
            return {"managed_repo_id": managed_repo_id, "refresh_performed": False}

    adapter = AdminRuntimeProviderAdapter(SimpleNamespace(managed_git_admin=_ManagedGit()))

    assert adapter.refresh_managed_git_repository_if_branch_changed(
        "mr_0123456789abcdef0123",
        branch="main",
        directory_path="root/team",
    ) == {
        "managed_repo_id": "mr_0123456789abcdef0123",
        "refresh_performed": False,
    }
    assert calls == [
        (
            "mr_0123456789abcdef0123",
            {"branch": "main", "directory_path": "root/team"},
        )
    ]


def test_admin_runtime_adapter_exposes_reference_repo_commit_transition_verification() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class _ManagedGit:
        def verify_reference_repo_commit_transition(
            self,
            managed_repo_id: str,
            **kwargs: object,
        ) -> dict[str, object]:
            calls.append((managed_repo_id, kwargs))
            return {
                "managed_repo_id": managed_repo_id,
                "current_commit_sha": kwargs["current_commit_sha"],
            }

    adapter = AdminRuntimeProviderAdapter(SimpleNamespace(managed_git_admin=_ManagedGit()))
    current_commit = "b" * 40

    assert adapter.verify_managed_git_reference_repo_commit_transition(
        "mr_0123456789abcdef0123",
        directory_path="root/team",
        branch="main",
        cached_branch_commit_sha="c" * 40,
        trusted_commit_sha="a" * 40,
        current_commit_sha=current_commit,
    ) == {
        "managed_repo_id": "mr_0123456789abcdef0123",
        "current_commit_sha": current_commit,
    }
    assert calls == [
        (
            "mr_0123456789abcdef0123",
            {
                "directory_path": "root/team",
                "branch": "main",
                "cached_branch_commit_sha": "c" * 40,
                "trusted_commit_sha": "a" * 40,
                "current_commit_sha": current_commit,
            },
        )
    ]


@dataclass
class _FakeQueryServices:
    def list_vms(self, **kwargs: Any) -> list[VmSummary]:
        del kwargs
        return [
            VmSummary(
                vm_name="vm-2",
                session_ids=["session-2"],
                libvirt_state="running",
                agent_state="omitted",
            )
        ]

    def get_issue(self, **kwargs: Any) -> dict[str, Any]:
        return {"issue_id": kwargs["issue_id"], "title": "Synthetic issue"}

    def list_issues(self, **kwargs: Any) -> list[LocalIssueRecord]:
        del kwargs
        return [
            LocalIssueRecord(
                issue_id="root::local::7",
                issue_number="7",
                title="Synthetic issue list entry",
                body="Body",
                yaml_path="/tmp/issue-7.yaml",
                directory_path="root/team-a",
                state="Open",
                source="local_gui",
            )
        ]


def test_query_providers_can_run_with_fake_contract_implementations() -> None:
    services = _FakeQueryServices()

    vm_provider = VmRunStateQueryProvider()
    vm_provider.initialize(
        VmRunStateQueryDefinition(
            id="query-vm",
            display_name="vm",
            type="query:vm:run-state",
            session_id="session-2",
        ),
        QueryRuntimeContext(provider_config={}, vm_inventory=services),
    )
    vm_output = vm_provider.execute(resolved_config={"session_id": "session-2"})
    assert vm_output["state_code"] == "running"

    issue_provider = IssueGetQueryProvider()
    issue_provider.initialize(
        IssueGetQueryDefinition(
            id="query-issue",
            display_name="issue",
            type="query:issue:get",
            issue_id="root:issue-7",
        ),
        QueryRuntimeContext(provider_config={}, issue_reader=services),
    )
    issue_output = issue_provider.execute()
    assert issue_output["issue_id"] == "root:issue-7"

    issue_list_provider = IssueListQueryProvider()
    issue_list_provider.initialize(
        IssueListQueryDefinition(
            id="query-issues",
            display_name="issues",
            type="query:issue:list",
        ),
        QueryRuntimeContext(
            provider_config={},
            issue_reader=services,
            metadata={"directory_path": "root/team-a"},
        ),
    )
    issue_list_output = issue_list_provider.execute()
    assert issue_list_output["count"] == 1
    assert issue_list_output["issues"][0]["issue_id"] == "root::local::7"
