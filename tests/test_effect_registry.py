from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directories import DirectoryStore
from agent_sandbox.execution import ExecutionStatusKind
from agent_sandbox.execution.container_worker_repository import ContainerWorkerExecutionRecord
from agent_sandbox.effects.providers.agent_clone_repo import (
    AgentCloneRepoEffectProvider,
    GitAuthorIdentity,
    _container_reachable_git_remote_url,
)
from agent_sandbox.effects.providers.agent_session import (
    build_container_session_files,
    _claude_json_log_filter_script,
    _codex_json_log_filter_script,
    container_prompt_command,
    container_thread_id_from_logs,
    selected_execution_backend_for_target,
)
from agent_sandbox.effects.providers.ado_pipeline_run import AdoPipelineRunEffectProvider
from agent_sandbox.effects.providers.ado_post_work_item_comment import (
    AdoPostWorkItemCommentEffectProvider,
)
from agent_sandbox.effects.providers.ado_work_item_tag import (
    AdoAddWorkItemTagEffectProvider,
    AdoRemoveWorkItemTagEffectProvider,
)
from agent_sandbox.effects.providers.git_push_branch import GitPushBranchEffectProvider
from agent_sandbox.effects.providers.http_request import HttpRequestEffectProvider
from agent_sandbox.effects.providers.pull_request_update import (
    PullRequestAddTagEffectProvider,
    PullRequestRemoveTagEffectProvider,
)
from agent_sandbox.effects.providers.runtime_diagnostics_snapshot import (
    RuntimeDiagnosticsSnapshotEffectProvider,
)
from agent_sandbox.effects.providers.session_revoke_mcp_tool_approval_lease import (
    SessionRevokeMcpToolApprovalLeaseEffectProvider,
)
from agent_sandbox.effects.providers.base import (
    EffectProviderBase,
    EffectProviderExecutionError,
    EffectRuntimeContext,
)
from agent_sandbox.effects.registry import (
    DEFAULT_EFFECT_PROVIDER_REGISTRY,
    EffectProviderFactory,
    EffectProviderRegistry,
)
from pydantic import BaseModel, Field, ValidationError

from agent_sandbox.directory_config import ContactDirectoryConfig
from agent_sandbox.events.models import WorkflowNodeEnvelope
from agent_sandbox.events.provider_models import (
    AgentCloneRepoEffectDefinition,
    AgentCopyFilesEffectDefinition,
    AgentCreateEffectDefinition,
    AgentDestroyEffectDefinition,
    AgentEnqueuePromptEffectDefinition,
    AgentExecEffectDefinition,
    AgentPromptEffectDefinition,
    ChatAssistantEnsureSessionEffectDefinition,
    EventScheduleEffectDefinition,
    PullRequestAddTagEffectDefinition,
    PullRequestRemoveTagEffectDefinition,
    PullRequestUpdateEffectDefinition,
    VmBootstrapFrontendEffectDefinition,
    VmBootstrapPythonEffectDefinition,
    VmCloneRepoEffectDefinition,
    VmCreateEffectDefinition,
)
from agent_sandbox.messaging.agent_prompt_queue import AgentPromptQueueStore
from agent_sandbox.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.models import (
    SessionManifest,
    ToolName,
    UpdatePullRequestResponse,
    VmActionResult,
)
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from agent_sandbox.repositories.skills_library import SkillLibraryRepository


class _ResolverStub:
    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        return GatewayConfig(runtime_dir=(GatewayConfig().runtime_dir / (directory_path or "root")))


class _ContainerResolverStub:
    def __init__(
        self,
        config: GatewayConfig,
        contacts_config: ContactDirectoryConfig | None = None,
    ) -> None:
        self.config = config
        self.contacts_config = contacts_config or ContactDirectoryConfig()

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        del directory_path
        return self.config

    def resolve_for_session_id(self, session_id: str) -> GatewayConfig:
        del session_id
        return self.config

    def resolve_contacts_for_directory(
        self, directory_path: str | None
    ) -> ContactDirectoryConfig:
        del directory_path
        return self.contacts_config

    def resolve_effect_provider_config(self, directory_path: str | None):
        from agent_sandbox.provider_config import EffectProviderConfig

        del directory_path
        return EffectProviderConfig.model_validate(self.config.model_dump(mode="json"))


def _libvirt_vm_defaults_stub() -> SimpleNamespace:
    return SimpleNamespace(
        libvirt_vm_default_template="default",
        libvirt_vm_templates={
            "default": "ubuntu-24.04-server-cloudimg-amd64.img",
            "dotnet": "dotnet-agent.qcow2",
        },
    )


def _write_root_communication_targets(
    config: GatewayConfig,
    targets: list[dict[str, object]],
) -> None:
    DirectoryStore(config.directories_store_path).ensure_root()
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(config.runtime_dir)
    )
    repository.write_overlay("root", {"targets": targets})


class _FakeContainerBackend:
    def __init__(self) -> None:
        self.prepared_sessions: list[dict[str, object]] = []
        self.cloned_repos: list[dict[str, object]] = []
        self.written_files: list[dict[str, object]] = []
        self.prepared_specs: list[object] = []
        self.started_specs: list[object] = []
        self.destroyed: list[str] = []
        self.log_snapshot_calls: list[dict[str, object]] = []
        self.status = SimpleNamespace(status=ExecutionStatusKind.STOPPED, message=None, metadata={})
        self.logs = ""
        self.prepare_error: Exception | None = None
        self.write_files_error: Exception | None = None
        self.start_error: Exception | None = None
        self.clone_result_cloned = True

    def prepare_session(self, **kwargs):
        if self.prepare_error is not None:
            raise self.prepare_error
        self.prepared_sessions.append(kwargs)
        return SimpleNamespace(
            agent_home_container_path="/home/agent",
            repo_root_container_path="/home/agent/repos",
        )

    def clone_repo(self, **kwargs):
        self.cloned_repos.append(kwargs)
        repo_name = kwargs.get("repo_name") or "repo"
        return SimpleNamespace(
            target_dir=f"/home/agent/repos/{repo_name}",
            guest_repo_name=repo_name,
            remote_url=kwargs["repo_source"],
            cloned=self.clone_result_cloned,
        )

    def write_files(self, **kwargs):
        if self.write_files_error is not None:
            raise self.write_files_error
        self.written_files.append(kwargs)
        return SimpleNamespace(
            copied_count=len(kwargs["files"]),
            copied_paths=[entry["destination_path"] for entry in kwargs["files"]],
        )

    def prepare(self, spec):
        self.prepared_specs.append(spec)
        return spec

    def start(self, spec):
        if self.start_error is not None:
            raise self.start_error
        self.started_specs.append(spec)
        return SimpleNamespace(execution_id=spec.target_name)

    def inspect(self, execution_id: str):
        return self.status

    def destroy(self, execution_id: str):
        self.destroyed.append(execution_id)

    def get_log_snapshot(self, execution_id: str, *, lines: int = 50, mode: str | None = None):
        self.log_snapshot_calls.append({"execution_id": execution_id, "lines": lines, "mode": mode})
        return self.logs


class _FakeContainerGitMirrorResolver:
    def __init__(
        self,
        *,
        worker_repo_source: str = "ssh://agent-sandbox-git-host/git-remotes/repo.git",
        canonical_repo_source: str = "https://example.test/org/repo.git",
    ) -> None:
        self.worker_repo_source = worker_repo_source
        self.canonical_repo_source = canonical_repo_source
        self.resolved: list[dict[str, object]] = []

    def resolve(self, **kwargs):
        self.resolved.append(kwargs)
        return SimpleNamespace(
            worker_repo_source=self.worker_repo_source,
            canonical_repo_source=self.canonical_repo_source,
            repo_name="repo",
            mirror_name="repo-abcdef123456",
            mirror_path="/git-remotes/repo-abcdef123456.git",
        )


@dataclass
class _AdminServiceStub:
    create_vm_calls: list[dict[str, object]]
    clone_repo_into_vm_calls: list[dict[str, object]]
    bootstrap_python_in_vm_calls: list[dict[str, object]]
    bootstrap_frontend_in_vm_calls: list[dict[str, object]]
    resume_calls: list[dict[str, object]]

    def __post_init__(self) -> None:
        sessions = [
            SessionManifest(
                session_id="session-42",
                vm_id="vm-42",
                allowed_tools=[ToolName.GET_AGENT_STATUS],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        ]
        self._sessions = {session.session_id: session for session in sessions}
        self.runtime = SimpleNamespace(
            session_repository=SimpleNamespace(
                list_sessions=lambda: list(self._sessions.values()),
                read_all=lambda: dict(self._sessions),
                get_session=lambda session_id: self._sessions.get(session_id),
                upsert_session=lambda session: self._sessions.__setitem__(
                    session.session_id, session
                ),
            )
        )

    def create_vm(self, **kwargs):
        self.create_vm_calls.append(kwargs)
        return VmActionResult(
            success=True, vm_name=str(kwargs["vm_name"]), action="create_vm", message="ok"
        )

    def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
        del directory_path
        return _libvirt_vm_defaults_stub()

    def clone_repo_into_vm(self, **kwargs):
        self.clone_repo_into_vm_calls.append(kwargs)
        return VmActionResult(
            success=True, vm_name=str(kwargs["vm_name"]), action="clone_repo_into_vm", message="ok"
        )

    def bootstrap_python_in_vm(self, **kwargs):
        self.bootstrap_python_in_vm_calls.append(kwargs)
        return VmActionResult(
            success=True,
            vm_name=str(kwargs["vm_name"]),
            action="bootstrap_python_in_vm",
            message="ok",
        )

    def bootstrap_frontend_in_vm(self, **kwargs):
        self.bootstrap_frontend_in_vm_calls.append(kwargs)
        return VmActionResult(
            success=True,
            vm_name=str(kwargs["vm_name"]),
            action="bootstrap_frontend_in_vm",
            message="ok",
        )

    def resume_agent(
        self,
        *,
        target: str,
        prompt: str,
        model: str | None = None,
        vm_user: str = "agent",
        dispatch_id: str | None = None,
        resume_thread_id: str | None = None,
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ):
        self.resume_calls.append(
            {
                "target": target,
                "prompt": prompt,
                "model": model,
                "vm_user": vm_user,
                "dispatch_id": dispatch_id,
                "resume_thread_id": resume_thread_id,
                "persist_thread_id": persist_thread_id,
                "mcp_gateway_url": mcp_gateway_url,
            }
        )
        return VmActionResult(
            success=True,
            vm_name=target,
            action="resume_issue_agent",
            message="ok",
            thread_id=resume_thread_id or "thread-new-123",
        )

    def session_update_metadata(self, *, target: str, metadata: dict[str, object]):
        session = self._sessions[target]
        updated = session.model_copy(
            update={"metadata": _deep_merge_metadata(session.metadata, metadata)}
        )
        self._sessions[target] = updated
        return {"success": True, "session_id": target, "metadata": updated.metadata}


def _deep_merge_metadata(base: dict[str, object], update: dict[str, object]) -> dict[str, object]:
    merged = dict(base)
    for key, value in update.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_metadata(existing, value)
            continue
        merged[key] = value
    return merged


class _RecordingProvider(EffectProviderBase):
    pass


class _CustomEffectSchema(BaseModel):
    greeting: str = Field(min_length=1)


class _CustomSchemaProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        assert isinstance(effect_definition, WorkflowNodeEnvelope)
        validated = _CustomEffectSchema.model_validate(effect_definition.config)
        effect_definition.config = validated.model_dump()
        return effect_definition


def test_default_registry_includes_all_known_effect_types() -> None:
    assert set(DEFAULT_EFFECT_PROVIDER_REGISTRY) == {
        "effect:vm:create",
        "effect:vm:clone-repo",
        "effect:vm:bootstrap-python",
        "effect:vm:bootstrap-frontend",
        "effect:vm:start",
        "effect:vm:stop",
        "effect:agent:create",
        "effect:agent:destroy",
        "effect:agent:clone-repo",
        "effect:agent:bootstrap-frontend",
        "effect:agent:copy-files",
        "effect:artifact:stage",
        "effect:artifact:publish-artifacts",
        "effect:artifact:import-published",
        "effect:artifact-space:create",
        "effect:artifact-space:publish-version",
        "effect:agent:prompt",
        "effect:agent:enqueue-prompt",
        "effect:chat-assistant:ensure-session",
        "effect:agent:exec",
        "effect:semantic:ensure-collection",
        "effect:semantic:upsert",
        "effect:semantic:delete",
        "effect:state:update",
        "effect:http:request",
        "effect:runtime:diagnostics-snapshot",
        "effect:event:emit",
        "effect:event:schedule",
        "effect:vm:exec",
        "effect:vm:copy-files",
        "effect:vm:delete-files",
        "effect:issue:update-state",
        "effect:issue:update-metadata",
        "effect:issue:upsert-ado-proxy",
        "effect:ado:pipeline-run",
        "effect:ado:post-work-item-comment",
        "effect:ado:add-work-item-tag",
        "effect:ado:remove-work-item-tag",
        "effect:ado:update-work-item",
        "effect:communication:send-message",
        "effect:notification:send",
        "effect:session:update-metadata",
        "effect:session:approve-mcp-tools-for-run",
        "effect:session:revoke-mcp-tool-approval-lease",
        "effect:session:renew-if-expired",
        "effect:git:promote-agent-branch",
        "effect:git:push-branch",
        "effect:pr:create",
        "effect:pull-request:add-tag",
        "effect:pull-request:remove-tag",
        "effect:pull-request:update",
    }
    assert DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:http:request"] is HttpRequestEffectProvider
    assert (
        DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:runtime:diagnostics-snapshot"]
        is RuntimeDiagnosticsSnapshotEffectProvider
    )
    assert (
        DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:ado:pipeline-run"] is AdoPipelineRunEffectProvider
    )
    assert (
        DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:ado:post-work-item-comment"]
        is AdoPostWorkItemCommentEffectProvider
    )
    assert (
        DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:ado:add-work-item-tag"]
        is AdoAddWorkItemTagEffectProvider
    )
    assert (
        DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:ado:remove-work-item-tag"]
        is AdoRemoveWorkItemTagEffectProvider
    )
    assert (
        DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:pull-request:add-tag"]
        is PullRequestAddTagEffectProvider
    )
    assert (
        DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:pull-request:remove-tag"]
        is PullRequestRemoveTagEffectProvider
    )
    assert (
        DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:session:revoke-mcp-tool-approval-lease"]
        is SessionRevokeMcpToolApprovalLeaseEffectProvider
    )
    assert DEFAULT_EFFECT_PROVIDER_REGISTRY["effect:git:push-branch"] is GitPushBranchEffectProvider


def test_event_schedule_effect_definition_validates_contract_shape() -> None:
    effect = EventScheduleEffectDefinition(
        id="effect-event-schedule",
        display_name="Schedule internal event",
        type="effect:event:schedule",
        event_type="custom.operator_event",
        payload={"hello": "world"},
        delay_seconds=60,
    )

    assert effect.event_type == "custom.operator_event"
    assert effect.payload == {"hello": "world"}

    with pytest.raises(ValidationError, match="exactly one of run_at or delay_seconds"):
        EventScheduleEffectDefinition(
            id="effect-event-schedule",
            display_name="Schedule internal event",
            type="effect:event:schedule",
            event_type="custom.operator_event",
            payload={},
        )

    with pytest.raises(ValidationError, match="payload must be a JSON object"):
        EventScheduleEffectDefinition(
            id="effect-event-schedule",
            display_name="Schedule internal event",
            type="effect:event:schedule",
            event_type="custom.operator_event",
            payload=[],
            delay_seconds=60,
        )

    with pytest.raises(ValidationError, match="event_type must start with"):
        EventScheduleEffectDefinition(
            id="effect-event-schedule",
            display_name="Schedule internal event",
            type="effect:event:schedule",
            event_type="bad event",
            payload={},
            delay_seconds=60,
        )

    with pytest.raises(ValidationError, match="exactly one of run_at or delay_seconds"):
        EventScheduleEffectDefinition(
            id="effect-event-schedule",
            display_name="Schedule internal event",
            type="effect:event:schedule",
            event_type="custom.operator_event",
            payload={},
            run_at="2030-01-01T12:00:00Z",
            delay_seconds=60,
        )


def test_factory_initializes_provider_with_runtime_context() -> None:
    effect = AgentPromptEffectDefinition(
        id="effect-1",
        display_name="Prompt",
        type="effect:agent:prompt",
        prompt="hello",
        session_id="session-1",
    )
    registry = EffectProviderRegistry()
    registry.register("effect:agent:prompt", _RecordingProvider)
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )

    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(),
        registry=registry,
        admin_service=admin_service,
        env={"API_TOKEN": "top-secret"},
    )
    provider = factory.build(effect, directory_path="root/team-a", graph_id="graph-1")

    assert isinstance(provider, _RecordingProvider)
    assert provider.effect_definition == effect
    assert provider.runtime_context is not None
    assert provider.runtime_context.resolve_secret("API_TOKEN") == "top-secret"
    assert provider.runtime_context.metadata["directory_path"] == "root/team-a"
    assert provider.runtime_context.metadata["graph_id"] == "graph-1"


