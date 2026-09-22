from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, Field, field_validator, model_validator
from referencing import Registry


CONNECTOR_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
CONNECTOR_OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
CONNECTOR_DRIVER_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
CONNECTOR_DEFAULT_TIMEOUT_SECONDS = 30.0
CONNECTOR_MAX_TIMEOUT_SECONDS = 300.0
CONNECTOR_DEFAULT_ARTIFACT_BYTES = 10 * 1024 * 1024
CONNECTOR_MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
CONNECTOR_MAX_INLINE_BYTES = 1024 * 1024
PROTECTED_CONNECTOR_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "host",
        "connection",
        "content-length",
        "transfer-encoding",
        "proxy-authorization",
        "mcp-session-id",
        "x-evander-mcp-session-token",
        "evander-mcp-session-token",
    }
)
_NO_REMOTE_SCHEMA_REGISTRY = Registry()


@dataclass(frozen=True)
class _ObjectSchemaCompatibility:
    may_match: bool
    matches_every_object: bool


_OBJECT_SCHEMA_NONE = _ObjectSchemaCompatibility(False, False)
_OBJECT_SCHEMA_SOME = _ObjectSchemaCompatibility(True, False)
_OBJECT_SCHEMA_ALL = _ObjectSchemaCompatibility(True, True)


def _object_schema_and(
    left: _ObjectSchemaCompatibility,
    right: _ObjectSchemaCompatibility,
) -> _ObjectSchemaCompatibility:
    if not left.may_match or not right.may_match:
        return _OBJECT_SCHEMA_NONE
    return _ObjectSchemaCompatibility(
        may_match=True,
        matches_every_object=left.matches_every_object and right.matches_every_object,
    )


def _object_schema_or(
    left: _ObjectSchemaCompatibility,
    right: _ObjectSchemaCompatibility,
) -> _ObjectSchemaCompatibility:
    return _ObjectSchemaCompatibility(
        may_match=left.may_match or right.may_match,
        matches_every_object=left.matches_every_object or right.matches_every_object,
    )


def _object_schema_not(
    compatibility: _ObjectSchemaCompatibility,
) -> _ObjectSchemaCompatibility:
    return _ObjectSchemaCompatibility(
        may_match=not compatibility.matches_every_object,
        matches_every_object=not compatibility.may_match,
    )


def _resolve_local_schema_reference(root: dict[str, Any], reference: str) -> Any | None:
    fragment = unquote(reference[1:])
    if not fragment:
        return root
    if fragment.startswith("/"):
        current: Any = root
        for encoded_token in fragment[1:].split("/"):
            token = encoded_token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict) and token in current:
                current = current[token]
            elif isinstance(current, list):
                try:
                    current = current[int(token)]
                except (IndexError, ValueError):
                    return None
            else:
                return None
        return current

    pending: list[Any] = [root]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if current.get("$anchor") == fragment or current.get("$dynamicAnchor") == fragment:
                return current
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return None


