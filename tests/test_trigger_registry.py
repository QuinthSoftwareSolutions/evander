from __future__ import annotations

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.models import CronTriggerDefinition
from agent_sandbox.triggers.providers.base import TriggerProviderBase
from agent_sandbox.triggers.registry import (
    DEFAULT_TRIGGER_PROVIDER_REGISTRY,
    TriggerProviderFactory,
    TriggerProviderRegistry,
)


class _ResolverStub:
    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        return GatewayConfig(runtime_dir=(GatewayConfig().runtime_dir / (directory_path or "root")))


class _RecordingProvider(TriggerProviderBase):
    pass


def test_default_registry_includes_all_known_trigger_types() -> None:
    assert set(DEFAULT_TRIGGER_PROVIDER_REGISTRY) == {
        "trigger:cron",
        "trigger:ticket:created",
        "trigger:ticket:changed",
        "trigger:discord:new-message",
        "trigger:email:new-message",
        "trigger:pull-request:new-feedback",
        "trigger:git:push",
    }


def test_factory_initializes_provider_with_runtime_context() -> None:
    trigger = CronTriggerDefinition(
        id="cron-1",
        display_name="Cron",
        type="trigger:cron",
        expression="*/5 * * * *",
    )
    registry = TriggerProviderRegistry()
    registry.register("trigger:cron", _RecordingProvider)

    factory = TriggerProviderFactory(
        provider_config_resolver=_ResolverStub(),
        registry=registry,
        env={"API_TOKEN": "top-secret"},
    )
    provider = factory.build(trigger, directory_path="root/team-a")

    assert isinstance(provider, _RecordingProvider)
    assert provider.trigger_definition == trigger
    assert provider.runtime_context is not None
    assert provider.runtime_context.resolve_secret("API_TOKEN") == "top-secret"
    assert provider.runtime_context.metadata["directory_path"] == "root/team-a"
