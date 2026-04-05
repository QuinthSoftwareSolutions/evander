from __future__ import annotations

from dataclasses import dataclass

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectProviderBase
from agent_sandbox.effects.registry import (
    DEFAULT_EFFECT_PROVIDER_REGISTRY,
    EffectProviderFactory,
    EffectProviderRegistry,
)
from agent_sandbox.events.models import AgentPromptEffectDefinition, VmCreateEffectDefinition
from agent_sandbox.models import VmActionResult


class _ResolverStub:
    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        return GatewayConfig(runtime_dir=(GatewayConfig().runtime_dir / (directory_path or "root")))


@dataclass
class _AdminServiceStub:
    create_vm_calls: list[dict[str, object]]
    resume_calls: list[dict[str, object]]

    def create_vm(self, **kwargs):
        self.create_vm_calls.append(kwargs)
        return VmActionResult(success=True, vm_name=str(kwargs["vm_name"]), action="create_vm", message="ok")

    def resume_agent(self, *, target: str, prompt: str, vm_user: str = "agent"):
        self.resume_calls.append({"target": target, "prompt": prompt, "vm_user": vm_user})
        return VmActionResult(success=True, vm_name=target, action="resume_issue_agent", message="ok")


class _RecordingProvider(EffectProviderBase):
    pass


def test_default_registry_includes_all_known_effect_types() -> None:
    assert set(DEFAULT_EFFECT_PROVIDER_REGISTRY) == {
        "effect:vm:create",
        "effect:agent:prompt",
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
    admin_service = _AdminServiceStub(create_vm_calls=[], resume_calls=[])

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
        dotnet=True,
    )
    admin_service = _AdminServiceStub(create_vm_calls=[], resume_calls=[])
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

    provider = factory.build(effect, directory_path="root/team-a")
    output = provider.execute()

    assert output == {
        "session_id": "session-lab-vm",
        "vm_name": "lab-vm",
        "succeeded": True,
    }
    assert admin_service.create_vm_calls[0]["directory_path"] == "root/team-a"
    assert admin_service.create_vm_calls[0]["dotnet"] is True


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
    admin_service = _AdminServiceStub(create_vm_calls=[], resume_calls=[])
    factory = EffectProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)

    provider = factory.build(effect)
    output = provider.execute()

    assert output["thread_id"] == "thread-123"
    assert output["succeeded"] is True
    assert output["output_mode"] == "on_completion"
    assert admin_service.resume_calls[0]["target"] == "session-42"