def _schema_object_compatibility(
    schema: Any,
    *,
    root: dict[str, Any],
    resolving: frozenset[str] = frozenset(),
) -> _ObjectSchemaCompatibility:
    """Return conservative lower and upper bounds for object-shaped instances.

    ``may_match=False`` is only returned when the complete schema proves that no object can
    validate. ``matches_every_object=True`` is similarly reserved for schemas proven to accept
    every object, which lets negation remain safe without rejecting schemas whose property-level
    satisfiability is merely unknown.
    """
    if isinstance(schema, bool):
        return _OBJECT_SCHEMA_ALL if schema else _OBJECT_SCHEMA_NONE
    if not isinstance(schema, dict):
        return _OBJECT_SCHEMA_SOME

    compatibility = _OBJECT_SCHEMA_ALL
    declared_type = schema.get("type")
    if declared_type is not None:
        declared_types = (
            {declared_type}
            if isinstance(declared_type, str)
            else set(declared_type)
            if isinstance(declared_type, list)
            else set()
        )
        if "object" not in declared_types:
            return _OBJECT_SCHEMA_NONE

    if "const" in schema:
        compatibility = _object_schema_and(
            compatibility,
            _OBJECT_SCHEMA_SOME if isinstance(schema["const"], dict) else _OBJECT_SCHEMA_NONE,
        )
    if "enum" in schema:
        enum_allows_object = any(isinstance(item, dict) for item in schema["enum"])
        compatibility = _object_schema_and(
            compatibility,
            _OBJECT_SCHEMA_SOME if enum_allows_object else _OBJECT_SCHEMA_NONE,
        )

    required = schema.get("required") or []
    min_properties = int(schema.get("minProperties") or 0)
    max_properties = schema.get("maxProperties")
    if max_properties is not None and (
        min_properties > int(max_properties) or len(set(required)) > int(max_properties)
    ):
        return _OBJECT_SCHEMA_NONE
    if schema.get("propertyNames") is False and (min_properties or required):
        return _OBJECT_SCHEMA_NONE

    properties_restrict = any(
        subschema not in ({}, True) for subschema in (schema.get("properties") or {}).values()
    )
    patterns_restrict = any(
        subschema not in ({}, True)
        for subschema in (schema.get("patternProperties") or {}).values()
    )
    additional_properties_restrict = schema.get("additionalProperties", True) not in ({}, True)
    unevaluated_properties_restrict = schema.get("unevaluatedProperties", True) not in ({}, True)
    property_names_restrict = schema.get("propertyNames", True) not in ({}, True)
    dependent_required_restrict = any((schema.get("dependentRequired") or {}).values())
    dependent_schemas_restrict = any(
        subschema not in ({}, True) for subschema in (schema.get("dependentSchemas") or {}).values()
    )
    if (
        min_properties
        or max_properties is not None
        or required
        or properties_restrict
        or patterns_restrict
        or additional_properties_restrict
        or unevaluated_properties_restrict
        or property_names_restrict
        or dependent_required_restrict
        or dependent_schemas_restrict
    ):
        compatibility = _object_schema_and(compatibility, _OBJECT_SCHEMA_SOME)

    for keyword in ("$ref", "$dynamicRef"):
        reference = schema.get(keyword)
        if not isinstance(reference, str) or not reference.startswith("#"):
            continue
        if reference in resolving:
            referenced = _OBJECT_SCHEMA_SOME
        else:
            target = _resolve_local_schema_reference(root, reference)
            referenced = (
                _schema_object_compatibility(
                    target,
                    root=root,
                    resolving=resolving | {reference},
                )
                if target is not None
                else _OBJECT_SCHEMA_SOME
            )
        compatibility = _object_schema_and(compatibility, referenced)

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for subschema in all_of:
            compatibility = _object_schema_and(
                compatibility,
                _schema_object_compatibility(subschema, root=root, resolving=resolving),
            )

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        any_compatibility = _OBJECT_SCHEMA_NONE
        for subschema in any_of:
            any_compatibility = _object_schema_or(
                any_compatibility,
                _schema_object_compatibility(subschema, root=root, resolving=resolving),
            )
        compatibility = _object_schema_and(compatibility, any_compatibility)

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        branch_compatibilities = [
            _schema_object_compatibility(subschema, root=root, resolving=resolving)
            for subschema in one_of
        ]
        possible_branches = [item for item in branch_compatibilities if item.may_match]
        universal_branches = [item for item in branch_compatibilities if item.matches_every_object]
        if not possible_branches or len(universal_branches) > 1:
            one_compatibility = _OBJECT_SCHEMA_NONE
        elif len(universal_branches) == 1 and len(possible_branches) == 1:
            one_compatibility = _OBJECT_SCHEMA_ALL
        else:
            one_compatibility = _OBJECT_SCHEMA_SOME
        compatibility = _object_schema_and(compatibility, one_compatibility)

    if "not" in schema:
        compatibility = _object_schema_and(
            compatibility,
            _object_schema_not(
                _schema_object_compatibility(schema["not"], root=root, resolving=resolving)
            ),
        )

    if "if" in schema:
        condition = _schema_object_compatibility(schema["if"], root=root, resolving=resolving)
        then_schema = _schema_object_compatibility(
            schema.get("then", True), root=root, resolving=resolving
        )
        else_schema = _schema_object_compatibility(
            schema.get("else", True), root=root, resolving=resolving
        )
        conditional = _object_schema_or(
            _object_schema_and(condition, then_schema),
            _object_schema_and(_object_schema_not(condition), else_schema),
        )
        compatibility = _object_schema_and(compatibility, conditional)

    return compatibility


