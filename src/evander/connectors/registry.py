from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from evander.connectors.models import (
    ConnectorDefinition,
    ConnectorInvocationResult,
    ConnectorOperationDefinition,
)


@dataclass(frozen=True)
class ConnectorCallContext:
    session_id: str
    directory_path: str
    runtime_dir: Any
    artifact_store: Any
    auth_headers: dict[str, str] = field(default_factory=dict, repr=False)
    sensitive_values: tuple[str, ...] = field(default=(), repr=False)
    # Host-only capabilities for domain drivers that execute durable operations.
    # These objects never cross the connector result boundary.
    host_service: Any = field(default=None, repr=False)
    authorize: Any = field(default=None, repr=False)


class ConnectorDriver(Protocol):
    def validate_definition(self, definition: ConnectorDefinition) -> None: ...

    async def invoke(
        self,
        *,
        context: ConnectorCallContext,
        connector_id: str,
        definition: ConnectorDefinition,
        operation_name: str,
        operation: ConnectorOperationDefinition,
        arguments: dict[str, Any],
    ) -> ConnectorInvocationResult: ...


class ConnectorRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, ConnectorDriver] = {}

    @property
    def driver_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._drivers))

    def register(self, name: str, driver: ConnectorDriver) -> None:
        normalized = str(name or "").strip().lower()
        if not normalized:
            raise ValueError("connector driver name must be non-empty")
        if normalized in self._drivers:
            raise ValueError(f"duplicate connector driver: {normalized}")
        self._drivers[normalized] = driver

    def require(self, name: str) -> ConnectorDriver:
        normalized = str(name or "").strip().lower()
        driver = self._drivers.get(normalized)
        if driver is None:
            raise ValueError(f"unknown connector driver: {normalized}")
        return driver