def test_pull_request_update_effect_provider_resolves_ado_config_and_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect = PullRequestUpdateEffectDefinition(
        id="effect-pr-update",
        display_name="Update PR",
        type="effect:pull-request:update",
        provider="ado",
        repo="repo-1",
        pr_id="17",
        title="New title",
        add_tags="reviewed",
        remove_tags=["needs-review"],
        expected_statuses="active",
    )
    assert effect.provider == "azure_devops"
    captured: dict[str, object] = {}

    class StubPrProvider:
        def __init__(self, **kwargs: object) -> None:
            captured["init"] = kwargs

        def update_pull_request(self, **kwargs: object) -> UpdatePullRequestResponse:
            captured["request"] = kwargs
            return UpdatePullRequestResponse(
                success=True,
                updated=True,
                skipped=False,
                provider="azure_devops",
                repo="repo-1",
                pr_id="17",
                status="active",
                title="New title",
                url="https://example.test/pr/17",
                tags=["reviewed"],
                added_tags=["reviewed"],
                removed_tags=["needs-review"],
            )

    monkeypatch.setattr(
        "agent_sandbox.pull_request_automation.AzureDevOpsPrQueryProvider", StubPrProvider
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=_AdminServiceStub(
            create_vm_calls=[],
            clone_repo_into_vm_calls=[],
            bootstrap_python_in_vm_calls=[],
            bootstrap_frontend_in_vm_calls=[],
            resume_calls=[],
        ),
        env={"AZURE_DEVOPS_PR_PAT": "secret"},
    )
    provider = factory.build(effect, directory_path="root/team-a")
    output = provider.execute()

    assert captured["init"] == {
        "organization_url": "https://dev.azure.com",
        "project": "default",
        "pat": "secret",
        "timeout_seconds": 30.0,
    }
    assert captured["request"] == {
        "repo": "repo-1",
        "pr_id": "17",
        "project": "default",
        "title": "New title",
        "description": None,
        "status": None,
        "add_tags": ["reviewed"],
        "remove_tags": ["needs-review"],
        "expected_statuses": ["active"],
    }
    assert output["updated"] is True
    assert output["added_tags"] == ["reviewed"]


def test_pull_request_tag_effect_providers_resolve_single_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_effect = PullRequestAddTagEffectDefinition(
        id="effect-pr-add-tag",
        display_name="Add PR tag",
        type="effect:pull-request:add-tag",
        provider="ado",
        repo="repo-1",
        pr_id="17",
        tag="reviewed",
    )
    remove_effect = PullRequestRemoveTagEffectDefinition(
        id="effect-pr-remove-tag",
        display_name="Remove PR tag",
        type="effect:pull-request:remove-tag",
        provider="azure-devops",
        repo="repo-1",
        pr_id="17",
        tag="stale",
    )
    assert add_effect.provider == "azure_devops"
    assert remove_effect.provider == "azure_devops"
    captured_requests: list[dict[str, object]] = []

    class StubPrProvider:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def update_pull_request(self, **kwargs: object) -> UpdatePullRequestResponse:
            captured_requests.append(dict(kwargs))
            tag = (kwargs.get("add_tags") or kwargs.get("remove_tags") or [""])[0]
            return UpdatePullRequestResponse(
                success=True,
                updated=True,
                skipped=False,
                provider="azure_devops",
                repo="repo-1",
                pr_id="17",
                status="active",
                title="Existing title",
                url="https://example.test/pr/17",
                tags=[tag] if kwargs.get("add_tags") else [],
                added_tags=list(kwargs.get("add_tags") or []),
                removed_tags=list(kwargs.get("remove_tags") or []),
            )

    monkeypatch.setattr(
        "agent_sandbox.pull_request_automation.AzureDevOpsPrQueryProvider", StubPrProvider
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=_AdminServiceStub(
            create_vm_calls=[],
            clone_repo_into_vm_calls=[],
            bootstrap_python_in_vm_calls=[],
            bootstrap_frontend_in_vm_calls=[],
            resume_calls=[],
        ),
        env={"AZURE_DEVOPS_PR_PAT": "secret"},
    )

    add_output = factory.build(add_effect, directory_path="root/team-a").execute()
    remove_output = factory.build(remove_effect, directory_path="root/team-a").execute()

    assert captured_requests == [
        {
            "repo": "repo-1",
            "pr_id": "17",
            "project": "default",
            "title": None,
            "description": None,
            "status": None,
            "add_tags": ["reviewed"],
            "remove_tags": [],
            "expected_statuses": [],
        },
        {
            "repo": "repo-1",
            "pr_id": "17",
            "project": "default",
            "title": None,
            "description": None,
            "status": None,
            "add_tags": [],
            "remove_tags": ["stale"],
            "expected_statuses": [],
        },
    ]
    assert add_output["tag"] == "reviewed"
    assert add_output["added_tags"] == ["reviewed"]
    assert remove_output["tag"] == "stale"
    assert remove_output["removed_tags"] == ["stale"]


def test_vm_create_provider_returns_required_outputs() -> None:
    effect = VmCreateEffectDefinition(
        id="effect-vm",
        display_name="Create VM",
        type="effect:vm:create",
        vm_name="lab-vm",
        labels={"workflow": "issue-worker", "role": "issue"},
        metadata={"issue_number": "42", "issue_title": "Fix picker"},
        dotnet=True,
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect, directory_path="root/team-a")
    output = provider.execute()

    assert output == {
        "provider": "libvirt",
        "session_id": "session-lab-vm",
        "vm_name": "lab-vm",
        "labels": {"workflow": "issue-worker", "role": "issue"},
        "success": True,
        "message": "ok",
    }
    assert admin_service.create_vm_calls[0]["directory_path"] == "root/team-a"
    assert admin_service.create_vm_calls[0]["labels"] == {
        "workflow": "issue-worker",
        "role": "issue",
    }
    assert admin_service.create_vm_calls[0]["metadata"] == {
        "issue_number": "42",
        "issue_title": "Fix picker",
    }
    assert admin_service.create_vm_calls[0]["dotnet"] is True


def test_vm_create_provider_passes_codex_skills() -> None:
    effect = VmCreateEffectDefinition(
        id="effect-vm",
        display_name="Create VM",
        type="effect:vm:create",
        vm_name="lab-vm",
        codex_skills=["playwright", "exampleindustry-app-demo"],
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect, directory_path="root/team-a")
    provider.execute()

    assert admin_service.create_vm_calls[0]["codex_skills"] == [
        "playwright",
        "exampleindustry-app-demo",
    ]


def test_agent_create_provider_passes_session_communication_policy() -> None:
    effect = AgentCreateEffectDefinition(
        id="effect-agent",
        display_name="Create agent",
        type="effect:agent:create",
        execution_backend="libvirt_vm",
        vm_name="lab-vm",
        session_id="lab-session",
        teams_channel_allowlist=["team-a/channel-1", "chat:chat-1"],
        teams_chat_allowlist=[" chat:chat-2 ", "chat:chat-2"],
        communication_file_receive_enabled=True,
        communication_file_send_enabled=False,
        communication_file_max_bytes=4096,
        communication_file_require_approval_for_send=True,
        web_search_enabled=True,
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    output = factory.build(effect, directory_path="root/team-a").execute()

    assert output["session_id"] == "lab-session"
    assert admin_service.create_vm_calls[0]["teams_channel_allowlist"] == [
        "team-a/channel-1",
        "chat:chat-1",
    ]
    assert admin_service.create_vm_calls[0]["teams_chat_allowlist"] == [
        "chat:chat-2",
        "chat:chat-2",
    ]
    assert admin_service.create_vm_calls[0]["communication_file_receive_enabled"] is True
    assert admin_service.create_vm_calls[0]["communication_file_send_enabled"] is False
    assert admin_service.create_vm_calls[0]["communication_file_max_bytes"] == 4096
    assert admin_service.create_vm_calls[0]["communication_file_require_approval_for_send"] is True
    assert admin_service.create_vm_calls[0]["web_search_enabled"] is True


def test_agent_create_provider_maps_communication_target_ids_to_allowlists(tmp_path: Path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)
    _write_root_communication_targets(
        config,
        [
            {
                "id": "teams-channel",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["read", "write"]},
            },
            {
                "id": "teams-chat",
                "provider": "teams",
                "kind": "teams",
                "address": "chat:chat-1",
                "policy": {"capabilities": ["read"]},
            },
            {
                "id": "teams-user",
                "provider": "teams",
                "kind": "teams",
                "address": "user:operator@example.com",
                "policy": {"capabilities": ["write"]},
            },
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678",
                "policy": {"capabilities": ["read", "write"]},
            },
            {
                "id": "ops-email",
                "provider": "email",
                "kind": "email",
                "address": "ops@example.com",
                "policy": {
                    "capabilities": ["read", "write"],
                    "auth_profile_ref": "email.ops",
                },
            },
        ],
    )
    effect = AgentCreateEffectDefinition(
        id="effect-agent",
        display_name="Create agent",
        type="effect:agent:create",
        execution_backend="libvirt_vm",
        vm_name="lab-vm",
        session_id="lab-session",
        communication_target_ids=[
            "teams-channel",
            "teams-chat",
            "teams-user",
            "discord-ops",
            "ops-email",
        ],
        teams_channel_allowlist=["team-a/existing"],
        discord_channels=["111111111111111111"],
        email_mailboxes=["default"],
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    admin_service.runtime.provider_config = _ContainerResolverStub(config)
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    factory.build(effect).execute()

    assert admin_service.create_vm_calls[0]["discord_channels"] == [
        "111111111111111111",
        "discord-ops",
    ]
    assert admin_service.create_vm_calls[0]["teams_channel_allowlist"] == [
        "team-a/existing",
        "teams-channel",
    ]
    assert admin_service.create_vm_calls[0]["teams_chat_allowlist"] == ["teams-chat"]
    assert admin_service.create_vm_calls[0]["teams_user_allowlist"] == ["teams-user"]
    assert admin_service.create_vm_calls[0]["email_mailboxes"] == ["default", "ops"]


@pytest.mark.parametrize(
    ("target_id", "capabilities"),
    [
        ("ops-email-read", ["read"]),
        ("ops-email-write", ["write"]),
    ],
)
def test_agent_create_provider_rejects_capability_limited_email_targets(
    tmp_path: Path, target_id: str, capabilities: list[str]
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)
    _write_root_communication_targets(
        config,
        [
            {
                "id": target_id,
                "provider": "email",
                "kind": "email",
                "address": "ops@example.com",
                "policy": {
                    "capabilities": capabilities,
                    "auth_profile_ref": "email.ops",
                },
            },
        ],
    )
    effect = AgentCreateEffectDefinition(
        id="effect-agent",
        display_name="Create agent",
        type="effect:agent:create",
        execution_backend="libvirt_vm",
        vm_name="lab-vm",
        session_id="lab-session",
        communication_target_ids=[target_id],
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    admin_service.runtime.provider_config = _ContainerResolverStub(config)
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(ValueError, match="requires both read and write capabilities"):
        factory.build(effect).execute()

    assert admin_service.create_vm_calls == []


def test_agent_create_provider_passes_session_metadata() -> None:
    effect = AgentCreateEffectDefinition(
        id="effect-agent",
        display_name="Create agent",
        type="effect:agent:create",
        execution_backend="libvirt_vm",
        vm_name="lab-vm",
        session_id="lab-session",
        metadata={"workflow": {"state": "active"}, "owns_pr": True},
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    output = factory.build(effect, directory_path="root/team-a").execute()

    assert output["session_id"] == "lab-session"
    assert admin_service.create_vm_calls[0]["metadata"] == {
        "workflow": {"state": "active"},
        "owns_pr": True,
        "libvirt_vm_template": {
            "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
            "template_alias": "default",
        },
    }


def test_agent_create_provider_passes_chat_allowlist_without_channel_allowlist() -> None:
    effect = AgentCreateEffectDefinition(
        id="effect-agent",
        display_name="Create agent",
        type="effect:agent:create",
        execution_backend="libvirt_vm",
        vm_name="lab-vm",
        session_id="lab-session",
        teams_chat_allowlist=[" chat:chat-1 "],
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    factory.build(effect, directory_path="root/team-a").execute()

    assert admin_service.create_vm_calls[0]["teams_channel_allowlist"] is None
    assert admin_service.create_vm_calls[0]["teams_chat_allowlist"] == ["chat:chat-1"]


def test_agent_create_libvirt_noops_existing_session(tmp_path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            vm_provider="libvirt",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    class AdminServiceStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.create_vm_calls: list[dict[str, object]] = []

        def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
            del directory_path
            return _libvirt_vm_defaults_stub()

        def create_vm(self, **kwargs):
            self.create_vm_calls.append(kwargs)
            session_repository.upsert_session(
                SessionManifest(
                    session_id=str(kwargs["session_id"]),
                    vm_id=str(kwargs["vm_name"]),
                    vm_provider="libvirt",
                    allowed_tools=[ToolName.GET_AGENT_STATUS],
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    metadata=dict(kwargs.get("metadata") or {}),
                )
            )
            return VmActionResult(
                success=True,
                vm_name=str(kwargs["vm_name"]),
                action="create_vm",
                message="ok",
            )

    admin_service = AdminServiceStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config), admin_service=admin_service
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="effect-agent",
            display_name="Create agent",
            type="effect:agent:create",
            execution_backend="libvirt_vm",
            vm_name="lab-vm",
            session_id="lab-session",
            existing_agent_behavior="noop",
            allowed_tools=["set_issue_plan"],
        )
    ).execute()

    assert output["success"] is True
    assert output["skipped"] is True
    assert admin_service.create_vm_calls == []
    assert session_repository.get_session("lab-session").allowed_tools == [
        ToolName.GET_AGENT_STATUS
    ]


def test_agent_create_libvirt_noops_existing_session_ignores_vm_image_selector(
    tmp_path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            vm_provider="libvirt",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "libvirt_vm_template": {
                    "image_ref": "dotnet-agent.qcow2",
                    "template_alias": "dotnet",
                }
            },
        )
    )

    class AdminServiceStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.create_vm_calls: list[dict[str, object]] = []
            self.resolve_vm_defaults_calls: list[str] = []

        def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
            self.resolve_vm_defaults_calls.append(directory_path)
            return _libvirt_vm_defaults_stub()

        def create_vm(self, **kwargs):
            self.create_vm_calls.append(kwargs)
            raise AssertionError("noop should not call create_vm")

    admin_service = AdminServiceStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config), admin_service=admin_service
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="effect-agent",
            display_name="Create agent",
            type="effect:agent:create",
            execution_backend="libvirt_vm",
            vm_name="lab-vm",
            session_id="lab-session",
            existing_agent_behavior="noop",
            vm_image="default",
        )
    ).execute()

    assert output["success"] is True
    assert output["skipped"] is True
    assert output["vm_image"] == "dotnet-agent.qcow2"
    assert output["vm_image_alias"] == "dotnet"
    assert admin_service.resolve_vm_defaults_calls == []
    assert admin_service.create_vm_calls == []


