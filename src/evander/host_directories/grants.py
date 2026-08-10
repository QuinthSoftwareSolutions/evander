from __future__ import annotations

from evander.host_directories.models import (
    HostDirectoryDirectoryConfig,
    normalize_host_directory_grants,
)

_TOOL_BY_CAPABILITY = {
    "list": "host_directory_list",
    "import": "host_directory_import",
    "delete": "host_directory_delete",
}


def host_directory_tool_names_for_grants(
    grants: dict[str, list[str]],
) -> list[str]:
    """Return the stable MCP tools needed by a normalized grant mapping."""

    required: list[str] = []
    for capability in _TOOL_BY_CAPABILITY:
        if any(capability in capabilities for capabilities in grants.values()):
            required.append(_TOOL_BY_CAPABILITY[capability])
    return required


def validate_host_directory_grant_mapping(
    config: HostDirectoryDirectoryConfig,
    grants: dict[str, list[str]],
    *,
    session_id: str,
) -> None:
    for directory_id, capabilities in grants.items():
        definition = config.host_directories.get(directory_id)
        if definition is None or definition.disabled:
            raise ValueError(
                f"host directory grant {directory_id!r} for session {session_id!r} "
                "references an unavailable directory"
            )
        unavailable = sorted(set(capabilities) - set(definition.capabilities))
        if unavailable:
            raise ValueError(
                f"host directory grant {directory_id!r} for session {session_id!r} "
                "includes unavailable capabilities: " + ", ".join(unavailable)
            )


def resolve_and_validate_host_directory_grants(
    *,
    provider_config: object,
    directory_path: str,
    session_id: str,
    value: object,
) -> dict[str, list[str]]:
    grants = normalize_host_directory_grants(value)
    resolver = getattr(provider_config, "resolve_host_directories_for_directory", None)
    if not callable(resolver):
        if grants:
            raise ValueError("host directory configuration is unavailable")
        return grants
    config = resolver(directory_path)
    validate_host_directory_grant_mapping(config, grants, session_id=session_id)
    return grants
