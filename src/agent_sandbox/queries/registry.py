from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from agent_sandbox.admin_service import AdminService
from agent_sandbox.events.models import QueryDefinitionDiscriminated, QueryType
from agent_sandbox.provider_config import ProviderConfigResolver

from .providers.base import QueryProviderBase, QueryRuntimeContext
from .providers.git_pr_note import GitPrNoteQueryProvider
from .providers.issue_get import IssueGetQueryProvider
from .providers.session_context import SessionContextQueryProvider
from .providers.vm_run_state import VmRunStateQueryProvider

QueryProviderClass = type[QueryProviderBase]


DEFAULT_QUERY_PROVIDER_REGISTRY: dict[QueryType, QueryProviderClass] = {
    "query:vm:run-state": VmRunStateQueryProvider,
    "query:git:pr-note": GitPrNoteQueryProvider,
    "query:session-context": SessionContextQueryProvider,
    "query:issue:get": IssueGetQueryProvider,
}


class QueryProviderRegistry:
    def __init__(self, initial: Mapping[QueryType, QueryProviderClass] | None = None) -> None:
        self._providers: dict[QueryType, QueryProviderClass] = dict(initial or DEFAULT_QUERY_PROVIDER_REGISTRY)

    def register(self, query_type: QueryType, provider_class: QueryProviderClass) -> None:
        self._providers[query_type] = provider_class

    def resolve(self, query_type: QueryType) -> QueryProviderClass:
        provider_class = self._providers.get(query_type)
        if provider_class is None:
            raise KeyError(f"No query provider registered for type {query_type!r}")
        return provider_class


class QueryProviderFactory:
    """Centralized provider constructor to keep env/secret handling host-side."""

    def __init__(
        self,
        provider_config_resolver: ProviderConfigResolver,
        admin_service: AdminService,
        *,
        registry: QueryProviderRegistry | None = None,
        env: Mapping[str, str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.provider_config_resolver = provider_config_resolver
        self.admin_service = admin_service
        self.registry = registry or QueryProviderRegistry()
        self.env = env or dict(os.environ)
        self.secret_resolver = secret_resolver

    def build(
        self,
        query_definition: QueryDefinitionDiscriminated,
        *,
        directory_path: str | None = None,
    ) -> QueryProviderBase:
        provider_class = self.registry.resolve(query_definition.type)
        resolved_config = self.provider_config_resolver.resolve_for_directory(directory_path)
        runtime_context = QueryRuntimeContext(
            resolved_config=resolved_config,
            admin_service=self.admin_service,
            env=self.env,
            secret_resolver=self.secret_resolver,
            metadata={"directory_path": directory_path or "root"},
        )
        provider = provider_class()
        validated_definition = provider_class.validate_definition(query_definition)
        provider.initialize(query_definition=validated_definition, runtime_context=runtime_context)
        return provider
