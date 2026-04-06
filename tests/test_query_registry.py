from __future__ import annotations

from dataclasses import dataclass

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.models import VmRunStateQueryDefinition
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

    def list_vms(self, *, include_inactive: bool = True, omit_state: bool = False):
        del include_inactive, omit_state
        return list(self.vms)


class _RecordingProvider(QueryProviderBase):
    pass


def test_default_registry_includes_known_query_types() -> None:
    assert set(DEFAULT_QUERY_PROVIDER_REGISTRY) == {"query:vm:run-state"}


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

    factory = QueryProviderFactory(provider_config_resolver=_ResolverStub(), admin_service=admin_service)
    provider = factory.build(query)
    output = provider.execute(resolved_config={"session_id": "session-1"})

    assert output["state_code"] == "running"
    assert output["is_running"] is True
