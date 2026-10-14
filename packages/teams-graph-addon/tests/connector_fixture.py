from evander.connectors.models import ConnectorDirectoryConfig


def _config(
    *,
    base_url: str = "https://files.example.test",
    auth_profile: str | None = None,
    requires_approval: bool = False,
    access: str = "read",
    result_mode: str = "json",
    method: str = "GET",
) -> ConnectorDirectoryConfig:
    payload: dict[str, object] = {
        "connectors": {
            "documents": {
                "display_name": "Documents",
                "description": "Approved document service",
                "driver": "rest",
                "config": {"base_url": base_url},
                "operations": {
                    "get_document": {
                        "description": "Fetch one document",
                        "input_schema": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                            "additionalProperties": False,
                        },
                        "access": access,
                        "requires_approval": requires_approval,
                        "result_mode": result_mode,
                        "config": {"method": method, "path": "/documents/{id}"},
                    }
                },
            }
        }
    }
    if auth_profile is not None:
        payload["auth_profiles"] = {
            auth_profile: {"type": "bearer_env", "token_env": "DOCUMENTS_TOKEN"}
        }
        payload["connectors"]["documents"]["auth_profile"] = auth_profile  # type: ignore[index]
    return ConnectorDirectoryConfig.model_validate(payload)