def test_agent_create_libvirt_upsert_allows_existing_session(tmp_path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            vm_provider="libvirt",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    class AdminServiceStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.create_vm_calls: list[dict[str, object]] = []

        def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
            del directory_path
            return _libvirt_vm_defaults_stub()

        def create_vm(self, **kwargs):
            self.create_vm_calls.append(kwargs)
            session_repository.upsert_session(
                SessionManifest(
                    session_id=str(kwargs["session_id"]),
                    vm_id=str(kwargs["vm_name"]),
                    vm_provider="libvirt",
                    allowed_tools=[ToolName.GET_AGENT_STATUS],
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    metadata=dict(kwargs.get("metadata") or {}),
                )
            )
            return VmActionResult(
                success=True,
                vm_name=str(kwargs["vm_name"]),
                action="create_vm",
                message="ok",
            )

    admin_service = AdminServiceStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config), admin_service=admin_service
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="effect-agent",
            display_name="Create agent",
            type="effect:agent:create",
            execution_backend="libvirt_vm",
            vm_name="lab-vm",
            session_id="lab-session",
            existing_agent_behavior="upsert",
            allowed_tools=["set_issue_plan"],
        )
    ).execute()

    assert output["success"] is True
    assert output["skipped"] is False
    assert admin_service.create_vm_calls[0]["allow_existing_session"] is True
    assert admin_service.create_vm_calls[0]["web_search_enabled"] is None

    factory.build(
        AgentCreateEffectDefinition(
            id="effect-agent-disable-search",
            display_name="Create agent",
            type="effect:agent:create",
            execution_backend="libvirt_vm",
            vm_name="lab-vm-2",
            session_id="lab-session",
            existing_agent_behavior="upsert",
            web_search_enabled=False,
        )
    ).execute()

    assert admin_service.create_vm_calls[1]["allow_existing_session"] is True
    assert admin_service.create_vm_calls[1]["web_search_enabled"] is False


def test_agent_create_libvirt_resolves_explicit_vm_image_selector(
    tmp_path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)

    class AdminServiceStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.create_vm_calls: list[dict[str, object]] = []

        def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
            del directory_path
            return _libvirt_vm_defaults_stub()

        def create_vm(self, **kwargs):
            self.create_vm_calls.append(kwargs)
            session_repository.upsert_session(
                SessionManifest(
                    session_id=str(kwargs["session_id"]),
                    vm_id=str(kwargs["vm_name"]),
                    vm_provider="libvirt",
                    allowed_tools=[ToolName.GET_AGENT_STATUS],
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    metadata=dict(kwargs.get("metadata") or {}),
                )
            )
            return VmActionResult(
                success=True,
                vm_name=str(kwargs["vm_name"]),
                action="create_vm",
                message="ok",
            )

    admin_service = AdminServiceStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config), admin_service=admin_service
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="effect-agent",
            display_name="Create agent",
            type="effect:agent:create",
            execution_backend="libvirt_vm",
            vm_name="lab-vm",
            session_id="lab-session",
            vm_image="dotnet",
        )
    ).execute()

    assert output["vm_image"] == "dotnet-agent.qcow2"
    assert output["vm_image_alias"] == "dotnet"
    assert admin_service.create_vm_calls[0]["metadata"]["libvirt_vm_template"] == {
        "image_ref": "dotnet-agent.qcow2",
        "template_alias": "dotnet",
    }
    assert session_repository.get_session("lab-session").metadata["libvirt_vm_template"] == {
        "image_ref": "dotnet-agent.qcow2",
        "template_alias": "dotnet",
    }


def test_agent_create_libvirt_resolves_bound_vm_image_selector(
    tmp_path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)

    class AdminServiceStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.create_vm_calls: list[dict[str, object]] = []

        def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
            del directory_path
            return _libvirt_vm_defaults_stub()

        def create_vm(self, **kwargs):
            self.create_vm_calls.append(kwargs)
            session_repository.upsert_session(
                SessionManifest(
                    session_id=str(kwargs["session_id"]),
                    vm_id=str(kwargs["vm_name"]),
                    vm_provider="libvirt",
                    allowed_tools=[ToolName.GET_AGENT_STATUS],
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    metadata=dict(kwargs.get("metadata") or {}),
                )
            )
            return VmActionResult(
                success=True,
                vm_name=str(kwargs["vm_name"]),
                action="create_vm",
                message="ok",
            )

    admin_service = AdminServiceStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config), admin_service=admin_service
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="effect-agent",
            display_name="Create agent",
            type="effect:agent:create",
            execution_backend="libvirt_vm",
            vm_name="lab-vm",
            session_id="lab-session",
            vm_image="${{ steps.pick.outputs.vm_image }}",
        )
    ).execute(resolved_config={"vm_image": "dotnet"})

    assert output["vm_image"] == "dotnet-agent.qcow2"
    assert output["vm_image_alias"] == "dotnet"
    assert admin_service.create_vm_calls[0]["metadata"]["libvirt_vm_template"] == {
        "image_ref": "dotnet-agent.qcow2",
        "template_alias": "dotnet",
    }
    assert session_repository.get_session("lab-session").metadata["libvirt_vm_template"] == {
        "image_ref": "dotnet-agent.qcow2",
        "template_alias": "dotnet",
    }


def test_agent_create_libvirt_uses_default_vm_image_selector_when_omitted(
    tmp_path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)

    class AdminServiceStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.create_vm_calls: list[dict[str, object]] = []

        def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
            del directory_path
            return _libvirt_vm_defaults_stub()

        def create_vm(self, **kwargs):
            self.create_vm_calls.append(kwargs)
            session_repository.upsert_session(
                SessionManifest(
                    session_id=str(kwargs["session_id"]),
                    vm_id=str(kwargs["vm_name"]),
                    vm_provider="libvirt",
                    allowed_tools=[ToolName.GET_AGENT_STATUS],
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    metadata=dict(kwargs.get("metadata") or {}),
                )
            )
            return VmActionResult(
                success=True,
                vm_name=str(kwargs["vm_name"]),
                action="create_vm",
                message="ok",
            )

    admin_service = AdminServiceStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config), admin_service=admin_service
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="effect-agent",
            display_name="Create agent",
            type="effect:agent:create",
            execution_backend="libvirt_vm",
            vm_name="lab-vm",
            session_id="lab-session",
        )
    ).execute()

    assert output["vm_image"] == "ubuntu-24.04-server-cloudimg-amd64.img"
    assert output["vm_image_alias"] == "default"
    assert admin_service.create_vm_calls[0]["metadata"]["libvirt_vm_template"] == {
        "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
        "template_alias": "default",
    }
    assert session_repository.get_session("lab-session").metadata["libvirt_vm_template"] == {
        "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
        "template_alias": "default",
    }


def test_agent_create_libvirt_upsert_preserves_existing_vm_image_when_selector_omitted(
    tmp_path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            vm_provider="libvirt",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "libvirt_vm_template": {
                    "image_ref": "dotnet-agent.qcow2",
                    "template_alias": "dotnet",
                }
            },
        )
    )

    class AdminServiceStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.create_vm_calls: list[dict[str, object]] = []

        def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
            del directory_path
            return _libvirt_vm_defaults_stub()

        def create_vm(self, **kwargs):
            self.create_vm_calls.append(kwargs)
            existing = session_repository.get_session(str(kwargs["session_id"]))
            assert existing is not None
            merged_metadata = dict(existing.metadata)
            merged_metadata.update(dict(kwargs.get("metadata") or {}))
            session_repository.upsert_session(
                existing.model_copy(update={"metadata": merged_metadata})
            )
            return VmActionResult(
                success=True,
                vm_name=str(kwargs["vm_name"]),
                action="create_vm",
                message="ok",
            )

    admin_service = AdminServiceStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config), admin_service=admin_service
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="effect-agent",
            display_name="Create agent",
            type="effect:agent:create",
            execution_backend="libvirt_vm",
            vm_name="lab-vm",
            session_id="lab-session",
            existing_agent_behavior="upsert",
            web_search_enabled=False,
            metadata={
                "workflow": {"state": "active"},
                "libvirt_vm_template": {
                    "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
                    "template_alias": "default",
                },
            },
        )
    ).execute()

    assert output["vm_image"] == "dotnet-agent.qcow2"
    assert output["vm_image_alias"] == "dotnet"
    assert "libvirt_vm_template" not in admin_service.create_vm_calls[0]["metadata"]
    assert admin_service.create_vm_calls[0]["metadata"] == {"workflow": {"state": "active"}}
    assert session_repository.get_session("lab-session").metadata["libvirt_vm_template"] == {
        "image_ref": "dotnet-agent.qcow2",
        "template_alias": "dotnet",
    }
    assert session_repository.get_session("lab-session").metadata["workflow"] == {
        "state": "active"
    }


def test_agent_create_libvirt_upsert_replaces_existing_vm_image_when_selector_provided(
    tmp_path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            vm_provider="libvirt",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "libvirt_vm_template": {
                    "image_ref": "dotnet-agent.qcow2",
                    "template_alias": "dotnet",
                }
            },
        )
    )

    class AdminServiceStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.create_vm_calls: list[dict[str, object]] = []

        def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
            del directory_path
            return _libvirt_vm_defaults_stub()

        def create_vm(self, **kwargs):
            self.create_vm_calls.append(kwargs)
            existing = session_repository.get_session(str(kwargs["session_id"]))
            assert existing is not None
            merged_metadata = dict(existing.metadata)
            merged_metadata.update(dict(kwargs.get("metadata") or {}))
            session_repository.upsert_session(
                existing.model_copy(update={"metadata": merged_metadata})
            )
            return VmActionResult(
                success=True,
                vm_name=str(kwargs["vm_name"]),
                action="create_vm",
                message="ok",
            )

    admin_service = AdminServiceStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config), admin_service=admin_service
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="effect-agent",
            display_name="Create agent",
            type="effect:agent:create",
            execution_backend="libvirt_vm",
            vm_name="lab-vm",
            session_id="lab-session",
            existing_agent_behavior="upsert",
            vm_image="default",
        )
    ).execute()

    expected_template = {
        "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
        "template_alias": "default",
    }
    assert output["vm_image"] == expected_template["image_ref"]
    assert output["vm_image_alias"] == expected_template["template_alias"]
    assert admin_service.create_vm_calls[0]["metadata"]["libvirt_vm_template"] == expected_template
    assert session_repository.get_session("lab-session").metadata["libvirt_vm_template"] == (
        expected_template
    )


def test_agent_create_libvirt_rejects_unknown_vm_image_selector_before_create(
    tmp_path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)

    class AdminServiceStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.create_vm_calls: list[dict[str, object]] = []

        def resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
            del directory_path
            return _libvirt_vm_defaults_stub()

        def create_vm(self, **kwargs):
            self.create_vm_calls.append(kwargs)
            return VmActionResult(
                success=True,
                vm_name=str(kwargs["vm_name"]),
                action="create_vm",
                message="ok",
            )

    admin_service = AdminServiceStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config), admin_service=admin_service
    )

    with pytest.raises(
        ValueError,
        match=(
            r"unknown libvirt VM template selector 'missing'; configured aliases: default, dotnet; "
            r"default template: 'default'"
        ),
    ):
        factory.build(
            AgentCreateEffectDefinition(
                id="effect-agent",
                display_name="Create agent",
                type="effect:agent:create",
                execution_backend="libvirt_vm",
                vm_name="lab-vm",
                session_id="lab-session",
                vm_image="missing",
            )
        ).execute()

    assert admin_service.create_vm_calls == []
    assert session_repository.get_session("lab-session") is None


def test_vm_clone_repo_provider_passes_through_repo_outputs() -> None:
    effect = VmCloneRepoEffectDefinition(
        id="effect-clone",
        display_name="Clone repo",
        type="effect:vm:clone-repo",
        session_id="session-42",
        repo_source="/tmp/repo",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )

    def fake_clone_repo_into_vm(**kwargs):
        admin_service.clone_repo_into_vm_calls.append(kwargs)
        return VmActionResult(
            success=True,
            vm_name=str(kwargs["vm_name"]),
            action="clone_repo_into_vm",
            message="clone ok",
            target_dir="/home/agent/RepoName",
            guest_repo_name="RepoName",
            remote_url="ssh://agent-sandbox-git-host/home/git/remotes/RepoName.git",
        )

    admin_service.clone_repo_into_vm = fake_clone_repo_into_vm  # type: ignore[method-assign]
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect, directory_path="root/team-a")
    output = provider.execute()

    assert output == {
        "success": True,
        "message": "clone ok",
        "session_id": "session-42",
        "vm_name": "vm-42",
        "target_dir": "/home/agent/RepoName",
        "guest_repo_name": "RepoName",
        "remote_url": "ssh://agent-sandbox-git-host/home/git/remotes/RepoName.git",
        "pr_notes_ref": "refs/notes/agent-pr/92e76c732d82",
    }
    assert admin_service.clone_repo_into_vm_calls[0] == {
        "vm_name": "vm-42",
        "session_id": "session-42",
        "repo_source": "/tmp/repo",
        "clone_branch": None,
        "vm_user": "agent",
        "directory_path": "root/team-a",
    }


