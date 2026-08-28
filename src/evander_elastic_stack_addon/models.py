from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import SplitResult, urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from evander.events.models import ConfigValue


ADDON_ID = "elastic-stack-addon"
ELASTIC_STACK_PROVIDER_STATE_KEY = "elastic_stack"
ELASTIC_STACK_SESSION_METADATA_KEY = "elastic_stack"

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def normalize_identifier(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must start with a lowercase letter or number and contain only "
            "lowercase letters, numbers, dots, underscores, or hyphens"
        )
    return normalized


def _http_url_parts(value: object, *, field_name: str) -> SplitResult:
    text = str(value or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an absolute http(s) URL") from exc
    if parts.scheme not in {"http", "https"} or not parts.netloc or not parts.hostname:
        raise ValueError(f"{field_name} must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise ValueError(f"{field_name} must not contain credentials")
    if parts.query or parts.fragment:
        raise ValueError(f"{field_name} must not contain a query or fragment")
    try:
        parts.port
    except ValueError as exc:
        raise ValueError(f"{field_name} must contain a valid port") from exc
    return parts


def _canonical_origin(parts: SplitResult) -> str:
    scheme = parts.scheme.lower()
    hostname = str(parts.hostname or "").lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    port = parts.port
    default_port = 443 if scheme == "https" else 80
    netloc = hostname if port is None or port == default_port else f"{hostname}:{port}"
    return f"{scheme}://{netloc}"


def canonical_http_origin(value: object, *, field_name: str) -> str:
    return _canonical_origin(_http_url_parts(value, field_name=field_name))


def normalize_base_url(value: object, *, field_name: str) -> str:
    parts = _http_url_parts(value, field_name=field_name)
    path = parts.path.rstrip("/")
    return f"{_canonical_origin(parts)}{path}"


def normalize_allowed_origin(value: object) -> str:
    parts = _http_url_parts(value, field_name="allowed_origins entry")
    hostname = str(parts.hostname or "")
    if (
        parts.path not in {"", "/"}
        or hostname in {"", "*"}
        or hostname.replace("*", "").strip(".") == ""
    ):
        raise ValueError("allowed_origins entries must be host-restricted http(s) origins")
    return _canonical_origin(parts)


def normalize_space_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "/" in text or "\\" in text or text in {".", ".."}:
        raise ValueError("space_id must be one path segment")
    return text


class ElasticStackAuthProfile(BaseModel):
    type: Literal["none", "basic_env", "api_key_env", "bearer_env"] = "none"
    username_env: str | None = None
    password_env: str | None = None
    token_env: str | None = None
    allowed_origins: list[str] = Field(min_length=1)

    @field_validator("username_env", "password_env", "token_env", mode="before")
    @classmethod
    def normalize_optional_env(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def normalize_allowed_origins(cls, value: object) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("allowed_origins must be a list")
        result: list[str] = []
        for item in value:
            result.append(normalize_allowed_origin(item))
        return list(dict.fromkeys(result))

    @model_validator(mode="after")
    def validate_secret_references(self) -> "ElasticStackAuthProfile":
        required = {
            "basic_env": ("username_env", "password_env"),
            "api_key_env": ("token_env",),
            "bearer_env": ("token_env",),
        }
        missing = [name for name in required.get(self.type, ()) if not getattr(self, name)]
        if missing:
            raise ValueError(f"auth profile type {self.type!r} requires: {', '.join(missing)}")
        return self


class ElasticStackEndpoint(BaseModel):
    base_url: str
    auth_profile: str

    @field_validator("base_url", mode="before")
    @classmethod
    def validate_base_url(cls, value: object) -> str:
        return normalize_base_url(value, field_name="base_url")

    @field_validator("auth_profile", mode="before")
    @classmethod
    def validate_auth_profile(cls, value: object) -> str:
        return normalize_identifier(value, field_name="auth_profile")


class ElasticStackTarget(BaseModel):
    kibana: ElasticStackEndpoint | None = None
    elasticsearch: ElasticStackEndpoint | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_endpoint(self) -> "ElasticStackTarget":
        if self.kibana is None and self.elasticsearch is None:
            raise ValueError("target must configure kibana or elasticsearch")
        return self

    def redacted_payload(self, *, target_id: str) -> dict[str, Any]:
        return {
            "target_id": target_id,
            "kibana_configured": self.kibana is not None,
            "elasticsearch_configured": self.elasticsearch is not None,
            "kibana_base_url": self.kibana.base_url if self.kibana else None,
            "elasticsearch_base_url": (self.elasticsearch.base_url if self.elasticsearch else None),
            "metadata": dict(self.metadata),
        }


class ElasticStackSettings(BaseModel):
    auth_profiles: dict[str, ElasticStackAuthProfile] = Field(default_factory=dict)
    targets: dict[str, ElasticStackTarget] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    max_import_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_import_objects: int = Field(default=1000, ge=1, le=10_000)

    @field_validator("auth_profiles", mode="before")
    @classmethod
    def normalize_auth_profile_ids(cls, value: object) -> dict[str, Any]:
        return _normalize_keyed_mapping(value, field_name="auth profile")

    @field_validator("targets", mode="before")
    @classmethod
    def normalize_target_ids(cls, value: object) -> dict[str, Any]:
        return _normalize_keyed_mapping(value, field_name="target")

    @model_validator(mode="after")
    def validate_target_profiles(self) -> "ElasticStackSettings":
        for target_id, target in self.targets.items():
            for endpoint_name in ("kibana", "elasticsearch"):
                endpoint = getattr(target, endpoint_name)
                if endpoint is not None and endpoint.auth_profile not in self.auth_profiles:
                    raise ValueError(
                        f"target {target_id!r} {endpoint_name} references unknown auth profile "
                        f"{endpoint.auth_profile!r}"
                    )
        return self


def settings_from_provider_state(
    provider_state: object,
    *,
    session_metadata: object | None = None,
) -> ElasticStackSettings:
    state = provider_state if isinstance(provider_state, dict) else {}
    raw_settings = state.get(ELASTIC_STACK_PROVIDER_STATE_KEY, {})
    if not isinstance(raw_settings, dict):
        raise ValueError("provider_state.elastic_stack must be an object")
    payload = dict(raw_settings)
    configured_targets = dict(payload.get("targets") or {})
    session = session_metadata if isinstance(session_metadata, dict) else {}
    session_section = session.get(ELASTIC_STACK_SESSION_METADATA_KEY, {})
    if session_section is not None and not isinstance(session_section, dict):
        raise ValueError("session metadata elastic_stack must be an object")
    session_targets = dict((session_section or {}).get("targets") or {})
    configured_targets.update(session_targets)
    payload["targets"] = configured_targets
    return ElasticStackSettings.model_validate(payload)


class _TargetedDefinition(BaseModel):
    target_id: ConfigValue
    session_id: ConfigValue | None = None
    space_id: ConfigValue | None = None
    outputs: dict[str, Any] | None = None

    @field_validator("target_id", mode="before")
    @classmethod
    def validate_target_id(cls, value: object) -> object:
        if isinstance(value, str) and ("${{" in value or "$(" in value):
            return value
        return normalize_identifier(value, field_name="target_id")

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session_id(cls, value: object) -> object | None:
        if value is None:
            return None
        if isinstance(value, str) and ("${{" in value or "$(" in value):
            return value
        text = str(value).strip()
        return text or None

    @field_validator("space_id", mode="before")
    @classmethod
    def validate_space_id(cls, value: object) -> object | None:
        if isinstance(value, str) and ("${{" in value or "$(" in value):
            return value
        return normalize_space_id(value)


class ElasticsearchClusterHealthQueryDefinition(_TargetedDefinition):
    type: Literal["query:elasticsearch:cluster-health"]


class ElasticsearchIndicesQueryDefinition(_TargetedDefinition):
    type: Literal["query:elasticsearch:indices"]
    index_pattern: ConfigValue = "*"
    include_hidden: ConfigValue = False


class ElasticsearchMappingsQueryDefinition(_TargetedDefinition):
    type: Literal["query:elasticsearch:mappings"]
    index_pattern: ConfigValue


class KibanaStatusQueryDefinition(_TargetedDefinition):
    type: Literal["query:kibana:status"]


class KibanaDataViewsQueryDefinition(_TargetedDefinition):
    type: Literal["query:kibana:data-views"]


class KibanaSavedObjectsQueryDefinition(_TargetedDefinition):
    type: Literal["query:kibana:saved-objects"]
    types: ConfigValue = Field(default_factory=lambda: ["dashboard", "visualization", "lens"])
    search: ConfigValue | None = None
    page: ConfigValue = 1
    per_page: ConfigValue = 100


class KibanaUpsertDataViewEffectDefinition(_TargetedDefinition):
    type: Literal["effect:kibana:upsert-data-view"]
    data_view: ConfigValue
    override: ConfigValue = True


class KibanaImportSavedObjectsEffectDefinition(_TargetedDefinition):
    type: Literal["effect:kibana:import-saved-objects"]
    ndjson: ConfigValue
    overwrite: ConfigValue = True
    create_new_copies: ConfigValue = False
    compatibility_mode: ConfigValue = False

    @model_validator(mode="after")
    def validate_import_options(self) -> "KibanaImportSavedObjectsEffectDefinition":
        if all(
            isinstance(value, bool)
            for value in (self.overwrite, self.create_new_copies, self.compatibility_mode)
        ):
            _validate_import_options(
                overwrite=self.overwrite,
                create_new_copies=self.create_new_copies,
                compatibility_mode=self.compatibility_mode,
            )
        return self


class KibanaSetDefaultDataViewEffectDefinition(_TargetedDefinition):
    type: Literal["effect:kibana:set-default-data-view"]
    data_view_id: ConfigValue | None = None
    force: ConfigValue = True


def _validate_import_options(
    *,
    overwrite: bool,
    create_new_copies: bool,
    compatibility_mode: bool,
) -> None:
    if create_new_copies and (overwrite or compatibility_mode):
        raise ValueError(
            "create_new_copies cannot be combined with overwrite or compatibility_mode"
        )
    if compatibility_mode and overwrite:
        raise ValueError("compatibility_mode cannot be combined with overwrite")


def _normalize_keyed_mapping(value: object, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name}s must be an object")
    result: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = normalize_identifier(raw_key, field_name=f"{field_name} id")
        if key in result:
            raise ValueError(f"duplicate {field_name} id: {key}")
        result[key] = item
    return result