def _validate_input_schema_contract(value: dict[str, Any]) -> None:
    pending: list[Any] = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for keyword in ("$ref", "$dynamicRef"):
                reference = current.get(keyword)
                if isinstance(reference, str) and reference and not reference.startswith("#"):
                    raise ValueError(f"input_schema {keyword} must be a local fragment reference")
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)

    if not _schema_object_compatibility(value, root=value).may_match:
        raise ValueError("input_schema must allow object arguments")


def normalize_connector_id(value: object, *, field_name: str = "connector_id") -> str:
    normalized = str(value or "").strip().lower()
    if not CONNECTOR_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must use lowercase letters, numbers, dots, underscores, or hyphens"
        )
    return normalized


def normalize_connector_operation(value: object, *, field_name: str = "operation") -> str:
    normalized = str(value or "").strip().lower()
    if not CONNECTOR_OPERATION_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must start with a lowercase letter and contain only lowercase "
            "letters, numbers, dots, underscores, or hyphens"
        )
    return normalized


def _http_url(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"{field_name} must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise ValueError(f"{field_name} must not contain credentials")
    if parts.fragment:
        raise ValueError(f"{field_name} must not contain a fragment")
    return text.rstrip("/")


class ConnectorAuthProfile(BaseModel):
    type: Literal[
        "none",
        "bearer",
        "api_key_header",
        "basic",
        "bearer_env",
        "api_key_header_env",
        "basic_env",
        "oauth2_client_credentials",
    ]
    credential_ref: str | None = Field(default=None, pattern=r"^cred_[0-9a-f]{32}$")
    token_env: str | None = None
    header_name: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    token_url: str | None = None
    client_id_env: str | None = None
    client_secret_env: str | None = None
    scopes: list[str] = Field(default_factory=list)
    audience: str | None = None
    client_auth_method: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic"

    @field_validator(
        "token_env",
        "header_name",
        "username_env",
        "password_env",
        "client_id_env",
        "client_secret_env",
        "audience",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    @field_validator("token_url", mode="before")
    @classmethod
    def normalize_token_url(cls, value: object) -> str | None:
        if value is None or not str(value).strip():
            return None
        return _http_url(value, field_name="token_url")

    @field_validator("scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = value.split()
        if not isinstance(value, (list, tuple)):
            raise ValueError("scopes must be a list of strings")
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))

    @model_validator(mode="after")
    def validate_required_fields(self) -> "ConnectorAuthProfile":
        managed = self.type in {"bearer", "api_key_header", "basic"} or self.credential_ref is not None
        if managed:
            if self.type in {"none", "bearer_env", "api_key_header_env", "basic_env"}:
                raise ValueError("Managed credentials require a managed authentication type")
            if not self.credential_ref or any(getattr(self, field) for field in (
                "token_env", "username_env", "password_env", "client_id_env", "client_secret_env"
            )):
                raise ValueError("Managed authentication requires one reference and no environment fields")
            if self.type == "api_key_header" and not self.header_name:
                raise ValueError("API key authentication requires header_name")
            if self.type == "oauth2_client_credentials" and not self.token_url:
                raise ValueError("OAuth authentication requires token_url")
        required: dict[str, tuple[str, ...]] = {
            "bearer_env": ("token_env",),
            "api_key_header_env": ("token_env", "header_name"),
            "basic_env": ("username_env", "password_env"),
            "oauth2_client_credentials": (
                "token_url",
                "client_id_env",
                "client_secret_env",
            ),
        }
        missing = [] if managed else [name for name in required.get(self.type, ()) if not getattr(self, name)]
        if missing:
            raise ValueError(f"auth profile type {self.type!r} requires: {', '.join(missing)}")
        if self.header_name is not None:
            name = self.header_name.lower()
            if name in PROTECTED_CONNECTOR_HEADERS:
                raise ValueError("api_key_header_env header_name is reserved")
            if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", self.header_name):
                raise ValueError("header_name is not a valid HTTP header name")
        return self


class ConnectorOperationDefinition(BaseModel):
    description: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "additionalProperties": False}
    )
    access: Literal["read", "write"]
    requires_approval: bool
    result_mode: Literal["json", "text", "artifact"]
    timeout_seconds: float = Field(
        default=CONNECTOR_DEFAULT_TIMEOUT_SECONDS,
        gt=0,
        le=CONNECTOR_MAX_TIMEOUT_SECONDS,
    )
    max_response_bytes: int | None = Field(default=None, gt=0, le=CONNECTOR_MAX_ARTIFACT_BYTES)
    config: dict[str, Any] = Field(default_factory=dict)
    disabled: bool = False

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("operation description must be non-empty")
        return text

    @field_validator("input_schema")
    @classmethod
    def validate_input_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError as exc:
            raise ValueError(f"input_schema is not valid JSON Schema: {exc.message}") from exc
        _validate_input_schema_contract(value)
        return value

    @model_validator(mode="after")
    def apply_result_limit(self) -> "ConnectorOperationDefinition":
        if self.max_response_bytes is None:
            self.max_response_bytes = (
                CONNECTOR_DEFAULT_ARTIFACT_BYTES
                if self.result_mode == "artifact"
                else CONNECTOR_MAX_INLINE_BYTES
            )
        if self.result_mode != "artifact" and self.max_response_bytes > CONNECTOR_MAX_INLINE_BYTES:
            raise ValueError(
                f"inline connector results cannot exceed {CONNECTOR_MAX_INLINE_BYTES} bytes"
            )
        return self