def test_agent_clone_repo_libvirt_uses_explicit_git_author_identity() -> None:
    effect = AgentCloneRepoEffectDefinition(
        id="agent-clone",
        display_name="Clone repo",
        type="effect:agent:clone-repo",
        session_id="session-42",
        repo_source="/tmp/repo",
        git_author_name="Ada Lovelace",
        git_author_email="ada@example.com",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    factory.build(effect, directory_path="root/team-a").execute()

    assert admin_service.clone_repo_into_vm_calls[0]["git_author_name"] == "Ada Lovelace"
    assert admin_service.clone_repo_into_vm_calls[0]["git_author_email"] == "ada@example.com"
    assert admin_service.clone_repo_into_vm_calls[0]["git_committer_name"] is None
    assert admin_service.clone_repo_into_vm_calls[0]["git_committer_email"] is None


def test_agent_clone_repo_libvirt_uses_explicit_git_committer_identity() -> None:
    effect = AgentCloneRepoEffectDefinition(
        id="agent-clone",
        display_name="Clone repo",
        type="effect:agent:clone-repo",
        session_id="session-42",
        repo_source="/tmp/repo",
        git_author_name="Ada Lovelace",
        git_author_email="ada@example.com",
        git_committer_name="Truman Bot",
        git_committer_email="truman@example.com",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    factory.build(effect, directory_path="root/team-a").execute()

    assert admin_service.clone_repo_into_vm_calls[0]["git_author_name"] == "Ada Lovelace"
    assert admin_service.clone_repo_into_vm_calls[0]["git_author_email"] == "ada@example.com"
    assert admin_service.clone_repo_into_vm_calls[0]["git_committer_name"] == "Truman Bot"
    assert admin_service.clone_repo_into_vm_calls[0]["git_committer_email"] == "truman@example.com"


def test_agent_clone_repo_requires_complete_git_author_identity() -> None:
    effect = AgentCloneRepoEffectDefinition(
        id="agent-clone",
        display_name="Clone repo",
        type="effect:agent:clone-repo",
        session_id="session-42",
        repo_source="/tmp/repo",
        git_author_name="Ada Lovelace",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    with pytest.raises(
        ValueError,
        match="git_author_name and git_author_email must both be set",
    ):
        factory.build(effect, directory_path="root/team-a").execute()

    assert admin_service.clone_repo_into_vm_calls == []


def test_agent_clone_repo_requires_complete_git_committer_identity() -> None:
    effect = AgentCloneRepoEffectDefinition(
        id="agent-clone",
        display_name="Clone repo",
        type="effect:agent:clone-repo",
        session_id="session-42",
        repo_source="/tmp/repo",
        git_committer_name="Truman Bot",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    with pytest.raises(
        ValueError,
        match="git_committer_name and git_committer_email must both be set",
    ):
        factory.build(effect, directory_path="root/team-a").execute()

    assert admin_service.clone_repo_into_vm_calls == []


def test_agent_clone_repo_libvirt_does_not_use_session_issue_owner_metadata() -> None:
    effect = AgentCloneRepoEffectDefinition(
        id="agent-clone",
        display_name="Clone repo",
        type="effect:agent:clone-repo",
        session_id="session-42",
        repo_source="/tmp/repo",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    admin_service._sessions["session-42"].metadata["issue_owner"] = {
        "display_name": "Ada Lovelace",
        "email": "ada@example.com",
    }
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    factory.build(effect, directory_path="root/team-a").execute()

    assert admin_service.clone_repo_into_vm_calls[0]["git_author_name"] is None
    assert admin_service.clone_repo_into_vm_calls[0]["git_author_email"] is None
    assert admin_service.clone_repo_into_vm_calls[0]["git_committer_name"] is None
    assert admin_service.clone_repo_into_vm_calls[0]["git_committer_email"] is None


def test_vm_bootstrap_python_provider_passes_expected_arguments() -> None:
    effect = VmBootstrapPythonEffectDefinition(
        id="effect-python",
        display_name="Bootstrap Python",
        type="effect:vm:bootstrap-python",
        session_id="session-42",
        host_checkout="/tmp/repo",
        target_dir="/home/agent/RepoName",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect, directory_path="root/team-a")
    output = provider.execute()

    assert output == {
        "success": True,
        "message": "ok",
        "session_id": "session-42",
        "vm_name": "vm-42",
    }
    assert admin_service.bootstrap_python_in_vm_calls[0] == {
        "vm_name": "vm-42",
        "session_id": "session-42",
        "host_checkout": "/tmp/repo",
        "target_dir": "/home/agent/RepoName",
        "vm_user": "agent",
    }


def test_vm_bootstrap_frontend_provider_passes_expected_arguments() -> None:
    effect = VmBootstrapFrontendEffectDefinition(
        id="effect-frontend",
        display_name="Bootstrap Frontend",
        type="effect:vm:bootstrap-frontend",
        session_id="session-42",
        host_checkout="/tmp/checkout",
        install_roots=["/tmp/checkout/Src/Clients"],
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect, directory_path="root/team-a")
    output = provider.execute()

    assert output == {
        "success": True,
        "message": "ok",
        "session_id": "session-42",
        "vm_name": "vm-42",
    }
    assert admin_service.bootstrap_frontend_in_vm_calls[0] == {
        "vm_name": "vm-42",
        "session_id": "session-42",
        "host_checkout": "/tmp/checkout",
        "install_roots": ["/tmp/checkout/Src/Clients"],
        "vm_user": "agent",
    }


def test_agent_prompt_provider_emits_thread_id() -> None:
    effect = AgentPromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:prompt",
        prompt="continue",
        session_id="session-42",
        resume_thread_id="thread-123",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect)
    output = provider.execute()

    assert output["dispatch_id"].startswith("dispatch-effect-prompt-")
    assert output["thread_id"] == "thread-123"
    assert output["success"] is True
    assert "output_mode" not in output
    assert admin_service.resume_calls[0]["target"] == "session-42"
    assert admin_service.resume_calls[0]["dispatch_id"] == output["dispatch_id"]
    assert admin_service.resume_calls[0]["resume_thread_id"] == "thread-123"


def test_agent_prompt_provider_uses_resume_thread_metadata_key() -> None:
    effect = AgentPromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:prompt",
        prompt="continue",
        session_id="session-42",
        resume_thread_metadata_key="teams:chat-1:agent_thread_id",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    session = admin_service._sessions["session-42"]
    admin_service._sessions["session-42"] = session.model_copy(
        update={
            "metadata": {"agent_thread_ids": {"teams:chat-1:agent_thread_id": "thread-existing"}}
        }
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    output = factory.build(effect).execute()

    assert output["thread_id"] == "thread-existing"
    assert admin_service.resume_calls[0]["resume_thread_id"] == "thread-existing"
    assert admin_service.resume_calls[0]["persist_thread_id"] is False
    updated = admin_service._sessions["session-42"]
    assert updated.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"] == (
        "thread-existing"
    )
    assert "teams:chat-1:agent_thread_id" not in updated.metadata


def test_factory_supports_new_provider_types_without_events_model_edit() -> None:
    effect = WorkflowNodeEnvelope(
        id="custom-1",
        display_name="Custom",
        type="effect:custom:test",
        config={"greeting": "hello"},
    )
    registry = EffectProviderRegistry(initial={})
    registry.register("effect:custom:test", _CustomSchemaProvider)  # type: ignore[arg-type]
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(),
        registry=registry,
        admin_service=admin_service,
    )

    provider = factory.build(effect)

    assert isinstance(provider, _CustomSchemaProvider)
    assert provider.effect_definition is not None
    assert provider.effect_definition.config == {"greeting": "hello"}


def test_agent_enqueue_prompt_provider_dispatches_immediately_when_idle() -> None:
    effect = AgentEnqueuePromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:enqueue-prompt",
        prompt="continue",
        session_id="session-42",
        queue_key="chat:thread-ui-1:msg-1",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    admin_service.list_vms = lambda include_inactive=True, vm_user="agent": []  # type: ignore[attr-defined]
    persisted: list[dict[str, object]] = []
    admin_service.vm_agent_admin = SimpleNamespace(  # type: ignore[attr-defined]
        persist_chat_assistant_agent_thread_id=lambda **kwargs: persisted.append(kwargs)
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect)
    output = provider.execute()

    assert output["dispatch_id"].startswith("dispatch-effect-prompt-")
    assert output["queued"] is False
    assert admin_service.resume_calls[0]["target"] == "session-42"
    assert admin_service.resume_calls[0]["resume_thread_id"] is None
    assert persisted == [
        {
            "session_id": "session-42",
            "agent_thread_id": "thread-new-123",
            "backend": "libvirt_vm",
            "assistant_scope": "admin_chat",
            "queue_key": "chat:thread-ui-1:msg-1",
        }
    ]


def test_agent_enqueue_prompt_provider_uses_resume_thread_metadata_key() -> None:
    effect = AgentEnqueuePromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:enqueue-prompt",
        prompt="continue",
        session_id="session-42",
        resume_thread_metadata_key="teams:chat-1:agent_thread_id",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    session = admin_service._sessions["session-42"]
    admin_service._sessions["session-42"] = session.model_copy(
        update={
            "metadata": {"agent_thread_ids": {"teams:chat-1:agent_thread_id": "thread-existing"}}
        }
    )
    admin_service.list_vms = lambda include_inactive=True, vm_user="agent": []  # type: ignore[attr-defined]
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    output = factory.build(effect).execute()

    assert output["thread_id"] == "thread-existing"
    assert admin_service.resume_calls[0]["resume_thread_id"] == "thread-existing"
    assert admin_service.resume_calls[0]["persist_thread_id"] is False
    updated = admin_service._sessions["session-42"]
    assert updated.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"] == (
        "thread-existing"
    )
    assert "teams:chat-1:agent_thread_id" not in updated.metadata


def test_agent_enqueue_prompt_provider_explicit_resume_thread_overrides_metadata_key() -> None:
    effect = AgentEnqueuePromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:enqueue-prompt",
        prompt="continue",
        session_id="session-42",
        resume_thread_id="thread-explicit",
        resume_thread_metadata_key="teams:chat-1:agent_thread_id",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    session = admin_service._sessions["session-42"]
    admin_service._sessions["session-42"] = session.model_copy(
        update={
            "metadata": {"agent_thread_ids": {"teams:chat-1:agent_thread_id": "thread-existing"}}
        }
    )
    admin_service.list_vms = lambda include_inactive=True, vm_user="agent": []  # type: ignore[attr-defined]
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    output = factory.build(effect).execute()

    assert output["thread_id"] == "thread-explicit"
    assert admin_service.resume_calls[0]["resume_thread_id"] == "thread-explicit"
    assert admin_service.resume_calls[0]["persist_thread_id"] is False
    updated = admin_service._sessions["session-42"]
    assert updated.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"] == (
        "thread-explicit"
    )
    assert "teams:chat-1:agent_thread_id" not in updated.metadata


def test_agent_enqueue_prompt_provider_uses_resume_thread_metadata_collection_override() -> None:
    effect = AgentEnqueuePromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:enqueue-prompt",
        prompt="continue",
        session_id="session-42",
        resume_thread_metadata_key="teams:chat-1:agent_thread_id",
        resume_thread_metadata_collection="team_threads",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    session = admin_service._sessions["session-42"]
    admin_service._sessions["session-42"] = session.model_copy(
        update={
            "metadata": {
                "agent_thread_ids": {"teams:chat-1:agent_thread_id": "thread-default"},
                "team_threads": {"teams:chat-1:agent_thread_id": "thread-override"},
            }
        }
    )
    admin_service.list_vms = lambda include_inactive=True, vm_user="agent": []  # type: ignore[attr-defined]
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    output = factory.build(effect).execute()

    assert output["thread_id"] == "thread-override"
    assert admin_service.resume_calls[0]["resume_thread_id"] == "thread-override"
    updated = admin_service._sessions["session-42"]
    assert updated.metadata["team_threads"]["teams:chat-1:agent_thread_id"] == ("thread-override")
    assert updated.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"] == (
        "thread-default"
    )


def test_agent_enqueue_prompt_provider_ignores_legacy_top_level_metadata_key() -> None:
    effect = AgentEnqueuePromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:enqueue-prompt",
        prompt="continue",
        session_id="session-42",
        resume_thread_metadata_key="teams:chat-1:agent_thread_id",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    session = admin_service._sessions["session-42"]
    admin_service._sessions["session-42"] = session.model_copy(
        update={"metadata": {"teams:chat-1:agent_thread_id": "thread-legacy"}}
    )
    admin_service.list_vms = lambda include_inactive=True, vm_user="agent": []  # type: ignore[attr-defined]
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    output = factory.build(effect).execute()

    assert output["thread_id"] == "thread-new-123"
    assert admin_service.resume_calls[0]["resume_thread_id"] is None
    updated = admin_service._sessions["session-42"]
    assert updated.metadata["teams:chat-1:agent_thread_id"] == "thread-legacy"
    assert updated.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"] == (
        "thread-new-123"
    )


def test_agent_enqueue_prompt_provider_uses_container_backend_for_container_session(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
        list_vms=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("container enqueue must not list libvirt VMs")
        ),
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentEnqueuePromptEffectDefinition(
            id="agent-enqueue",
            display_name="Enqueue",
            type="effect:agent:enqueue-prompt",
            target="session-1",
            prompt="check teams",
        )
    ).execute()

    assert output["execution_backend"] == "container_worker_vm"
    assert output["queued"] is False
    assert output["output_mode"] == "on_completion"
    assert output["session_id"] == "session-1"
    assert output["vm_name"] == "truman"
    assert backend.started_specs[0].session_id == "session-1"
    command = str(backend.started_specs[0].command)
    assert "check teams" in command
    assert "--json" in command
    assert "agent_sandbox_codex_json_to_log" in command
    assert "thread.started" in command
    assert "item.completed" in command
    assert "agent_sandbox_latest_codex_session_entry" not in command


def test_agent_enqueue_prompt_infers_claude_provider_for_anthropic_container_session(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        model_provider_mode="anthropic",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model=None,
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
        list_vms=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("container enqueue must not list libvirt VMs")
        ),
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentEnqueuePromptEffectDefinition(
            id="agent-enqueue",
            display_name="Enqueue",
            type="effect:agent:enqueue-prompt",
            target="session-1",
            prompt="check teams",
        )
    ).execute()

    assert output["execution_backend"] == "container_worker_vm"
    assert output["queued"] is False
    command = str(backend.started_specs[0].command)
    assert "claude -p" in command
    assert "--model claude-sonnet-4-6" in command
    assert "--model gpt-5.4" not in command


def test_agent_enqueue_prompt_provider_rejects_expired_container_session_before_backend_start(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
        list_vms=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("expired container enqueue must not list libvirt VMs")
        ),
    )
    backend_calls: list[object] = []
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend_calls.append((context, config)) or _FakeContainerBackend(),
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(
        ValueError,
        match=r"session expired: session_id=session-1 expires_at=.*Z",
    ):
        factory.build(
            AgentEnqueuePromptEffectDefinition(
                id="agent-enqueue",
                display_name="Enqueue",
                type="effect:agent:enqueue-prompt",
                target="session-1",
                prompt="check teams",
            )
        ).execute()

    assert backend_calls == []
    assert not (tmp_path / "queued-agent-prompts.json").exists()


def test_container_prompt_command_does_not_resume_synthetic_dispatch_id() -> None:
    command = container_prompt_command(
        prompt="check teams",
        model="gpt-5.4-mini",
        resume_thread_id="dispatch-effect-agent-enqueue-prompt-1-deadbeef",
    )

    assert "exec resume dispatch-effect-agent-enqueue-prompt-1-deadbeef" not in command
    assert "codex exec --json" in command
    assert "-s danger-full-access" in command
    assert "-C /home/agent" in command
    assert 'web_search="disabled"' in command
    assert "cd /home/agent" in command


def test_container_prompt_command_does_not_resume_synthetic_chat_thread_id() -> None:
    command = container_prompt_command(
        prompt="check admin chat",
        model="gpt-5.4-mini",
        resume_thread_id="chat-thread-alice-123",
    )

    assert "exec resume chat-thread-alice-123" not in command
    assert "codex exec --json" in command
    assert "-s danger-full-access" in command
    assert "-C /home/agent" in command


def test_container_prompt_command_uses_resume_last_for_same_context_without_thread() -> None:
    command = container_prompt_command(
        prompt="check teams",
        model="gpt-5.4-mini",
        resume_thread_id=None,
        resume_mode="same_context",
    )

    assert "codex exec resume --last" in command
    assert "--json" in command
    assert "-s danger-full-access" not in command
    assert "-C /home/agent" not in command


