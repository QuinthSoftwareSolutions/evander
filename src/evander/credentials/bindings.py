from __future__ import annotations

from .store import CredentialError, binding_fingerprint


def connector_binding(profile_id, connector_id, profile, connector) -> dict:
    # Normalize legacy and managed representations to the same protocol binding.
    protocol = profile.model_dump(mode="json", exclude_none=True)
    for key in (
        "credential_ref",
        "token_env",
        "username_env",
        "password_env",
        "client_id_env",
        "client_secret_env",
    ):
        protocol.pop(key, None)
    protocol["type"] = protocol["type"].removesuffix("_env")
    definition = connector.model_dump(mode="json", exclude_none=True)
    for key in ("display_name", "description", "disabled"):
        definition.pop(key, None)
    return {
        "consumer": "connector",
        "profile_id": profile_id,
        "connector_id": connector_id,
        "destination": str(connector.config.get("base_url") or connector.driver),
        "token_endpoint": profile.token_url,
        "operations": sorted(connector.operations),
        "fingerprint": binding_fingerprint({"protocol": protocol, "definition": definition}),
    }


def connector_fields(profile) -> tuple[str, ...]:
    mode = profile.type.removesuffix("_env")
    return {
        "bearer": ("token",),
        "api_key_header": ("token",),
        "basic": ("username", "password"),
        "oauth2_client_credentials": ("client_id", "client_secret"),
    }.get(mode, ())


def validate_connector_values(profile, values: dict) -> None:
    fields = connector_fields(profile)
    if (
        not fields
        or not isinstance(values, dict)
        or set(values) != set(fields)
        or any(
            not isinstance(v, str) or not v or len(v) > 8192 or "\r" in v or "\n" in v
            for v in values.values()
        )
    ):
        raise CredentialError("Enter all required credential fields as nonempty single-line values")


def teams_binding(config, directory: str) -> dict:
    return {
        "consumer": "teams",
        "directory": directory,
        "identity_id": config.teams_delegated_identity_id,
        "client_id": config.teams_delegated_client_id,
        "tenant": config.teams_delegated_tenant_id,
        "authority": config.teams_delegated_authority_base_url,
        "destination": config.teams_graph_api_base_url,
        "scopes": sorted(config.teams_delegated_scopes),
        "fingerprint": binding_fingerprint(
            {
                "client_id": config.teams_delegated_client_id,
                "tenant": config.teams_delegated_tenant_id,
                "authority": config.teams_delegated_authority_base_url,
                "destination": config.teams_graph_api_base_url,
                "scopes": sorted(config.teams_delegated_scopes),
            }
        ),
    }