class ConnectorDefinition(BaseModel):
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    driver: str
    auth_profile: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    operations: dict[str, ConnectorOperationDefinition] = Field(default_factory=dict)
    health_operation: str | None = None
    health_arguments: dict[str, Any] = Field(default_factory=dict)
    disabled: bool = False

    @field_validator("display_name", "description", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("value must be non-empty")
        return text

    @field_validator("driver", mode="before")
    @classmethod
    def normalize_driver(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not CONNECTOR_DRIVER_PATTERN.fullmatch(text):
            raise ValueError("driver must be a lowercase connector driver name")
        return text

    @field_validator("auth_profile", mode="before")
    @classmethod
    def normalize_auth_profile(cls, value: object) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_connector_id(value, field_name="auth_profile")

    @field_validator("operations", mode="before")
    @classmethod
    def normalize_operations(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("operations must be an object")
        return {normalize_connector_operation(name): operation for name, operation in value.items()}

    @field_validator("health_operation", mode="before")
    @classmethod
    def normalize_health_operation(cls, value: object) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_connector_operation(value, field_name="health_operation")

    @model_validator(mode="after")
    def validate_health_operation(self) -> "ConnectorDefinition":
        if self.health_operation is not None:
            operation = self.operations.get(self.health_operation)
            if operation is None or operation.disabled:
                raise ValueError("health_operation must name an enabled operation")
            if operation.access != "read" or operation.requires_approval:
                raise ValueError("health_operation must be an unapproved read operation")
            errors = sorted(
                Draft202012Validator(
                    operation.input_schema,
                    registry=_NO_REMOTE_SCHEMA_REGISTRY,
                ).iter_errors(self.health_arguments),
                key=lambda error: list(error.absolute_path),
            )
            if errors:
                raise ValueError(
                    f"health_arguments do not match health operation schema: {errors[0].message}"
                )
        return self


class ConnectorDirectoryConfig(BaseModel):
    auth_profiles: dict[str, ConnectorAuthProfile] = Field(default_factory=dict)
    connectors: dict[str, ConnectorDefinition] = Field(default_factory=dict)

    @field_validator("auth_profiles", "connectors", mode="before")
    @classmethod
    def normalize_keys(cls, value: object, info) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError(f"{info.field_name} must be an object")
        return {
            normalize_connector_id(key, field_name=f"{info.field_name} key"): item
            for key, item in value.items()
        }

    @model_validator(mode="after")
    def validate_references(self) -> "ConnectorDirectoryConfig":
        for connector_id, connector in self.connectors.items():
            if connector.disabled:
                continue
            if connector.auth_profile and connector.auth_profile not in self.auth_profiles:
                raise ValueError(
                    f"connector {connector_id!r} references unknown auth profile "
                    f"{connector.auth_profile!r}"
                )
            if not connector.operations:
                raise ValueError(f"connector {connector_id!r} must define operations")
        return self


def validate_connector_directory_overlay(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("connector directory config must be an object")
    unknown = sorted(set(payload) - {"auth_profiles", "connectors"})
    if unknown:
        raise ValueError("connector directory config does not allow fields: " + ", ".join(unknown))
    normalized: dict[str, Any] = {}
    if "auth_profiles" in payload:
        raw_profiles = payload["auth_profiles"]
        if not isinstance(raw_profiles, dict):
            raise ValueError("auth_profiles must be an object")
        profiles: dict[str, Any] = {}
        for raw_id, raw_profile in raw_profiles.items():
            profile_id = normalize_connector_id(raw_id, field_name="auth_profiles key")
            if not isinstance(raw_profile, dict):
                raise ValueError(f"auth profile {profile_id!r} must be an object")
            allowed_profile_fields = set(ConnectorAuthProfile.model_fields)
            unknown_profile_fields = sorted(set(raw_profile) - allowed_profile_fields)
            if unknown_profile_fields:
                raise ValueError(
                    f"auth profile {profile_id!r} does not allow fields: "
                    + ", ".join(unknown_profile_fields)
                )
            if "type" in raw_profile:
                profile_type = str(raw_profile["type"] or "").strip()
                if profile_type not in {
                    "none",
                    "bearer",
                    "api_key_header",
                    "basic",
                    "bearer_env",
                    "api_key_header_env",
                    "basic_env",
                    "oauth2_client_credentials",
                }:
                    raise ValueError(f"auth profile {profile_id!r} has unsupported type")
            profiles[profile_id] = dict(raw_profile)
        normalized["auth_profiles"] = profiles
    if "connectors" in payload:
        raw_connectors = payload["connectors"]
        if not isinstance(raw_connectors, dict):
            raise ValueError("connectors must be an object")
        connectors: dict[str, Any] = {}
        for raw_id, raw_connector in raw_connectors.items():
            connector_id = normalize_connector_id(raw_id, field_name="connectors key")
            if not isinstance(raw_connector, dict):
                raise ValueError(f"connector {connector_id!r} must be an object")
            allowed_connector_fields = set(ConnectorDefinition.model_fields)
            unknown_connector_fields = sorted(set(raw_connector) - allowed_connector_fields)
            if unknown_connector_fields:
                raise ValueError(
                    f"connector {connector_id!r} does not allow fields: "
                    + ", ".join(unknown_connector_fields)
                )
            normalized_connector = dict(raw_connector)
            if "driver" in normalized_connector:
                normalized_connector["driver"] = ConnectorDefinition.normalize_driver(
                    normalized_connector["driver"]
                )
            if "auth_profile" in normalized_connector:
                normalized_connector["auth_profile"] = ConnectorDefinition.normalize_auth_profile(
                    normalized_connector["auth_profile"]
                )
            if "health_operation" in normalized_connector:
                normalized_connector["health_operation"] = (
                    ConnectorDefinition.normalize_health_operation(
                        normalized_connector["health_operation"]
                    )
                )
            for object_field in ("config", "health_arguments"):
                if object_field in normalized_connector and not isinstance(
                    normalized_connector[object_field], dict
                ):
                    raise ValueError(f"connector {connector_id!r} {object_field} must be an object")
            if "disabled" in normalized_connector and not isinstance(
                normalized_connector["disabled"], bool
            ):
                raise ValueError(f"connector {connector_id!r} disabled must be boolean")
            if "operations" in normalized_connector:
                raw_operations = normalized_connector["operations"]
                if not isinstance(raw_operations, dict):
                    raise ValueError(f"connector {connector_id!r} operations must be an object")
                normalized_operations: dict[str, Any] = {}
                for raw_operation_name, raw_operation in raw_operations.items():
                    operation_name = normalize_connector_operation(raw_operation_name)
                    normalized_operations[operation_name] = _validate_operation_overlay(
                        connector_id, operation_name, raw_operation
                    )
                normalized_connector["operations"] = normalized_operations
            connectors[connector_id] = normalized_connector
        normalized["connectors"] = connectors
    return normalized


def _validate_operation_overlay(
    connector_id: str, operation_name: str, payload: object
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"connector operation {connector_id}.{operation_name} must be an object")
    allowed_fields = set(ConnectorOperationDefinition.model_fields)
    unknown = sorted(set(payload) - allowed_fields)
    if unknown:
        raise ValueError(
            f"connector operation {connector_id}.{operation_name} does not allow fields: "
            + ", ".join(unknown)
        )
    normalized = dict(payload)
    if "description" in normalized:
        normalized["description"] = ConnectorOperationDefinition.normalize_description(
            normalized["description"]
        )
    if "input_schema" in normalized:
        if not isinstance(normalized["input_schema"], dict):
            raise ValueError(
                f"connector operation {connector_id}.{operation_name} input_schema must be an object"
            )
        ConnectorOperationDefinition.validate_input_schema(normalized["input_schema"])
    for name, allowed in (
        ("access", {"read", "write"}),
        ("result_mode", {"json", "text", "artifact"}),
    ):
        if name in normalized and normalized[name] not in allowed:
            raise ValueError(
                f"connector operation {connector_id}.{operation_name} has invalid {name}"
            )
    for name in ("requires_approval", "disabled"):
        if name in normalized and not isinstance(normalized[name], bool):
            raise ValueError(
                f"connector operation {connector_id}.{operation_name} {name} must be boolean"
            )
    if "config" in normalized and not isinstance(normalized["config"], dict):
        raise ValueError(
            f"connector operation {connector_id}.{operation_name} config must be an object"
        )
    if "timeout_seconds" in normalized:
        timeout = float(normalized["timeout_seconds"])
        if timeout <= 0 or timeout > CONNECTOR_MAX_TIMEOUT_SECONDS:
            raise ValueError(
                f"connector operation {connector_id}.{operation_name} timeout_seconds is out of range"
            )
        normalized["timeout_seconds"] = timeout
    if "max_response_bytes" in normalized and normalized["max_response_bytes"] is not None:
        max_bytes = int(normalized["max_response_bytes"])
        if max_bytes <= 0 or max_bytes > CONNECTOR_MAX_ARTIFACT_BYTES:
            raise ValueError(
                f"connector operation {connector_id}.{operation_name} max_response_bytes is out of range"
            )
        normalized["max_response_bytes"] = max_bytes
    return normalized


class ConnectorGrant(BaseModel):
    connector_id: str
    operations: list[str]

    @field_validator("connector_id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        return normalize_connector_id(value)

    @field_validator("operations", mode="before")
    @classmethod
    def normalize_granted_operations(cls, value: object) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("operations must be a list")
        return list(dict.fromkeys(normalize_connector_operation(item) for item in value))


class ConnectorArtifactResult(BaseModel):
    artifact_id: str
    filename: str
    mime_type: str | None = None
    byte_count: int
    sha256: str
    expires_at: str


class ConnectorInvocationResult(BaseModel):
    connector_id: str
    operation: str
    result_mode: Literal["json", "text", "artifact"]
    value: Any | None = None
    artifact: ConnectorArtifactResult | None = None
    upstream_status: int | None = None
    duration_ms: int | None = None


def normalize_connector_grants(value: object) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("connector_grants must be an object mapping connector ids to operations")
    normalized: dict[str, list[str]] = {}
    for connector_id, operations in value.items():
        grant = ConnectorGrant(connector_id=connector_id, operations=operations)
        normalized[grant.connector_id] = grant.operations
    return normalized