def test_container_prompt_command_uses_fresh_context_for_new_context_without_thread() -> None:
    command = container_prompt_command(
        prompt="check teams",
        model="gpt-5.4-mini",
        resume_thread_id=None,
        resume_mode="new_context",
    )

    assert "codex exec resume --last" not in command
    assert "codex exec --json" in command
    assert "-s danger-full-access" in command
    assert "-C /home/agent" in command


def test_container_prompt_command_supports_claude_provider() -> None:
    command = container_prompt_command(
        prompt="check teams",
        model="claude-sonnet-4-6",
        resume_thread_id=None,
        agent_provider="claude",
        mcp_gateway_url="http://gateway.test:8000/mcp?session_id=session-1",
    )

    assert "claude -p" in command
    assert "--dangerously-skip-permissions" in command
    assert "--model claude-sonnet-4-6" in command
    assert "--output-format stream-json" in command
    assert "--verbose" in command
    assert "--mcp-config /home/agent/.claude/mcp-config.json" in command
    assert "printf '%s' 'check teams' | claude -p" in command
    assert "--mcp-config /home/agent/.claude/mcp-config.json 'check teams'" not in command
    assert "agent_sandbox_claude_json_to_log" in command
    assert "http://gateway.test:8000/mcp?session_id=session-1" in command


def test_container_prompt_command_supports_claude_resume_modes() -> None:
    resume_thread = container_prompt_command(
        prompt="check teams",
        model="claude-sonnet-4-6",
        resume_thread_id="claude-session-1",
        agent_provider="claude",
    )
    resume_last = container_prompt_command(
        prompt="check teams",
        model="claude-sonnet-4-6",
        resume_thread_id=None,
        resume_mode="same_context",
        agent_provider="claude",
    )

    assert "printf '%s' 'check teams' | claude --resume claude-session-1 -p" in resume_thread
    assert "printf '%s' 'check teams' | claude --continue -p" in resume_last


def test_container_session_files_do_not_leak_azure_openai_settings() -> None:
    config = GatewayConfig(
        runtime_dir=Path("/tmp/runtime"),
        audit_log_path=Path("/tmp/runtime/audit.jsonl"),
        session_store_path=Path("/tmp/runtime/sessions.json"),
        approval_store_path=Path("/tmp/runtime/approvals.json"),
        draft_store_path=Path("/tmp/runtime/drafts.json"),
        email_store_path=Path("/tmp/runtime/emails.json"),
        discord_command_store_path=Path("/tmp/runtime/discord.json"),
        gateway_url="http://relay.test:8000/mcp",
        model_provider_mode="azure_openai",
        azure_openai_endpoint="https://example-resource.openai.azure.com/",
        azure_openai_api_key_env="AZURE_OPENAI_API_KEY",
        azure_openai_model_deployments={"gpt-5.4": "deployment-gpt-5-4"},
    )

    files = build_container_session_files(
        config=config,
        session_id="session-1",
        model="gpt-5.4",
        web_search_enabled=False,
        dotnet=False,
        nuget=False,
        playwright=False,
    )
    by_path = {entry["destination_path"]: entry["content"] for entry in files}
    combined_content = "\n".join(by_path.values())

    assert by_path["/home/agent/.codex/config.toml"] == (
        'model = "gpt-5.4"\n'
        'model_provider = "openai"\n'
        'model_reasoning_effort = "high"\n'
        'personality = "pragmatic"\n'
        'web_search = "disabled"\n'
        'openai_base_url = "http://relay.test:11434/v1/session/session-1"\n'
        "\n"
        "[mcp_servers.agent_sandbox]\n"
        'url = "http://relay.test:8000/mcp?session_id=session-1"\n'
        "enabled = true\n"
        "\n"
        '[projects."/home/agent"]\n'
        'trust_level = "trusted"\n'
    )
    assert "OPENAI_API_KEY=\"${OPENAI_API_KEY:-relay-placeholder}\"" in by_path[
        "/home/agent/.profile"
    ]
    assert "azure_openai_endpoint" not in combined_content
    assert "azure_openai_api_key_env" not in combined_content
    assert "azure_openai_model_deployments" not in combined_content
    assert "https://example-resource.openai.azure.com" not in combined_content
    assert "AZURE_OPENAI_API_KEY" not in combined_content
    assert "deployment-gpt-5-4" not in combined_content


def test_container_session_files_include_claude_provider_config() -> None:
    config = GatewayConfig(
        runtime_dir=Path("/tmp/runtime"),
        audit_log_path=Path("/tmp/runtime/audit.jsonl"),
        session_store_path=Path("/tmp/runtime/sessions.json"),
        approval_store_path=Path("/tmp/runtime/approvals.json"),
        draft_store_path=Path("/tmp/runtime/drafts.json"),
        email_store_path=Path("/tmp/runtime/emails.json"),
        discord_command_store_path=Path("/tmp/runtime/discord.json"),
        gateway_url="http://relay.test:8000/mcp",
        model_provider_mode="anthropic",
    )

    files = build_container_session_files(
        config=config,
        session_id="session-1",
        model=None,
        agent_provider=None,
        web_search_enabled=False,
        dotnet=False,
        nuget=False,
        playwright=False,
    )
    by_path = {entry["destination_path"]: entry["content"] for entry in files}

    assert "AGENT_PROVIDER=claude\n" in by_path["/home/agent/.agent-sandbox/session.env"]
    assert (
        "ANTHROPIC_BASE_URL=http://relay.test:11434/session/session-1\n"
        in by_path["/home/agent/.agent-sandbox/session.env"]
    )
    assert "model = \"claude-sonnet-4-6\"" in by_path["/home/agent/.codex/config.toml"]
    assert by_path["/home/agent/.claude/settings.json"] == (
        "{\n"
        '  "attribution": {\n'
        '    "commit": ""\n'
        "  },\n"
        '  "env": {\n'
        '    "ANTHROPIC_AUTH_TOKEN": "relay-placeholder",\n'
        '    "ANTHROPIC_BASE_URL": "http://relay.test:11434/session/session-1",\n'
        '    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",\n'
        '    "DISABLE_TELEMETRY": "1"\n'
        "  }\n"
        "}\n"
    )
    assert by_path["/home/agent/.claude/mcp-config.json"] == (
        "{\n"
        '  "mcpServers": {\n'
        '    "agent_sandbox": {\n'
        '      "tools": [\n'
        '        "*"\n'
        "      ],\n"
        '      "type": "http",\n'
        '      "url": "http://relay.test:8000/mcp?session_id=session-1"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )


def test_container_prompt_uses_claude_default_model_for_claude_session(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        model_provider_mode="anthropic",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="claude-session",
            vm_id=None,
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model=None,
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "truman",
            },
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_prompt.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentPromptEffectDefinition(
            id="agent-prompt",
            display_name="Prompt",
            type="effect:agent:prompt",
            session_id="claude-session",
            prompt="continue",
        )
    ).execute()

    assert output["execution_backend"] == "container_worker_vm"
    command = str(backend.started_specs[0].command)
    assert "claude -p" in command
    assert "--model claude-sonnet-4-6" in command
    assert "--model gpt-5.4" not in command


def test_agent_create_container_infers_claude_provider_for_anthropic_config(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        model_provider_mode="anthropic",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="claude-session",
        )
    ).execute()

    manifest = session_repository.get_session("claude-session")
    assert manifest is not None
    assert output["execution_backend"] == "container_worker_vm"
    assert manifest.metadata["agent_provider"] == "claude"
    bootstrap_files = backend.written_files[0]["files"]
    bootstrap_by_path = {entry["destination_path"]: entry for entry in bootstrap_files}
    assert (
        "AGENT_PROVIDER=claude\n"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )


def test_claude_json_log_filter_handles_stream_json_events() -> None:
    events = [
        {
            "type": "system",
            "session_id": "019de31a-8393-71d2-8990-e5f4fb0a4df2",
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "I am checking the repo now."}]
            },
        },
        {"type": "result", "result": "Done."},
    ]
    completed = subprocess.run(
        ["python3", "-c", _claude_json_log_filter_script()],
        input="\n".join(json.dumps(event) for event in events) + "\n",
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == (
        "session id: 019de31a-8393-71d2-8990-e5f4fb0a4df2\n"
        "I am checking the repo now.\n"
        "Done.\n"
    )


def test_codex_json_log_filter_handles_current_exec_event_stream() -> None:
    events = [
        {"type": "session_meta", "payload": {"id": "019de31a-8393-71d2-8990-e5f4fb0a4df2"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": "I am checking the repo now.",
                "phase": "commentary",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "I am checking the repo now.",
                    }
                ],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Done."}],
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "last_agent_message": "Done.",
            },
        },
    ]
    completed = subprocess.run(
        ["python3", "-c", _codex_json_log_filter_script()],
        input="\n".join(json.dumps(event) for event in events) + "\n",
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == (
        "session id: 019de31a-8393-71d2-8990-e5f4fb0a4df2\nI am checking the repo now.\nDone.\n"
    )


def test_container_thread_id_from_logs_parses_json_filter_output() -> None:
    logs = "planning\nSession ID: 019de31a-8393-71d2-8990-e5f4fb0a4df2\n"

    assert container_thread_id_from_logs(logs) == "019de31a-8393-71d2-8990-e5f4fb0a4df2"


def test_agent_enqueue_prompt_provider_queues_when_container_session_is_busy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
        list_vms=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("container enqueue must not list libvirt VMs")
        ),
    )
    backend = _FakeContainerBackend()
    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.RUNNING,
        message=None,
        metadata={},
    )
    backend.repository = SimpleNamespace(
        list_active_by_session=lambda session_id, active_statuses: [
            ContainerWorkerExecutionRecord(
                execution_id="agent-session-1-old",
                session_id=session_id,
                worker_job_id="job-1",
                worker_url="http://worker.test",
                target_name="agent-session-1-old",
                last_status=next(iter(active_statuses)),
            )
        ],
        read_all=lambda: (_ for _ in ()).throw(
            AssertionError("container enqueue should not scan all execution mappings")
        ),
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentEnqueuePromptEffectDefinition(
            id="agent-enqueue",
            display_name="Enqueue",
            type="effect:agent:enqueue-prompt",
            target="session-1",
            prompt="check teams",
            queue_key="chat:thread-1:msg-1",
            resume_thread_metadata_key="teams:chat-1:agent_thread_id",
        )
    ).execute()

    assert output["execution_backend"] == "container_worker_vm"
    assert output["queued"] is True
    assert output["queue_key"] == "chat:thread-1:msg-1"
    assert backend.started_specs == []
    queued_payload = (tmp_path / "queued-agent-prompts.json").read_text(encoding="utf-8")
    assert "check teams" in queued_payload
    assert "chat:thread-1:msg-1" in queued_payload
    assert "teams:chat-1:agent_thread_id" in queued_payload


def test_agent_enqueue_prompt_provider_caps_recreated_busy_container_prompt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        agent_prompt_queue_max_retries=1,
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
        list_vms=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("container enqueue must not list libvirt VMs")
        ),
    )
    backend = _FakeContainerBackend()
    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.RUNNING,
        message=None,
        metadata={},
    )
    backend.repository = SimpleNamespace(
        list_active_by_session=lambda session_id, active_statuses: [
            ContainerWorkerExecutionRecord(
                execution_id="agent-session-1-old",
                session_id=session_id,
                worker_job_id="job-1",
                worker_url="http://worker.test",
                target_name="agent-session-1-old",
                last_status=next(iter(active_statuses)),
            )
        ],
        read_all=lambda: (_ for _ in ()).throw(
            AssertionError("container enqueue should not scan all execution mappings")
        ),
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )
    effect = AgentEnqueuePromptEffectDefinition(
        id="agent-enqueue",
        display_name="Enqueue",
        type="effect:agent:enqueue-prompt",
        target="session-1",
        prompt="check teams",
        queue_key="chat:thread-1:msg-1",
    )

    output = factory.build(effect).execute()
    assert output["queued"] is True
    assert output["retry_count"] == 1
    assert output["max_retries"] == 1
    AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").delete(
        "chat:thread-1:msg-1"
    )

    with pytest.raises(EffectProviderExecutionError) as exc_info:
        factory.build(effect).execute()

    assert exc_info.value.outputs["failed_phase"] == "retry_limit"
    assert exc_info.value.outputs["queue_key"] == "chat:thread-1:msg-1"
    assert exc_info.value.outputs["queued"] is False
    assert exc_info.value.outputs["retry_count"] == 2
    assert exc_info.value.outputs["max_retries"] == 1
    assert AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").get(
        "chat:thread-1:msg-1"
    ) is None


def test_agent_enqueue_prompt_keeps_distinct_busy_messages_for_same_metadata_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
        list_vms=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("container enqueue must not list libvirt VMs")
        ),
    )
    backend = _FakeContainerBackend()
    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.RUNNING,
        message=None,
        metadata={},
    )
    backend.repository = SimpleNamespace(
        list_active_by_session=lambda session_id, active_statuses: [
            ContainerWorkerExecutionRecord(
                execution_id="agent-session-1-old",
                session_id=session_id,
                worker_job_id="job-1",
                worker_url="http://worker.test",
                target_name="agent-session-1-old",
                last_status=next(iter(active_statuses)),
            )
        ],
        read_all=lambda: (_ for _ in ()).throw(
            AssertionError("container enqueue should not scan all execution mappings")
        ),
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    for message_id in ("msg-1", "msg-2"):
        output = factory.build(
            AgentEnqueuePromptEffectDefinition(
                id="agent-enqueue",
                display_name="Enqueue",
                type="effect:agent:enqueue-prompt",
                target="session-1",
                prompt=f"check teams {message_id}",
                queue_key=f"teams:chat-1:{message_id}",
                resume_thread_metadata_key="teams:chat-1:agent_thread_id",
            )
        ).execute()
        assert output["queued"] is True

    queued = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").read_all()
    assert sorted(queued) == ["teams:chat-1:msg-1", "teams:chat-1:msg-2"]
    assert {record.resume_thread_metadata_key for record in queued.values()} == {
        "teams:chat-1:agent_thread_id"
    }


def test_chat_assistant_ensure_session_uses_event_capability_mode(tmp_path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="container_worker_vm")

    class FakeVmAgentAdmin:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def resolve_chat_assistant_session(self, **kwargs):
            self.calls.append(kwargs)
            settings = kwargs["settings"]
            return {
                "session_id": "assistant-session",
                "agent_thread_id": "assistant-thread",
                "vm_name": "assistant-session",
                "execution_backend": kwargs["backend"],
                "capability_profile": "read-only" if settings.get("read_only") else "full-admin",
            }

    vm_agent_admin = FakeVmAgentAdmin()
    admin_service = SimpleNamespace(
        config=config,
        vm_agent_admin=vm_agent_admin,
        read_assistant_settings=lambda: {
            "enabled": True,
            "read_only": False,
            "default_model": "gpt-test",
            "default_profile": "full-admin",
        },
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        ChatAssistantEnsureSessionEffectDefinition(
            id="chat-session",
            display_name="Resolve chat assistant",
            type="effect:chat-assistant:ensure-session",
            user_id="alice",
            thread_id="thread-1",
            user_message_id="msg-1",
            content="hello",
            backend="container_worker_vm",
            assistant_capability_mode="read-only",
        )
    ).execute()

    assert vm_agent_admin.calls[0]["settings"]["read_only"] is True
    assert vm_agent_admin.calls[0]["settings"]["default_profile"] == "read-only"
    assert output["assistant_capability_mode"] == "read-only"
    assert output["capability_profile"] == "read-only"


def test_chat_assistant_ensure_session_resume_thread_id_flows_into_enqueue_prompt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="container_worker_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-session",
            vm_provider="container_worker_vm",
            vm_provider_id="assistant-session",
            directory_path="root",
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "assistant-session",
            },
        )
    )

    class FakeVmAgentAdmin:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def resolve_chat_assistant_session(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "session_id": "assistant-session",
                "agent_thread_id": "assistant-thread",
                "vm_name": "assistant-session",
                "execution_backend": kwargs["backend"],
                "capability_profile": "read-only",
            }

    backend = _FakeContainerBackend()
    backend.start_error = RuntimeError("worker unavailable")
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )
    admin_service = SimpleNamespace(
        config=config,
        vm_agent_admin=FakeVmAgentAdmin(),
        read_assistant_settings=lambda: {
            "enabled": True,
            "read_only": False,
            "default_model": "gpt-test",
            "default_profile": "full-admin",
        },
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
        list_vms=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("container enqueue must not list libvirt VMs")
        ),
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    ensure_output = factory.build(
        ChatAssistantEnsureSessionEffectDefinition(
            id="chat-session",
            display_name="Resolve chat assistant",
            type="effect:chat-assistant:ensure-session",
            user_id="alice",
            thread_id="thread-1",
            user_message_id="msg-1",
            content="hello",
            backend="container_worker_vm",
            assistant_capability_mode="read-only",
        )
    ).execute()

    with pytest.raises(RuntimeError, match="queued prompt for retry"):
        factory.build(
            AgentEnqueuePromptEffectDefinition(
                id="agent-enqueue",
                display_name="Enqueue",
                type="effect:agent:enqueue-prompt",
                target="assistant-session",
                session_id="assistant-session",
                prompt="check teams",
                vm_name="assistant-session",
                resume_thread_id=ensure_output["agent_thread_id"],
                queue_key=f"chat:{ensure_output['thread_id']}:{ensure_output['user_message_id']}",
            )
        ).execute()

    queued = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").get(
        f"chat:{ensure_output['thread_id']}:{ensure_output['user_message_id']}"
    )
    assert queued is not None
    assert queued.resume_thread_id == "assistant-thread"
    assert queued.queue_key == "chat:thread-1:msg-1"
    assert queued.target == "assistant-session"


