from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.execution import ExecutionStatusKind
from agent_sandbox.execution.container_worker_repository import ContainerWorkerExecutionRecord
from agent_sandbox.effects.providers.agent_clone_repo import (
    AgentCloneRepoEffectProvider,
    _container_reachable_git_remote_url,
)
from agent_sandbox.effects.providers.agent_session import selected_execution_backend_for_target
from agent_sandbox.effects.providers.base import EffectProviderBase, EffectRuntimeContext
from agent_sandbox.effects.registry import (
    DEFAULT_EFFECT_PROVIDER_REGISTRY,
    EffectProviderFactory,
    EffectProviderRegistry,
)
from pydantic import BaseModel, Field

from agent_sandbox.events.models import WorkflowNodeEnvelope
from agent_sandbox.events.provider_models import (
    AgentCloneRepoEffectDefinition,
    AgentCopyFilesEffectDefinition,
    AgentCreateEffectDefinition,
    AgentEnqueuePromptEffectDefinition,
    AgentExecEffectDefinition,
    AgentPromptEffectDefinition,
    VmBootstrapFrontendEffectDefinition,
    VmBootstrapPythonEffectDefinition,
    VmCloneRepoEffectDefinition,
    VmCreateEffectDefinition,
)
from agent_sandbox.models import SessionManifest, ToolName, VmActionResult
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository


class _ResolverStub:
    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        return GatewayConfig(runtime_dir=(GatewayConfig().runtime_dir / (directory_path or "root")))


class _ContainerResolverStub:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        del directory_path
        return self.config

    def resolve_for_session_id(self, session_id: str) -> GatewayConfig:
        del session_id
        return self.config

    def resolve_effect_provider_config(self, directory_path: str | None):
        from agent_sandbox.provider_config import EffectProviderConfig

        del directory_path
        return EffectProviderConfig.model_validate(self.config.model_dump(mode="json"))


class _FakeContainerBackend:
    def __init__(self) -> None:
        self.prepared_sessions: list[dict[str, object]] = []
        self.cloned_repos: list[dict[str, object]] = []
        self.written_files: list[dict[str, object]] = []
        self.prepared_specs: list[object] = []
        self.started_specs: list[object] = []
        self.destroyed: list[str] = []
        self.status = SimpleNamespace(status=ExecutionStatusKind.STOPPED, message=None, metadata={})
        self.logs = ""
        self.start_error: Exception | None = None

    def prepare_session(self, **kwargs):
        self.prepared_sessions.append(kwargs)
        return SimpleNamespace(
            workspace_container_path="/workspace",
            agent_home_container_path="/home/agent",
        )

    def clone_repo(self, **kwargs):
        self.cloned_repos.append(kwargs)
        return SimpleNamespace(
            target_dir="/workspace",
            guest_repo_name="repo",
            remote_url=kwargs["repo_source"],
            cloned=True,
        )

    def write_files(self, **kwargs):
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

    def get_log_snapshot(self, execution_id: str, *, lines: int = 50):
        del execution_id, lines
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
        self.runtime = SimpleNamespace(
            session_repository=SimpleNamespace(
                list_sessions=lambda: sessions,
                read_all=lambda: {session.session_id: session for session in sessions},
            )
        )

    def create_vm(self, **kwargs):
        self.create_vm_calls.append(kwargs)
        return VmActionResult(
            success=True, vm_name=str(kwargs["vm_name"]), action="create_vm", message="ok"
        )

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
    ):
        self.resume_calls.append(
            {
                "target": target,
                "prompt": prompt,
                "model": model,
                "vm_user": vm_user,
                "dispatch_id": dispatch_id,
                "resume_thread_id": resume_thread_id,
            }
        )
        return VmActionResult(
            success=True,
            vm_name=target,
            action="resume_issue_agent",
            message="ok",
            thread_id=resume_thread_id or "thread-new-123",
        )


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
        "effect:agent:clone-repo",
        "effect:agent:bootstrap-frontend",
        "effect:agent:copy-files",
        "effect:agent:prompt",
        "effect:agent:enqueue-prompt",
        "effect:agent:exec",
        "effect:bash:script",
        "effect:vm:exec",
        "effect:vm:copy-files",
        "effect:vm:delete-files",
        "effect:issue:update-state",
        "effect:session:update-metadata",
        "effect:git:promote-agent-branch",
        "effect:pr:create",
    }


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
    provider = factory.build(effect, directory_path="root/team-a")

    assert isinstance(provider, _RecordingProvider)
    assert provider.effect_definition == effect
    assert provider.runtime_context is not None
    assert provider.runtime_context.resolve_secret("API_TOKEN") == "top-secret"
    assert provider.runtime_context.metadata["directory_path"] == "root/team-a"


