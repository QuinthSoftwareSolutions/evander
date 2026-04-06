from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_sandbox.admin_service import AdminService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.models import QueryDefinitionDiscriminated


@dataclass(frozen=True)
class QueryRuntimeContext:
    """Host-side runtime inputs used by query providers."""

    resolved_config: GatewayConfig
    admin_service: AdminService
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    secret_resolver: Callable[[str], str | None] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolve_secret(self, ref: str) -> str | None:
        normalized_ref = ref.strip()
        if not normalized_ref:
            return None
        if self.secret_resolver is not None:
            return self.secret_resolver(normalized_ref)
        return self.env.get(normalized_ref)


class QueryProvider(Protocol):
    def initialize(
        self,
        query_definition: QueryDefinitionDiscriminated,
        runtime_context: QueryRuntimeContext,
    ) -> None:
        ...

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        ...


class QueryProviderBase:
    """Convenience base class for query provider implementations."""

    def __init__(self) -> None:
        self.query_definition: QueryDefinitionDiscriminated | None = None
        self.runtime_context: QueryRuntimeContext | None = None

    def initialize(
        self,
        query_definition: QueryDefinitionDiscriminated,
        runtime_context: QueryRuntimeContext,
    ) -> None:
        self.query_definition = query_definition
        self.runtime_context = runtime_context

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del resolved_config
        return {}