def test_agent_enqueue_prompt_provider_queues_when_container_dispatch_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
        list_vms=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("container enqueue must not list libvirt VMs")
        ),
    )
    backend = _FakeContainerBackend()
    backend.start_error = RuntimeError("worker unavailable")
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(RuntimeError, match="queued prompt for retry"):
        factory.build(
            AgentEnqueuePromptEffectDefinition(
                id="agent-enqueue",
                display_name="Enqueue",
                type="effect:agent:enqueue-prompt",
                target="session-1",
                prompt="check teams",
            )
        ).execute()

    assert backend.started_specs == []
    queued_payload = (tmp_path / "queued-agent-prompts.json").read_text(encoding="utf-8")
    assert "session-1" in queued_payload
    assert "check teams" in queued_payload


def test_agent_prompt_provider_passes_model_override() -> None:
    effect = AgentPromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:prompt",
        prompt="continue",
        session_id="session-42",
        model="gpt-5.4",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect)
    provider.execute()

    assert admin_service.resume_calls[0]["model"] == "gpt-5.4"


def test_agent_prompt_provider_starts_new_thread_when_resume_thread_id_missing() -> None:
    effect = AgentPromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:prompt",
        prompt="continue",
        session_id="session-42",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect)
    output = provider.execute()

    assert output["thread_id"] == "thread-new-123"
    assert admin_service.resume_calls[0]["resume_thread_id"] is None


def test_agent_prompt_provider_rejects_expired_session_before_resume_agent() -> None:
    effect = AgentPromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:prompt",
        prompt="continue",
        session_id="session-42",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    session = admin_service._sessions["session-42"]
    expired_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    admin_service._sessions["session-42"] = session.model_copy(
        update={"expires_at": expired_expires_at}
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    with pytest.raises(
        ValueError,
        match=r"session expired: session_id=session-42 expires_at=.*Z",
    ):
        factory.build(effect).execute()

    assert admin_service.resume_calls == []


def test_agent_enqueue_prompt_provider_rejects_expired_session_before_resume_agent() -> None:
    effect = AgentEnqueuePromptEffectDefinition(
        id="effect-prompt",
        display_name="Prompt",
        type="effect:agent:enqueue-prompt",
        prompt="continue",
        session_id="session-42",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    session = admin_service._sessions["session-42"]
    expired_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    admin_service._sessions["session-42"] = session.model_copy(
        update={"expires_at": expired_expires_at}
    )
    admin_service.list_vms = lambda include_inactive=True, vm_user="agent": []  # type: ignore[attr-defined]
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=admin_service,
        provider_runtime=SimpleNamespace(
            session_lookup=SimpleNamespace(
                read_all_sessions=lambda: dict(admin_service._sessions)
            )
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"session expired: session_id=session-42 expires_at=.*Z",
    ):
        factory.build(effect).execute()

    assert admin_service.resume_calls == []


def test_agent_create_clone_copy_and_prompt_use_container_session_backend(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="libvirt_vm",
        container_worker_base_url="http://worker.test",
        container_worker_guest_openai_base_url="http://relay.test:11434/v1",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        container_worker_guest_http_target_relay_base_url="http://http-target.test:11777",
        container_worker_guest_nuget_relay_base_url="http://nuget.test:11666",
        container_worker_guest_pypi_index_url="http://nexus.test:8081/repository/pypi-all/simple",
        container_worker_guest_npm_registry_url="http://nexus.test:8081/repository/npm-all/",
    )
    _write_root_communication_targets(
        config,
        [
            {
                "id": "teams-channel",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-9",
                "policy": {"capabilities": ["read", "write"]},
            },
            {
                "id": "teams-chat",
                "provider": "teams",
                "kind": "teams",
                "address": "chat:chat-9",
                "policy": {"capabilities": ["read"]},
            },
            {
                "id": "teams-user",
                "provider": "teams",
                "kind": "teams",
                "address": "user:operator@example.com",
                "policy": {"capabilities": ["write"]},
            },
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678",
                "policy": {"capabilities": ["read", "write"]},
            },
        ],
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_clone_repo.container_backend",
        lambda context, config: backend,
    )
    mirror_resolver = _FakeContainerGitMirrorResolver()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_clone_repo._container_git_mirror_resolver",
        lambda context, directory_path: mirror_resolver,
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_copy_files.container_backend",
        lambda context, config: backend,
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_prompt.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    create = factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-1",
            model="gpt-5.4",
            allowed_tools=["set_issue_plan"],
            communication_target_ids=[
                "teams-channel",
                "teams-chat",
                "teams-user",
                "discord-ops",
            ],
            teams_channel_allowlist=["team-a/channel-1", " chat:chat-1 "],
            teams_chat_allowlist=[" chat:chat-2 ", "chat:chat-2"],
            communication_file_receive_enabled=True,
            communication_file_send_enabled=True,
            communication_file_max_bytes=2048,
            communication_file_require_approval_for_send=True,
            web_search_enabled=True,
            dotnet=True,
            nuget=True,
            playwright=True,
            metadata={
                "workflow": {"state": "active"},
                "labels": {"agent_role": "reviewer"},
            },
        )
    ).execute()
    clone = factory.build(
        AgentCloneRepoEffectDefinition(
            id="agent-clone",
            display_name="Clone",
            type="effect:agent:clone-repo",
            session_id="session-1",
            repo_source="/tmp/repo",
        )
    ).execute()
    copied = factory.build(
        AgentCopyFilesEffectDefinition(
            id="agent-copy",
            display_name="Copy",
            type="effect:agent:copy-files",
            session_id="session-1",
            files=[
                {"destination_path": "/home/agent/repos/repo/AGENT_TASK.yaml", "content": "task"}
            ],
        )
    ).execute()
    prompted = factory.build(
        AgentPromptEffectDefinition(
            id="agent-prompt",
            display_name="Prompt",
            type="effect:agent:prompt",
            session_id="session-1",
            prompt="continue",
        )
    ).execute()

    manifest = session_repository.get_session("session-1")
    assert manifest is not None
    assert manifest.vm_id is None
    assert manifest.vm_provider == "container_worker_vm"
    assert ToolName.SET_ISSUE_PLAN in manifest.allowed_tools
    assert manifest.discord_channel_allowlist == ["discord-ops"]
    assert manifest.teams_channel_allowlist == [
        "team-a/channel-1",
        "chat:chat-1",
        "teams-channel",
    ]
    assert manifest.teams_chat_allowlist == ["chat:chat-2", "teams-chat"]
    assert manifest.teams_user_allowlist == ["teams-user"]
    assert manifest.communication_file_receive_enabled is True
    assert manifest.communication_file_send_enabled is True
    assert manifest.communication_file_max_bytes == 2048
    assert manifest.communication_file_require_approval_for_send is True
    assert manifest.web_search_enabled is True
    assert manifest.metadata["workflow"] == {"state": "active"}
    assert manifest.metadata["labels"] == {"agent_role": "reviewer"}
    assert create["execution_backend"] == "container_worker_vm"
    assert create["agent_home"] == "/home/agent"
    assert create["repo_root"] == "/home/agent/repos"
    assert clone["target_dir"] == "/home/agent/repos/repo"
    assert clone["remote_url"] == "ssh://agent-sandbox-git-host/git-remotes/repo.git"
    assert clone["canonical_repo_source"] == "https://example.test/org/repo.git"
    assert clone["pr_notes_ref"] == "refs/notes/agent-pr/84097828fc31"
    assert copied["copied_paths"] == ["/home/agent/repos/repo/AGENT_TASK.yaml"]
    assert prompted["execution_backend"] == "container_worker_vm"
    assert "output_mode" not in prompted
    assert backend.prepared_sessions[0]["session_id"] == "session-1"
    assert mirror_resolver.resolved[0]["repo_source"] == "/tmp/repo"
    assert (
        backend.cloned_repos[0]["repo_source"]
        == "ssh://agent-sandbox-git-host/git-remotes/repo.git"
    )
    assert backend.cloned_repos[0]["repo_name"] == "repo"
    bootstrap_files = backend.written_files[0]["files"]
    bootstrap_by_path = {entry["destination_path"]: entry for entry in bootstrap_files}
    assert "/home/agent/.agent-sandbox/session.env" in bootstrap_by_path
    assert "/home/agent/.config/pip/pip.conf" in bootstrap_by_path
    assert "/home/agent/.npmrc" in bootstrap_by_path
    assert (
        "AGENT_SANDBOX_SESSION_ID=session-1"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "AGENT_SANDBOX_ARTIFACTS_DIR=/artifacts"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "HTTP_TARGET_RELAY_BASE_URL=http://http-target.test:11777"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "AGENT_SANDBOX_HTTP_TARGET_RELAY_BASE_URL=http://http-target.test:11777"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "PIP_INDEX_URL=http://nexus.test:8081/repository/pypi-all/simple"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "NPM_CONFIG_REGISTRY=http://nexus.test:8081/repository/npm-all/"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "GIT_TERMINAL_PROMPT=0"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "GIT_HTTP_LOW_SPEED_LIMIT=1"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "GIT_HTTP_LOW_SPEED_TIME=15"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "trusted-host = nexus.test"
        in bootstrap_by_path["/home/agent/.config/pip/pip.conf"]["content"]
    )
    assert (
        "registry=http://nexus.test:8081/repository/npm-all/"
        in bootstrap_by_path["/home/agent/.npmrc"]["content"]
    )
    assert "replace-registry-host=always" not in bootstrap_by_path["/home/agent/.npmrc"]["content"]
    assert (
        'openai_base_url = "http://relay.test:11434/v1/session/session-1"'
        in bootstrap_by_path["/home/agent/.codex/config.toml"]["content"]
    )
    assert 'web_search = "live"' in bootstrap_by_path["/home/agent/.codex/config.toml"]["content"]
    assert "DOTNET_ROOT" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert "AGENT_SANDBOX_DOTNET_TOOLS_ROOT" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert 'export DOTNET_CLI_HOME="$HOME"' in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert ".dotnet-cli" not in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert (
        "DOTNET_SKIP_WORKLOAD_MANIFEST_UPDATE"
        in bootstrap_by_path["/home/agent/.profile"]["content"]
    )
    assert (
        "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE"
        in bootstrap_by_path["/home/agent/.profile"]["content"]
    )
    assert "DOTNET_NOLOGO" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert "AGENT_SANDBOX_NODE_ROOT" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert (
        "AGENT_SANDBOX_PLAYWRIGHT_RUNTIME" in bootstrap_by_path["/home/agent/.profile"]["content"]
    )
    assert "PLAYWRIGHT_BROWSERS_PATH" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert (
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD" in bootstrap_by_path["/home/agent/.profile"]["content"]
    )
    assert "PLAYWRIGHT_MCP_SANDBOX" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert "PLAYWRIGHT_MCP_EXECUTABLE_PATH" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert "CHROME_BIN" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert "CHROMIUM_BIN" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert "/home/agent/.dotnet-cli/.keep" not in bootstrap_by_path
    assert "/home/agent/.nuget/NuGet/NuGet.Config" in bootstrap_by_path
    nuget_config = bootstrap_by_path["/home/agent/.nuget/NuGet/NuGet.Config"]["content"]
    assert "<clear />" in nuget_config
    assert "api.nuget.org" not in nuget_config
    assert "https://www.nuget.org" not in nuget_config
    assert "http://nuget.test:11666/nuget/nuget-org/v3/index.json" in nuget_config
    assert "http://nuget.test:11666/nuget/azure/v3/index.json" in nuget_config
    assert 'allowInsecureConnections="true"' in nuget_config
    assert "<packageSourceMapping>" in nuget_config
    assert '<packageSource key="sandbox-nuget">' in nuget_config
    assert '<package pattern="*" />' in nuget_config
    assert '<packageSource key="sandbox-azure">' in nuget_config
    assert '<package pattern="ExampleIndustry.*" />' in nuget_config
    assert backend.written_files[1]["files"][0]["destination_path"].endswith("agent-pr-note")
    assert backend.written_files[1]["files"][1]["destination_path"].endswith("push-agent")
    assert backend.written_files[1]["files"][2]["destination_path"].endswith("sync-base")
    assert backend.written_files[2]["files"][0]["content"] == "task"
    clone_config_command = str(backend.started_specs[0].command)
    assert "cd /home/agent/repos/repo" in clone_config_command
    assert "config alias.agent-pr-note" in clone_config_command
    assert "config alias.push-agent" in clone_config_command
    assert "config alias.sync-base" in clone_config_command
    assert ".agent-sandbox/bin/push-agent" in clone_config_command
    assert ".agent-sandbox/bin/sync-base" in clone_config_command
    assert "git config agent-sandbox.prNotesRef" in clone_config_command
    assert 'git config agent-sandbox.workBranch "$WORK_BRANCH"' in clone_config_command
    assert "git switch -c" in clone_config_command
    assert "refs/notes/agent-pr/" in clone_config_command
    assert 'git config agent-sandbox.pushAgentAvailable "true"' in clone_config_command
    assert "CANONICAL_REMOTE_URL=https://example.test/org/repo.git" in clone_config_command
    assert "url.${PUSH_REMOTE_URL}.insteadOf" in clone_config_command
    assert backend.started_specs[-1].command is not None
    assert "cd /home/agent" in str(backend.started_specs[-1].command)
    assert "continue" in str(backend.started_specs[-1].command)
    assert 'web_search="live"' in str(backend.started_specs[-1].command)