def test_vm_create_provider_returns_required_outputs() -> None:
    effect = VmCreateEffectDefinition(
        id="effect-vm",
        display_name="Create VM",
        type="effect:vm:create",
        vm_name="lab-vm",
        labels={"workflow": "issue-worker", "role": "issue"},
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
    }
    assert admin_service.clone_repo_into_vm_calls[0] == {
        "vm_name": "vm-42",
        "session_id": "session-42",
        "repo_source": "/tmp/repo",
        "clone_branch": None,
        "vm_user": "agent",
        "directory_path": "root/team-a",
    }


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
        output_mode="on_completion",
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
    assert output["output_mode"] == "on_completion"
    assert admin_service.resume_calls[0]["target"] == "session-42"
    assert admin_service.resume_calls[0]["dispatch_id"] == output["dispatch_id"]
    assert admin_service.resume_calls[0]["resume_thread_id"] == "thread-123"


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
        resume_thread_id="thread-123",
    )
    admin_service = _AdminServiceStub(
        create_vm_calls=[],
        clone_repo_into_vm_calls=[],
        bootstrap_python_in_vm_calls=[],
        bootstrap_frontend_in_vm_calls=[],
        resume_calls=[],
    )
    admin_service.list_vms = lambda include_inactive=True, vm_user="agent": []  # type: ignore[attr-defined]
    factory = EffectProviderFactory(
        provider_config_resolver=_ResolverStub(), admin_service=admin_service
    )

    provider = factory.build(effect)
    output = provider.execute()

    assert output["dispatch_id"].startswith("dispatch-effect-prompt-")
    assert output["queued"] is False
    assert admin_service.resume_calls[0]["target"] == "session-42"


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
    assert "check teams" in str(backend.started_specs[0].command)


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
        read_all=lambda: {
            "agent-session-1-old": ContainerWorkerExecutionRecord(
                execution_id="agent-session-1-old",
                session_id="session-1",
                worker_job_id="job-1",
                worker_url="http://worker.test",
                target_name="agent-session-1-old",
                last_status="running",
            )
        }
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
        )
    ).execute()

    assert output["execution_backend"] == "container_worker_vm"
    assert output["queued"] is True
    assert output["queue_key"] == "session-1"
    assert backend.started_specs == []
    queued_payload = (tmp_path / "queued-agent-prompts.json").read_text(encoding="utf-8")
    assert "check teams" in queued_payload


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
            dotnet=True,
            nuget=True,
            playwright=True,
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
            files=[{"destination_path": "/workspace/AGENT_TASK.yaml", "content": "task"}],
        )
    ).execute()
    prompted = factory.build(
        AgentPromptEffectDefinition(
            id="agent-prompt",
            display_name="Prompt",
            type="effect:agent:prompt",
            session_id="session-1",
            prompt="continue",
            output_mode="on_completion",
        )
    ).execute()

    manifest = session_repository.get_session("session-1")
    assert manifest is not None
    assert manifest.vm_id is None
    assert manifest.vm_provider == "container_worker_vm"
    assert ToolName.SET_ISSUE_PLAN in manifest.allowed_tools
    assert create["execution_backend"] == "container_worker_vm"
    assert clone["target_dir"] == "/workspace"
    assert clone["remote_url"] == "ssh://agent-sandbox-git-host/git-remotes/repo.git"
    assert clone["canonical_repo_source"] == "https://example.test/org/repo.git"
    assert copied["copied_paths"] == ["/workspace/AGENT_TASK.yaml"]
    assert prompted["execution_backend"] == "container_worker_vm"
    assert prompted["output_mode"] == "on_completion"
    assert backend.prepared_sessions[0]["session_id"] == "session-1"
    assert mirror_resolver.resolved[0]["repo_source"] == "/tmp/repo"
    assert backend.cloned_repos[0]["repo_source"] == "ssh://agent-sandbox-git-host/git-remotes/repo.git"
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
        "PIP_INDEX_URL=http://nexus.test:8081/repository/pypi-all/simple"
        in bootstrap_by_path["/home/agent/.agent-sandbox/session.env"]["content"]
    )
    assert (
        "NPM_CONFIG_REGISTRY=http://nexus.test:8081/repository/npm-all/"
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
    assert (
        'openai_base_url = "http://relay.test:11434/v1/session/session-1"'
        in bootstrap_by_path["/home/agent/.codex/config.toml"]["content"]
    )
    assert "DOTNET_ROOT" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert "AGENT_SANDBOX_NODE_ROOT" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert "PLAYWRIGHT_BROWSERS_PATH" in bootstrap_by_path["/home/agent/.profile"]["content"]
    assert "/home/agent/.nuget/NuGet/NuGet.Config" in bootstrap_by_path
    assert backend.written_files[1]["files"][0]["destination_path"].endswith("agent-pr-note")
    assert backend.written_files[2]["files"][0]["content"] == "task"
    clone_config_command = str(backend.started_specs[0].command)
    assert "config alias.agent-pr-note" in clone_config_command
    assert "config alias.push-agent" in clone_config_command
    assert 'git config agent-sandbox.pushAgentAvailable "true"' in clone_config_command
    assert "CANONICAL_REMOTE_URL=https://example.test/org/repo.git" in clone_config_command
    assert "url.${PUSH_REMOTE_URL}.insteadOf" in clone_config_command
    assert backend.started_specs[-1].command is not None
    assert "continue" in str(backend.started_specs[-1].command)


def test_agent_clone_container_skips_push_alias_for_unreachable_remotes() -> None:
    remote_source = "git@ssh.dev.azure.com:v3/example/project/sample-repo"

    command = AgentCloneRepoEffectProvider._container_git_config_command(
        session_id="session-1",
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
    assert "config alias.push-agent" in command
    assert 'git config agent-sandbox.pushAgentAvailable "true"' in command


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
        repo_source="git@example.test/org/repo.git",
        remote_url="file:///artifacts/agent-push.git",
        clone_branch="main",
    )

    assert "PUSH_REMOTE_URL=/artifacts/agent-push.git" in command
    assert 'git remote add agent "$PUSH_REMOTE_URL"' in command
    assert "config alias.push-agent" in command
    assert 'git config agent-sandbox.pushAgentAvailable "true"' in command
    assert "git config agent-sandbox.baseBranch main" in command


def test_container_reachable_git_remote_url_is_limited_to_mounted_paths() -> None:
    assert _container_reachable_git_remote_url("/workspace/push.git") == "/workspace/push.git"
    assert (
        _container_reachable_git_remote_url("file:///home/agent/remotes/repo.git")
        == "/home/agent/remotes/repo.git"
    )
    assert _container_reachable_git_remote_url("/workspace/../tmp/repo.git") is None
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
            working_directory="/workspace",
        )
    ).execute()

    assert output["success"] is True
    assert output["execution_backend"] == "container_worker_vm"
    assert output["session_id"] == "session-container"
    assert output["stdout"] == "prepared\n"
    assert output["stderr"] == ""
    assert backend.started_specs[0].session_id == "session-container"
    assert backend.started_specs[0].directory_path == "root/team-a"
    assert "cd /workspace" in str(backend.started_specs[0].command)
    assert "echo prepared" in str(backend.started_specs[0].command)
    assert backend.destroyed == [output["execution_id"]]


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
