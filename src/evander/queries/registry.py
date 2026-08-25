from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from evander.events.models import (
    QueryDefinitionDiscriminated,
    QueryType,
    WorkflowNodeEnvelope,
)
from evander.provider_config import ProviderConfigResolver, QueryProviderConfig
from evander.provider_runtime.adapters import (
    build_provider_runtime_ports,
    query_provider_config_blob,
)
from evander.workflow.plugins import QUERY_PLUGIN_CLASSES

from .providers.base import QueryProviderBase, QueryRuntimeContext

QueryProviderClass = type[QueryProviderBase]


DEFAULT_QUERY_PROVIDER_REGISTRY: dict[QueryType, QueryProviderClass] = dict(QUERY_PLUGIN_CLASSES)


class QueryProviderRegistry:
    def __init__(self, initial: Mapping[QueryType, QueryProviderClass] | None = None) -> None:
        self._providers: dict[QueryType, QueryProviderClass] = dict(
            initial or DEFAULT_QUERY_PROVIDER_REGISTRY
        )

    def register(self, query_type: QueryType, provider_class: QueryProviderClass) -> None:
        self._providers[query_type] = provider_class

    def resolve(self, query_type: QueryType) -> QueryProviderClass:
        provider_class = self._providers.get(query_type)
        if provider_class is None:
            raise KeyError(f"No query provider registered for type {query_type!r}")
        return provider_class

    def registered_types(self) -> tuple[QueryType, ...]:
        return tuple(self._providers.keys())


class QueryProviderFactory:
    """Centralized provider constructor to keep env/secret handling host-side."""

    def __init__(
        self,
        provider_config_resolver: ProviderConfigResolver,
        admin_service: object | None = None,
        *,
        registry: QueryProviderRegistry | None = None,
        provider_runtime: object | None = None,
        admin_adapter: object | None = None,
        gate_adapter: object | None = None,
        env: Mapping[str, str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.provider_config_resolver = provider_config_resolver
        self._provider_runtime_ports = build_provider_runtime_ports(
            provider_runtime=provider_runtime,
            admin_service=admin_service,
            admin_adapter=admin_adapter,
            gate_adapter=gate_adapter,
        )
        self.admin_service = (
            self._provider_runtime_ports.admin_service
            or admin_service
            or admin_adapter
            or provider_runtime
        )
        self._admin_adapter = self.admin_service
        self.registry = registry or QueryProviderRegistry()
        self.env = env or dict(os.environ)
        self.secret_resolver = secret_resolver

    def build(
        self,
        query_definition: QueryDefinitionDiscriminated,
        *,
        directory_path: str | None = None,
        graph_id: str | None = None,
    ) -> QueryProviderBase:
        provider_class = self.registry.resolve(query_definition.type)
        resolved_config = _resolve_query_provider_config(
            self.provider_config_resolver, directory_path
        )
        metadata: dict[str, Any] = {"directory_path": directory_path or "root"}
        if graph_id is not None:
            metadata["graph_id"] = graph_id
        runtime_context = QueryRuntimeContext(
            provider_config=query_provider_config_blob(resolved_config),
            provider_config_resolver=self.provider_config_resolver,
            vm_inventory=self._provider_runtime_ports.vm_inventory
            or self._provider_runtime_ports.gate_vm_inventory,
            git_merge_base_reader=self._provider_runtime_ports.git_merge_base_reader,
            git_pr_note_reader=self._provider_runtime_ports.git_pr_note_reader,
            issue_reader=self._provider_runtime_ports.issue_reader,
            session_lookup=self._provider_runtime_ports.session_lookup,
            session_context_reader=self._provider_runtime_ports.session_context_reader,
            session_artifact_reader=self._provider_runtime_ports.session_artifact_reader,
            admin_service=self._admin_adapter,
            env=self.env,
            secret_resolver=self.secret_resolver,
            metadata=metadata,
        )
        provider = provider_class()
        validated_definition = _validate_query_definition(provider_class, query_definition)
        provider.initialize(query_definition=validated_definition, runtime_context=runtime_context)
        return provider


def _validate_query_definition(
    provider_class: QueryProviderClass,
    query_definition: QueryDefinitionDiscriminated,
) -> QueryDefinitionDiscriminated:
    if type(query_definition) is WorkflowNodeEnvelope:
        return provider_class.validate_definition(query_definition)
    return query_definition


def _resolve_query_provider_config(
    provider_config_resolver: ProviderConfigResolver,
    directory_path: str | None,
) -> QueryProviderConfig:
    resolve_provider_config = getattr(
        provider_config_resolver, "resolve_query_provider_config", None
    )
    if callable(resolve_provider_config):
        return resolve_provider_config(directory_path)
    resolve_for_directory = getattr(provider_config_resolver, "resolve_for_directory", None)
    if callable(resolve_for_directory):
        return QueryProviderConfig.model_validate(
            _model_dump(resolve_for_directory(directory_path))
        )
    raise AttributeError("provider_config_resolver must resolve query provider config")


def _model_dump(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value