def test_agent_prompt_provider_rejects_expired_container_session_before_backend_start(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        ),
    )
    backend_calls: list[object] = []
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_prompt.container_backend",
        lambda context, config: backend_calls.append((context, config)) or _FakeContainerBackend(),
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(
        ValueError,
        match=r"session expired: session_id=session-1 expires_at=.*Z",
    ):
        factory.build(
            AgentPromptEffectDefinition(
                id="agent-prompt",
                display_name="Prompt",
                type="effect:agent:prompt",
                session_id="session-1",
                prompt="continue",
            )
        ).execute()

    assert backend_calls == []


def test_agent_create_container_can_override_npm_registry_url(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="libvirt_vm",
        container_worker_base_url="http://worker.test",
        container_worker_guest_openai_base_url="http://relay.test:11434/v1",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        container_worker_guest_http_target_relay_base_url="http://http-target.test:11777",
        container_worker_guest_nuget_relay_base_url="http://nuget.test:11666",
        container_worker_guest_pypi_index_url="http://nexus.test:8081/repository/pypi-all/simple",
        container_worker_guest_npm_registry_url="http://nexus.test:8081/repository/npm-all/",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-facade",
            npm_registry_url="http://facade.test:11881",
        )
    ).execute()

    bootstrap_files = backend.written_files[0]["files"]
    bootstrap_by_path = {entry["destination_path"]: entry for entry in bootstrap_files}
    assert (
        "NPM_CONFIG_REGISTRY=http://facade.test:11881/"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "registry=http://facade.test:11881/"
        in bootstrap_by_path["/home/agent/.npmrc"]["content"]
    )
    assert "replace-registry-host=always" in bootstrap_by_path["/home/agent/.npmrc"]["content"]


def test_agent_create_container_can_select_configured_image_alias(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="libvirt_vm",
        container_worker_base_url="http://worker.test",
        container_worker_default_image="runner-base:latest",
        container_worker_images={
            "default": "runner-base:latest",
            "dotnet-playwright": "runner-dotnet-playwright:2026-06-05",
        },
        container_worker_guest_openai_base_url="http://relay.test:11434/v1",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        container_worker_guest_http_target_relay_base_url="http://http-target.test:11777",
        container_worker_guest_nuget_relay_base_url="http://nuget.test:11666",
        container_worker_guest_pypi_index_url="http://nexus.test:8081/repository/pypi-all/simple",
        container_worker_guest_npm_registry_url="http://nexus.test:8081/repository/npm-all/",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    result = factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-alias",
            container_image="dotnet-playwright",
        )
    ).execute()

    manifest = session_repository.get_session("session-alias")
    assert manifest is not None
    assert manifest.metadata["container_worker"] == {
        "image": "runner-dotnet-playwright:2026-06-05",
        "image_alias": "dotnet-playwright",
    }
    assert result["container_image"] == "runner-dotnet-playwright:2026-06-05"
    assert result["container_image_alias"] == "dotnet-playwright"
    assert backend.prepared_sessions[0]["metadata"]["container_worker"] == {
        "image": "runner-dotnet-playwright:2026-06-05",
        "image_alias": "dotnet-playwright",
    }


def test_agent_create_container_accepts_explicit_default_image_reference(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="libvirt_vm",
        container_worker_base_url="http://worker.test",
        container_worker_default_image="runner-base:latest",
        container_worker_images={"default": "runner-base:latest"},
        container_worker_guest_openai_base_url="http://relay.test:11434/v1",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        container_worker_guest_http_target_relay_base_url="http://http-target.test:11777",
        container_worker_guest_nuget_relay_base_url="http://nuget.test:11666",
        container_worker_guest_pypi_index_url="http://nexus.test:8081/repository/pypi-all/simple",
        container_worker_guest_npm_registry_url="http://nexus.test:8081/repository/npm-all/",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    result = factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-default",
            container_image="runner-base:latest",
        )
    ).execute()

    manifest = session_repository.get_session("session-default")
    assert manifest is not None
    assert manifest.metadata["container_worker"] == {
        "image": "runner-base:latest",
        "image_alias": None,
    }
    assert result["container_image"] == "runner-base:latest"
    assert result["container_image_alias"] is None
    assert backend.prepared_sessions[0]["metadata"]["container_worker"] == {
        "image": "runner-base:latest",
        "image_alias": None,
    }


def test_agent_create_container_rejects_unknown_container_image_selector_before_bootstrap(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="libvirt_vm",
        container_worker_base_url="http://worker.test",
        container_worker_default_image="runner-base:latest",
        container_worker_images={
            "default": "runner-base:latest",
            "dotnet-playwright": "runner-dotnet-playwright:2026-06-05",
        },
        container_worker_guest_openai_base_url="http://relay.test:11434/v1",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        container_worker_guest_http_target_relay_base_url="http://http-target.test:11777",
        container_worker_guest_nuget_relay_base_url="http://nuget.test:11666",
        container_worker_guest_pypi_index_url="http://nexus.test:8081/repository/pypi-all/simple",
        container_worker_guest_npm_registry_url="http://nexus.test:8081/repository/npm-all/",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()

    def fail_prepare(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("container session should not be prepared")

    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    monkeypatch.setattr(backend, "prepare_session", fail_prepare)
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(ValueError, match="unknown container worker image selector"):
        factory.build(
            AgentCreateEffectDefinition(
                id="agent-create",
                display_name="Create",
                type="effect:agent:create",
                execution_backend="container_worker_vm",
                session_id="session-missing",
                container_image="missing-selector",
            )
        ).execute()

    assert backend.prepared_sessions == []
    assert backend.written_files == []


def test_agent_create_container_rejects_vm_image_before_bootstrap(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="libvirt_vm",
        container_worker_base_url="http://worker.test",
        container_worker_guest_openai_base_url="http://relay.test:11434/v1",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        container_worker_guest_http_target_relay_base_url="http://http-target.test:11777",
        container_worker_guest_nuget_relay_base_url="http://nuget.test:11666",
        container_worker_guest_pypi_index_url="http://nexus.test:8081/repository/pypi-all/simple",
        container_worker_guest_npm_registry_url="http://nexus.test:8081/repository/npm-all/",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(
        ValueError,
        match=(
            r"effect:agent:create vm_image is only supported with execution_backend='libvirt_vm'; "
            r"use container_image for container_worker_vm"
        ),
    ):
        factory.build(
            AgentCreateEffectDefinition(
                id="agent-create",
                display_name="Create",
                type="effect:agent:create",
                execution_backend="container_worker_vm",
                session_id="session-vm-image",
                vm_image="dotnet",
            )
        ).execute()

    assert backend.prepared_sessions == []
    assert backend.written_files == []
    assert session_repository.get_session("session-vm-image") is None


@pytest.mark.parametrize(
    "npm_registry_url",
    [
        "ftp://nexus.test/repository/npm-all/",
        "http://facade.test\nfetch-retries=0",
        "http://facade.test\rfetch-retries=0",
        "http://facade.test\tfetch-retries=0",
    ],
)
def test_agent_create_effect_definition_rejects_invalid_npm_registry_url(
    npm_registry_url: str,
) -> None:
    with pytest.raises(ValidationError, match="npm_registry_url"):
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-invalid",
            npm_registry_url=npm_registry_url,
        )


@pytest.mark.parametrize(
    "resolved_npm_registry_url",
    [
        "ftp://nexus.test/repository/npm-all/",
        "http://facade.test\nfetch-retries=0",
        "http://facade.test\rfetch-retries=0",
        "http://facade.test\tfetch-retries=0",
    ],
)
def test_agent_create_container_rejects_invalid_npm_registry_url_before_bootstrap(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    resolved_npm_registry_url: str,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="libvirt_vm",
        container_worker_base_url="http://worker.test",
        container_worker_guest_openai_base_url="http://relay.test:11434/v1",
        container_worker_guest_gateway_url="http://gateway.test:8000/mcp",
        container_worker_guest_http_target_relay_base_url="http://http-target.test:11777",
        container_worker_guest_nuget_relay_base_url="http://nuget.test:11666",
        container_worker_guest_pypi_index_url="http://nexus.test:8081/repository/pypi-all/simple",
        container_worker_guest_npm_registry_url="http://nexus.test:8081/repository/npm-all/",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    provider = factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-invalid",
            npm_registry_url="${{trigger_1.npm_registry_url}}",
        )
    )

    with pytest.raises(ValueError, match="npm_registry_url"):
        provider.execute(resolved_config={"npm_registry_url": resolved_npm_registry_url})

    assert backend.prepared_sessions == []
    assert backend.written_files == []


def test_agent_clone_container_skips_config_job_when_repo_already_prepared(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_provider="container_worker_vm",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    backend.clone_result_cloned = False
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_clone_repo.container_backend",
        lambda context, config: backend,
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_clone_repo._container_git_mirror_resolver",
        lambda context, directory_path: _FakeContainerGitMirrorResolver(),
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentCloneRepoEffectDefinition(
            id="agent-clone",
            display_name="Clone",
            type="effect:agent:clone-repo",
            session_id="session-1",
            repo_source="/tmp/repo",
        )
    ).execute()

    assert output["message"] == "Repo already prepared for container worker session session-1"
    assert output["target_dir"] == "/home/agent/repos/repo"
    assert backend.written_files[0]["files"][0]["destination_path"].endswith("agent-pr-note")
    assert backend.written_files[0]["files"][1]["destination_path"].endswith("push-agent")
    assert backend.written_files[0]["files"][2]["destination_path"].endswith("sync-base")
    assert backend.started_specs == []
    assert backend.destroyed == []


def test_agent_create_container_noops_when_session_exists(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_provider="container_worker_vm",
            model="gpt-5.4",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"existing": "keep"},
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-1",
            existing_agent_behavior="noop",
            model="gpt-5.5",
            allowed_tools=["set_issue_plan"],
            dotnet=True,
        )
    ).execute()

    assert output["success"] is True
    assert output["skipped"] is True
    assert output["agent_home"] == "/home/agent"
    assert output["repo_root"] == "/home/agent/repos"
    assert backend.prepared_sessions == []
    assert backend.written_files == []
    updated = session_repository.get_session("session-1")
    assert updated is not None
    assert updated.model == "gpt-5.4"
    assert updated.allowed_tools == [ToolName.GET_AGENT_STATUS]
    assert updated.metadata == {"existing": "keep"}


def test_agent_create_container_upserts_when_session_exists(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_provider="container_worker_vm",
            model="gpt-5.4",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            web_search_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "agent_provider": "claude",
                "agent_thread_ids": {
                    "teams:chat-1:agent_thread_id": "thread-existing",
                },
                "existing": "replace",
                "nested": {"keep": "value", "replace": "old"},
                "labels": {"old": "label"},
                "container_worker": {
                    "image": "runner-special:2026-06-05",
                    "image_alias": "special",
                },
            },
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-1",
            existing_agent_behavior="upsert",
            labels={"new": "label"},
            model="gpt-5.5",
            allowed_tools=["set_issue_plan"],
            metadata={
                "explicit": "set",
                "existing": "updated",
                "nested": {"replace": "new"},
            },
        )
    ).execute()

    assert output["success"] is True
    assert output["skipped"] is False
    assert output["container_image"] == "runner-special:2026-06-05"
    assert output["container_image_alias"] == "special"
    assert backend.prepared_sessions[0]["session_id"] == "session-1"
    assert backend.prepared_sessions[0]["metadata"]["container_worker"] == {
        "image": "runner-special:2026-06-05",
        "image_alias": "special",
    }
    assert backend.prepared_sessions[0]["metadata"]["labels"] == {"new": "label"}
    assert backend.written_files
    updated = session_repository.get_session("session-1")
    assert updated is not None
    assert updated.model == "gpt-5.5"
    assert updated.allowed_tools == [ToolName.SET_ISSUE_PLAN, ToolName.STAGE_ARTIFACT]
    assert updated.web_search_enabled is True
    assert updated.metadata["execution_backend"] == "container_worker_vm"
    assert updated.metadata["agent_provider"] == "claude"
    assert updated.metadata["agent_thread_ids"] == {
        "teams:chat-1:agent_thread_id": "thread-existing",
    }
    assert updated.metadata["existing"] == "updated"
    assert updated.metadata["explicit"] == "set"
    assert updated.metadata["nested"] == {"keep": "value", "replace": "new"}
    assert updated.metadata["labels"] == {"new": "label"}
    assert updated.metadata["model"] == "gpt-5.5"
    assert updated.metadata["container_worker"] == {
        "image": "runner-special:2026-06-05",
        "image_alias": "special",
    }
    bootstrap_by_path = {
        entry["destination_path"]: entry for entry in backend.written_files[0]["files"]
    }
    assert (
        "AGENT_PROVIDER=claude\n"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert 'web_search = "live"' in bootstrap_by_path["/home/agent/.codex/config.toml"][
        "content"
    ]


def test_agent_create_container_upsert_preserves_omitted_allowed_contacts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    contacts_config = ContactDirectoryConfig.model_validate(
        {
            "contacts": {
                "ops": {"name": "Ops"},
                "support": {"name": "Support"},
            }
        }
    )
    resolver = _ContainerResolverStub(config, contacts_config)
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_provider="container_worker_vm",
            model="gpt-5.4",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            allowed_contact_ids=["ops"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "container_worker": {
                    "image": "runner-special:2026-06-05",
                    "image_alias": "special",
                }
            },
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=resolver,
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=resolver,
        admin_service=admin_service,
    )

    factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-1",
            existing_agent_behavior="upsert",
            labels={"changed": "true"},
        )
    ).execute()

    updated = session_repository.get_session("session-1")
    assert updated is not None
    assert updated.allowed_contact_ids == ["ops"]
    assert backend.prepared_sessions[-1]["metadata"]["labels"] == {"changed": "true"}

    factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-1",
            existing_agent_behavior="upsert",
            allowed_contact_ids=[],
        )
    ).execute()

    updated = session_repository.get_session("session-1")
    assert updated is not None
    assert updated.allowed_contact_ids == []

    factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-1",
            existing_agent_behavior="upsert",
            allowed_contact_ids=["support"],
        )
    ).execute()

    updated = session_repository.get_session("session-1")
    assert updated is not None
    assert updated.allowed_contact_ids == ["support"]


def test_agent_create_container_does_not_persist_manifest_when_prepare_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    backend.prepare_error = RuntimeError("prepare failed")
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(RuntimeError, match="prepare failed"):
        factory.build(
            AgentCreateEffectDefinition(
                id="agent-create",
                display_name="Create",
                type="effect:agent:create",
                execution_backend="container_worker_vm",
                session_id="session-1",
                model="gpt-5.4",
            )
        ).execute()

    assert backend.prepared_sessions == []
    assert backend.written_files == []
    assert session_repository.get_session("session-1") is None


def test_agent_create_container_does_not_persist_manifest_when_write_files_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    backend.write_files_error = RuntimeError("write files failed")
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(RuntimeError, match="write files failed"):
        factory.build(
            AgentCreateEffectDefinition(
                id="agent-create",
                display_name="Create",
                type="effect:agent:create",
                execution_backend="container_worker_vm",
                session_id="session-1",
                model="gpt-5.4",
            )
        ).execute()

    assert backend.prepared_sessions[0]["session_id"] == "session-1"
    assert backend.written_files == []
    assert session_repository.get_session("session-1") is None


