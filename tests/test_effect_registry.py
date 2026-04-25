from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectProviderBase
from agent_sandbox.effects.registry import (
    DEFAULT_EFFECT_PROVIDER_REGISTRY,
    EffectProviderFactory,
    EffectProviderRegistry,
)
from pydantic import BaseModel, Field

from agent_sandbox.events.models import WorkflowNodeEnvelope
from agent_sandbox.events.provider_models import (
    AgentEnqueuePromptEffectDefinition,
    AgentPromptEffectDefinition,
    VmBootstrapFrontendEffectDefinition,
    VmBootstrapPythonEffectDefinition,
    VmCloneRepoEffectDefinition,
    VmCreateEffectDefinition,
)
from agent_sandbox.models import SessionManifest, ToolName, VmActionResult


class _ResolverStub:
    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        return GatewayConfig(runtime_dir=(GatewayConfig().runtime_dir / (directory_path or "root")))


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
        return VmActionResult(success=True, vm_name=str(kwargs["vm_name"]), action="create_vm", message="ok")

    def clone_repo_into_vm(self, **kwargs):
        self.clone_repo_into_vm_calls.append(kwargs)
        return VmActionResult(success=True, vm_name=str(kwargs["vm_name"]), action="clone_repo_into_vm", message="ok")

    def bootstrap_python_in_vm(self, **kwargs):
        self.bootstrap_python_in_vm_calls.append(kwargs)
        return VmActionResult(success=True, vm_name=str(kwargs["vm_name"]), action="bootstrap_python_in_vm", message="ok")

    def bootstrap_frontend_in_vm(self, **kwargs):
        self.bootstrap_frontend_in_vm_calls.append(kwargs)
        return VmActionResult(success=True, vm_name=str(kwargs["vm_name"]), action="bootstrap_frontend_in_vm", message="ok")

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
        "effect:agent:prompt",
        "effect:agent:enqueue-prompt",
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
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

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
    assert admin_service.create_vm_calls[0]["labels"] == {"workflow": "issue-worker", "role": "issue"}
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
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

    provider = factory.build(effect, directory_path="root/team-a")
    provider.execute()

    assert admin_service.create_vm_calls[0]["codex_skills"] == ["playwright", "exampleindustry-app-demo"]


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
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

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
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

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
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

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
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

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
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

    provider = factory.build(effect)
    output = provider.execute()

    assert output["dispatch_id"].startswith("dispatch-effect-prompt-")
    assert output["queued"] is False
    assert admin_service.resume_calls[0]["target"] == "session-42"


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
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

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
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

    provider = factory.build(effect)
    output = provider.execute()

    assert output["thread_id"] == "thread-new-123"
    assert admin_service.resume_calls[0]["resume_thread_id"] is None
