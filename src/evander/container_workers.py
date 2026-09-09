from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer


class ContainerWorkerProfile(BaseModel):
    """Operator-approved connection overrides, selected by ID when creating an agent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str | None = None
    base_url: str = Field(min_length=1)
    token_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    git_allowed_from: str | None = None
    default_image: str = Field(default="evander-runner:latest", min_length=1)
    images: dict[str, str] = Field(default_factory=dict)
    workspace_persistence: str = Field(default="session", min_length=1)
    codex_auth_mode: Literal["metered_api", "chatgpt_subscription"] = "metered_api"
    guest_openai_base_url: str | None = None
    guest_anthropic_base_url: str | None = None
    guest_gateway_url: str | None = None
    guest_http_target_relay_base_url: str | None = None
    guest_http_target_browser_proxy_url: str | None = None
    guest_nuget_relay_base_url: str | None = None
    guest_pypi_index_url: str | None = None
    guest_npm_registry_url: str | None = None
    mssql_enabled: bool = False
    mssql_host: str | None = None
    mssql_port: int = Field(default=1433, gt=0, le=65535)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be an HTTP(S) worker URL without credentials or query")
        return value.rstrip("/")

    def connection_overrides(self) -> dict[str, object]:
        return {
            f"container_worker_{key}": value
            for key, value in self.model_dump(
                mode="json", exclude_unset=True, exclude={"display_name"}
            ).items()
        }

    @model_serializer(mode="wrap")
    def serialize_overrides(self, handler):
        # Provider config is repeatedly serialized and validated. Preserve omitted
        # fields so they keep inheriting directory defaults after each round trip.
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}