def test_agent_clone_container_skips_push_alias_for_unreachable_remotes() -> None:
    remote_source = "git@ssh.dev.azure.com:v3/example/project/sample-repo"

    command = AgentCloneRepoEffectProvider._container_git_config_command(
        session_id="session-1",
        target_dir="/home/agent/repos/sample-repo",
        repo_source=remote_source,
        remote_url=remote_source,
        clone_branch=None,
    )

    assert "PUSH_REMOTE_URL=''" in command
    assert "config alias.push-agent" not in command
    assert "git remote remove agent >/dev/null 2>&1 || true" in command
    assert 'git config agent-sandbox.pushAgentAvailable "false"' in command


def test_agent_clone_container_uses_private_ssh_mirror_for_push_and_instead_of() -> None:
    command = AgentCloneRepoEffectProvider._container_git_config_command(
        session_id="session-1",
        target_dir="/home/agent/repos/repo",
        repo_source="https://dev.azure.com/org/project/_git/repo",
        remote_url="ssh://agent-sandbox-git-host/opt/agent-sandbox/git-remotes/repo-abc.git",
        clone_branch="main",
    )

    assert (
        "PUSH_REMOTE_URL=ssh://agent-sandbox-git-host/opt/agent-sandbox/git-remotes/repo-abc.git"
        in command
    )
    assert 'git remote add agent "$PUSH_REMOTE_URL"' in command
    assert 'git remote set-url origin "$CANONICAL_REMOTE_URL"' in command
    assert 'git config "url.${PUSH_REMOTE_URL}.insteadOf" "$CANONICAL_REMOTE_URL"' in command


def test_agent_clone_container_configures_git_author_identity() -> None:
    command = AgentCloneRepoEffectProvider._container_git_config_command(
        session_id="session-1",
        target_dir="/home/agent/repos/repo",
        repo_source="https://dev.azure.com/org/project/_git/repo",
        remote_url="ssh://agent-sandbox-git-host/opt/agent-sandbox/git-remotes/repo.git",
        clone_branch="main",
        git_identity=GitAuthorIdentity(
            name="Ada Lovelace",
            email="ada@example.com",
        ),
    )

    assert "git config user.name 'Ada Lovelace'" in command
    assert "git config user.email ada@example.com" in command
    assert "git config author.name 'Ada Lovelace'" in command
    assert "git config author.email ada@example.com" in command
    assert "git config agent-sandbox.gitAuthorName 'Ada Lovelace'" in command
    assert "git config agent-sandbox.gitAuthorEmail ada@example.com" in command
    assert "config alias.push-agent" in command
    assert "git config agent-sandbox.prNotesRef" in command
    assert "config alias.sync-base" in command
    assert "CLONE_BRANCH=main" in command
    assert 'git config agent-sandbox.baseBranch "$BASE_BRANCH"' in command
    assert 'git config agent-sandbox.pushAgentAvailable "true"' in command


def test_agent_clone_container_configures_separate_git_committer_identity() -> None:
    command = AgentCloneRepoEffectProvider._container_git_config_command(
        session_id="session-1",
        target_dir="/home/agent/repos/repo",
        repo_source="https://dev.azure.com/org/project/_git/repo",
        remote_url="ssh://agent-sandbox-git-host/opt/agent-sandbox/git-remotes/repo.git",
        clone_branch="main",
        git_author_identity=GitAuthorIdentity(
            name="Ada Lovelace",
            email="ada@example.com",
        ),
        git_committer_identity=GitAuthorIdentity(
            name="Truman Bot",
            email="truman@example.com",
        ),
    )

    assert "git config user.name 'Ada Lovelace'" in command
    assert "git config user.email ada@example.com" in command
    assert "git config author.name 'Ada Lovelace'" in command
    assert "git config author.email ada@example.com" in command
    assert "git config committer.name 'Truman Bot'" in command
    assert "git config committer.email truman@example.com" in command
    assert "git config agent-sandbox.gitAuthorName 'Ada Lovelace'" in command
    assert "git config agent-sandbox.gitCommitterName 'Truman Bot'" in command


def test_agent_clone_container_fails_before_worker_when_mirror_resolution_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_provider="container_worker_vm",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()

    class FailingResolver:
        def resolve(self, **kwargs):
            raise RuntimeError("upstream fetch failed")

    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_clone_repo.container_backend",
        lambda context, config: backend,
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_clone_repo._container_git_mirror_resolver",
        lambda context, directory_path: FailingResolver(),
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(RuntimeError, match="upstream fetch failed"):
        factory.build(
            AgentCloneRepoEffectDefinition(
                id="agent-clone",
                display_name="Clone",
                type="effect:agent:clone-repo",
                session_id="session-1",
                repo_source="https://example.test/org/repo.git",
            )
        ).execute()

    assert backend.prepared_sessions == []
    assert backend.cloned_repos == []


def test_agent_clone_container_allows_push_alias_for_mounted_remotes() -> None:
    command = AgentCloneRepoEffectProvider._container_git_config_command(
        session_id="session-1",
        target_dir="/home/agent/repos/repo",
        repo_source="git@example.test/org/repo.git",
        remote_url="file:///artifacts/agent-push.git",
        clone_branch="main",
    )

    assert "PUSH_REMOTE_URL=/artifacts/agent-push.git" in command
    assert 'git remote add agent "$PUSH_REMOTE_URL"' in command
    assert "config alias.push-agent" in command
    assert 'git config agent-sandbox.pushAgentAvailable "true"' in command
    assert "CLONE_BRANCH=main" in command
    assert 'git config agent-sandbox.baseBranch "$BASE_BRANCH"' in command


def test_container_reachable_git_remote_url_is_limited_to_mounted_paths() -> None:
    assert (
        _container_reachable_git_remote_url("/home/agent/repos/repo/push.git")
        == "/home/agent/repos/repo/push.git"
    )
    assert (
        _container_reachable_git_remote_url("file:///home/agent/remotes/repo.git")
        == "/home/agent/remotes/repo.git"
    )
    assert _container_reachable_git_remote_url("/home/agent/../tmp/repo.git") is None
    assert _container_reachable_git_remote_url("/tmp/repo.git") is None
    assert _container_reachable_git_remote_url("git@example.test/org/repo.git") is None
    assert (
        _container_reachable_git_remote_url(
            "ssh://agent-sandbox-git-host/opt/agent-sandbox/git-remotes/repo.git"
        )
        == "ssh://agent-sandbox-git-host/opt/agent-sandbox/git-remotes/repo.git"
    )


def test_agent_create_container_rejects_mssql_until_supported(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(ValueError, match="does not support MSSQL"):
        factory.build(
            AgentCreateEffectDefinition(
                id="agent-create",
                display_name="Create",
                type="effect:agent:create",
                execution_backend="container_worker_vm",
                session_id="session-1",
                mssql=True,
            )
        ).execute()

    assert backend.prepared_sessions == []


def test_agent_create_container_writes_managed_codex_skills(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    SkillLibraryRepository(tmp_path).create_skill(
        name="managed.skill",
        content="# Managed\n",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    factory.build(
        AgentCreateEffectDefinition(
            id="agent-create",
            display_name="Create",
            type="effect:agent:create",
            execution_backend="container_worker_vm",
            session_id="session-1",
            codex_skills=["managed.skill"],
        )
    ).execute()

    bootstrap_files = backend.written_files[0]["files"]
    skill_file = next(
        entry
        for entry in bootstrap_files
        if entry["destination_path"]
        == "/home/agent/.config/agent-sandbox/codex/skills/managed.skill/SKILL.md"
    )
    assert skill_file["content"] == "# Managed\n"


def test_agent_create_container_rejects_unknown_codex_skill_before_prepare(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_create.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(LookupError, match="Unknown skill: missing.skill"):
        factory.build(
            AgentCreateEffectDefinition(
                id="agent-create",
                display_name="Create",
                type="effect:agent:create",
                execution_backend="container_worker_vm",
                session_id="session-1",
                codex_skills=["missing.skill"],
            )
        ).execute()

    assert backend.prepared_sessions == []


def test_agent_destroy_provider_destroys_vm_for_libvirt_session(tmp_path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-libvirt",
            vm_id="vm-libvirt",
            vm_provider="libvirt",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    class DestroyAdminStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.calls: list[str] = []

        def destroy_vm(self, target: str):
            self.calls.append(target)
            return VmActionResult(
                success=True,
                vm_name=target,
                action="destroy_vm",
                message=f"Destroyed VM {target}",
            )

    admin_service = DestroyAdminStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentDestroyEffectDefinition(
            id="agent-destroy",
            display_name="Destroy",
            type="effect:agent:destroy",
            session_id="session-libvirt",
        )
    ).execute()

    assert admin_service.calls == ["vm-libvirt"]
    assert output["success"] is True
    assert output["execution_backend"] == "libvirt_vm"
    assert output["session_id"] == "session-libvirt"
    assert output["vm_name"] == "vm-libvirt"
    assert output["status"] == "destroyed"


def test_agent_destroy_provider_deletes_container_session(tmp_path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="libvirt_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-container",
            vm_id=None,
            vm_provider="container_worker_vm",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    class DeleteAdminStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.calls: list[tuple[str, str | None]] = []

        def delete_admin_agent(self, agent_id: str, *, backend: str | None = None):
            self.calls.append((agent_id, backend))
            session = session_repository.get_session(agent_id)
            assert session is not None
            session_repository.upsert_session(
                session.model_copy(
                    update={
                        "metadata": {
                            **session.metadata,
                            "container_lifecycle": {
                                "state": "cleaned_up",
                                "cleanup_status": "cleanup_succeeded",
                            },
                        },
                    }
                )
            )
            return {
                "success": True,
                "target_name": agent_id,
                "status": "cleaned_up",
                "session_id": agent_id,
                "message": "Cleaned up container worker session",
            }

    admin_service = DeleteAdminStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentDestroyEffectDefinition(
            id="agent-destroy",
            display_name="Destroy",
            type="effect:agent:destroy",
            session_id="session-container",
        )
    ).execute()

    assert admin_service.calls == [("session-container", "container_worker_vm")]
    assert output["success"] is True
    assert output["execution_backend"] == "container_worker_vm"
    assert output["session_id"] == "session-container"
    assert output["status"] == "cleaned_up"
    assert session_repository.get_session("session-container") is not None
    assert (
        session_repository.get_session("session-container").metadata["container_lifecycle"]["state"]
        == "cleaned_up"
    )


def test_agent_exec_provider_delegates_libvirt_sessions_to_vm_exec(tmp_path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-libvirt",
            vm_id="vm-libvirt",
            vm_provider="libvirt",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    class ExecAdminStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.calls: list[dict[str, object]] = []

        def vm_exec(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "success": True,
                "vm_name": kwargs["target"],
                "action": "vm_exec",
                "message": "ok",
                "exit_code": 0,
                "stdout": "hello\n",
                "stderr": "",
            }

    admin_service = ExecAdminStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentExecEffectDefinition(
            id="agent-exec",
            display_name="Exec",
            type="effect:agent:exec",
            session_id="session-libvirt",
            command="echo hello",
            vm_user="builder",
            working_directory="/work",
            check=True,
        )
    ).execute()

    assert admin_service.calls == [
        {
            "target": "vm-libvirt",
            "vm_user": "builder",
            "command": "echo hello",
            "working_directory": "/work",
            "environment": {},
            "stdin": None,
            "timeout_seconds": 3600.0,
            "check": True,
        }
    ]
    assert output["success"] is True
    assert output["execution_backend"] == "libvirt_vm"
    assert output["stdout"] == "hello\n"


def test_agent_exec_provider_runs_container_command_and_cleans_up(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="libvirt_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-container",
            vm_id=None,
            vm_provider="container_worker_vm",
            directory_path="root/team-a",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    backend.logs = "prepared\n"
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_exec.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentExecEffectDefinition(
            id="agent-exec",
            display_name="Exec",
            type="effect:agent:exec",
            session_id="session-container",
            command=["echo", "prepared"],
            working_directory="/home/agent/repos/repo",
        )
    ).execute()

    assert output["success"] is True
    assert output["execution_backend"] == "container_worker_vm"
    assert output["session_id"] == "session-container"
    assert output["stdout"] == "prepared\n"
    assert output["stderr"] == ""
    assert backend.started_specs[0].session_id == "session-container"
    assert backend.started_specs[0].directory_path == "root/team-a"
    assert "cd /home/agent/repos/repo" in str(backend.started_specs[0].command)
    assert "echo prepared" in str(backend.started_specs[0].command)
    assert backend.destroyed == [output["execution_id"]]
    assert backend.log_snapshot_calls == [
        {"execution_id": output["execution_id"], "lines": 0, "mode": "raw"}
    ]


def test_agent_exec_provider_returns_container_failure_when_check_disabled(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-container",
            vm_id=None,
            vm_provider="container_worker_vm",
            directory_path="root/team-a",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.FAILED,
        message="command failed",
        metadata={"exit_code": 22},
    )
    backend.logs = "failure log\n"
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_exec.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentExecEffectDefinition(
            id="agent-exec",
            display_name="Exec",
            type="effect:agent:exec",
            session_id="session-container",
            command="false",
            check=False,
        )
    ).execute()

    assert output["success"] is False
    assert output["status"] == "failed"
    assert output["exit_code"] == 22
    assert output["stdout"] == "failure log\n"
    assert output["stderr"] == "command failed"
    assert "cd /home/agent" in str(backend.started_specs[0].command)
    assert backend.destroyed == [output["execution_id"]]


def test_agent_exec_provider_raises_for_checked_container_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-container",
            vm_id=None,
            vm_provider="container_worker_vm",
            directory_path="root/team-a",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            provider_config=_ContainerResolverStub(config),
        )
    )
    backend = _FakeContainerBackend()
    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.FAILED,
        message="command failed",
        metadata={},
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_exec.container_backend",
        lambda context, config: backend,
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    with pytest.raises(RuntimeError, match="command failed"):
        factory.build(
            AgentExecEffectDefinition(
                id="agent-exec",
                display_name="Exec",
                type="effect:agent:exec",
                session_id="session-container",
                command="false",
                check=True,
            )
        ).execute()

    assert backend.destroyed


def test_agent_followup_routing_uses_libvirt_session_provider_over_container_default(
    tmp_path,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-libvirt",
            vm_id="vm-libvirt",
            vm_provider="libvirt",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context = EffectRuntimeContext(
        resolved_config=config,
        admin_service=SimpleNamespace(
            runtime=SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
        ),
        metadata={"directory_path": "root"},
    )

    execution_backend, _ = selected_execution_backend_for_target(
        context,
        {"session_id": "session-libvirt"},
        directory_path="root",
    )

    assert execution_backend == "libvirt_vm"


def test_agent_copy_files_vm_name_only_uses_resolved_vm_name_as_copy_target(
    tmp_path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="libvirt_vm")
    session_repository = JsonSessionManifestRepository(config.session_store_path)

    class CopyAdminStub:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                session_repository=session_repository,
                provider_config=_ContainerResolverStub(config),
            )
            self.calls: list[dict[str, object]] = []

        def vm_copy_files(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "success": True,
                "message": "copied",
                "vm_name": kwargs["target"],
                "copied_count": len(kwargs["files"]),
                "copied_paths": [entry["destination_path"] for entry in kwargs["files"]],
            }

    admin_service = CopyAdminStub()
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=admin_service,
    )

    output = factory.build(
        AgentCopyFilesEffectDefinition(
            id="agent-copy",
            display_name="Copy",
            type="effect:agent:copy-files",
            vm_name="vm-standalone",
            files=[{"destination_path": "/home/agent/hello.txt", "content": "hello"}],
        )
    ).execute()

    assert admin_service.calls[0]["target"] == "vm-standalone"
    assert output["session_id"] == "session-vm-standalone"
    assert output["vm_name"] == "vm-standalone"
