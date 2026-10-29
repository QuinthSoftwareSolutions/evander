from __future__ import annotations

from typing import Protocol

from evander.connectors.models import (
    ConnectorDirectoryConfig,
    normalize_connector_grants,
)


class ConnectorConfigResolver(Protocol):
    def resolve_connectors_for_directory(self, directory_path: str) -> ConnectorDirectoryConfig: ...


def validate_connector_grant_mapping(
    config: ConnectorDirectoryConfig,
    grants: dict[str, list[str]],
    *,
    session_id: str,
) -> None:
    """Validate normalized grants against one resolved connector catalog."""

    for connector_id, operation_names in grants.items():
        connector = config.connectors.get(connector_id)
        if connector is None or connector.disabled:
            raise ValueError(
                f"session {session_id!r} grants unknown or disabled connector {connector_id!r}"
            )
        for operation_name in operation_names:
            operation = connector.operations.get(operation_name)
            if operation is None or operation.disabled:
                raise ValueError(
                    f"session {session_id!r} grants unknown or disabled connector operation "
                    f"{connector_id}.{operation_name}"
                )


def resolve_and_validate_connector_grants(
    resolver: ConnectorConfigResolver | None,
    *,
    directory_path: str,
    session_id: str,
    value: object,
) -> dict[str, list[str]]:
    """Normalize grants and validate them through the canonical directory catalog."""

    grants = normalize_connector_grants(value)
    if not grants:
        return {}
    if resolver is None or not hasattr(resolver, "resolve_connectors_for_directory"):
        raise ValueError("connector grants require a directory connector configuration resolver")
    config = resolver.resolve_connectors_for_directory(directory_path)
    validate_connector_grant_mapping(config, grants, session_id=session_id)
    return grants
