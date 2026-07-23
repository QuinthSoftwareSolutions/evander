from __future__ import annotations

from pathlib import Path

import pytest

from evander.config import GatewayConfig
from evander.events.provider_models import (
    CommunicationNewMessageTriggerDefinition,
    CronTriggerDefinition,
)
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.triggers.providers.base import TriggerProviderBase
from evander.triggers.registry import (
    DEFAULT_TRIGGER_PROVIDER_REGISTRY,
    TriggerProviderFactory,
    TriggerProviderRegistry,
)


class _ResolverStub:
    def __init__(self, runtime_dir: Path | None = None) -> None:
        self._runtime_dir = runtime_dir or GatewayConfig().runtime_dir

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        del directory_path
        return GatewayConfig(
            runtime_dir=self._runtime_dir,
            directories_store_path=self._runtime_dir / "directories.json",
        )


class _RecordingProvider(TriggerProviderBase):
    pass


class _RuntimeStub:
    def list_vms(self, **kwargs):
        del kwargs
        return []


def test_default_registry_includes_all_known_trigger_types() -> None:
    assert set(DEFAULT_TRIGGER_PROVIDER_REGISTRY) == {
        "trigger:cron",
        "trigger:ticket:created",
        "trigger:ticket:changed",
        "trigger:issue:comment-created",
        "trigger:ado:work-item-matching",
        "trigger:ado:work-item-comment-created",
        "trigger:remote-issue-matching",
        "trigger:remote-issue-comment-created",
        "trigger:script-run-request",
        "trigger:communication:new-message",
        "trigger:http:webhook",
        "trigger:discord:new-message",
        "trigger:teams:new-message",
        "trigger:teams:direct-message",
        "trigger:teams-meeting:new-transcript",
        "trigger:email:new-message",
        "trigger:event",
        "trigger:pull-request:new-feedback",
        "trigger:pull-request:completed",
        "trigger:review:new-request",
        "trigger:approval:pending-created",
        "trigger:git:push",
        "trigger:host-directory:file-added",
        "trigger:ci:run:completed",
        "trigger:agent:prompt:new",
        "trigger:agent:run:completed",
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


def test_factory_exposes_vm_inventory_metadata_alias() -> None:
    trigger = CronTriggerDefinition(
        id="cron-1",
        display_name="Cron",
        type="trigger:cron",
        expression="*/5 * * * *",
    )
    runtime = _RuntimeStub()

    factory = TriggerProviderFactory(
        provider_config_resolver=_ResolverStub(),
        admin_service=runtime,
    )
    provider = factory.build(trigger)

    assert provider.runtime_context is not None
    assert provider.runtime_context.metadata["admin_service"] is runtime
    assert callable(getattr(provider.runtime_context.metadata["vm_inventory"], "list_vms", None))


def test_factory_rejects_non_read_communication_targets_during_provider_setup(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "teams-notify",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-1",
                    "policy": {"capabilities": ["notify"]},
                }
            ]
        },
    )
    trigger = CommunicationNewMessageTriggerDefinition.model_validate(
        {
            "id": "comm-1",
            "display_name": "Communication",
            "type": "trigger:communication:new-message",
            "communication_target_ids": ["teams-notify"],
        }
    )

    factory = TriggerProviderFactory(
        provider_config_resolver=_ResolverStub(runtime_dir=runtime_dir),
        registry=TriggerProviderRegistry(),
    )

    with pytest.raises(ValueError, match="read capability"):
        factory.build(trigger, directory_path="root")
