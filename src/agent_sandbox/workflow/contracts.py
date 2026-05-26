from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_sandbox.models import DirectoryPath

NODE_OUTPUTS_CONTEXT_KEY = "__node_outputs"
RESERVED_OUTPUT_NAMES = frozenset({NODE_OUTPUTS_CONTEXT_KEY})


class WorkflowNodeEnvelope(BaseModel):
    id: str
    display_name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, dict[str, Any]] | None = None

    @model_validator(mode="before")
    @classmethod
    def collect_legacy_top_level_config(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        config = payload.get("config")
        if config is None:
            config = {}
        if not isinstance(config, dict):
            return value
        model_fields = set(cls.model_fields)
        for key in list(payload.keys()):
            if key in model_fields:
                continue
            config[key] = payload.pop(key)
        payload["config"] = config
        return payload

    @field_validator("id", "display_name", mode="before")
    @classmethod
    def normalize_non_empty_fields(cls, value: object, info) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @model_validator(mode="after")
    def validate_declared_output_names(self) -> "WorkflowNodeEnvelope":
        if self.outputs is not None:
            validate_output_mapping_keys(
                self.outputs,
                context=f"{self.type} {self.id} declared outputs",
            )
        return self

    def __getattr__(self, item: str) -> Any:
        config = object.__getattribute__(self, "config")
        if isinstance(config, dict) and item in config:
            return config[item]
        raise AttributeError(item)


class WorkflowNodeDefinition(WorkflowNodeEnvelope):
    """Backward-compatible alias for historical node model naming."""


class EdgeDefinition(BaseModel):
    source_id: str
    target_id: str
    source_port: str | None = None
    allow_cycle: bool = False

    @field_validator("source_id", "target_id", mode="before")
    @classmethod
    def normalize_node_id(cls, value: object, info) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator("source_port", mode="before")
    @classmethod
    def normalize_source_port(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


_NODE_OUTPUT_SCHEMA_REGISTRY: dict[str, dict[str, dict[str, Any]]] = {}
_OUTPUT_SCHEMA_BOOTSTRAP_ATTEMPTED = False


def register_node_output_schema(
    *, node_type: str, outputs: Mapping[str, Mapping[str, Any]]
) -> None:
    _NODE_OUTPUT_SCHEMA_REGISTRY[node_type] = {
        str(key): dict(value) for key, value in outputs.items()
    }


def get_node_output_schema_registry() -> Mapping[str, dict[str, dict[str, Any]]]:
    return _NODE_OUTPUT_SCHEMA_REGISTRY


def effective_node_output_schema(node: Any) -> dict[str, dict[str, Any]]:
    _ensure_output_schema_bootstrap()
    if isinstance(node, Mapping):
        node_type = str(node.get("type") or "").strip()
        raw_outputs = node.get("outputs")
        template = node.get("template")
        content = node.get("content")
        content_format = node.get("format")
    else:
        node_type = str(getattr(node, "type", "") or "").strip()
        raw_outputs = getattr(node, "outputs", None)
        template = getattr(node, "template", None)
        content = getattr(node, "content", None)
        content_format = getattr(node, "format", None)

    schema: dict[str, dict[str, Any]] = {}
    static_schema = _NODE_OUTPUT_SCHEMA_REGISTRY.get(node_type, {})
    if static_schema:
        schema.update({key: dict(value) for key, value in static_schema.items()})

    if node_type == "gate:map" and isinstance(template, Mapping):
        for key, value in template.items():
            name = str(key).strip()
            if not name:
                continue
            schema[name] = _infer_schema_from_template_value(value)
        return schema

    if node_type == "gate:deserialize":
        literal_schema = _infer_schema_from_deserialized_content(
            content,
            format_name=content_format,
        )
        if literal_schema is not None:
            return literal_schema

    if isinstance(raw_outputs, Mapping):
        schema.update(
            {
                str(key): dict(value)
                for key, value in raw_outputs.items()
                if isinstance(value, Mapping)
            }
        )
    return schema


def validate_output_mapping_keys(payload: Mapping[str, Any], *, context: str) -> None:
    reserved = tuple(sorted(name for name in payload if name in RESERVED_OUTPUT_NAMES))
    if not reserved:
        return
    reserved_list = ", ".join(reserved)
    raise ValueError(f"{context} must not use reserved output name(s): {reserved_list}")


def validate_filter_when_expression(value: Any, *, context: str = "gate:filter when") -> None:
    _validate_filter_when_node(value, path=context)


def _validate_filter_when_node(value: Any, *, path: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a JSON object")

    logical_keys = tuple(key for key in ("all", "any", "not") if key in value)
    operator_keys = tuple(
        key
        for key in (
            "equals",
            "not_equals",
            "in",
            "not_in",
            "greater_than",
            "less_than",
            "is_null",
            "is_not_null",
            "starts_with",
            "ends_with",
            "contains",
            "matches_regex",
        )
        if key in value
    )

    if logical_keys:
        if len(logical_keys) != 1 or len(value) != 1:
            raise ValueError(f"{path} must contain exactly one logical operator")
        logical_key = logical_keys[0]
        nested = value[logical_key]
        if logical_key == "not":
            _validate_filter_when_node(nested, path=f"{path}.not")
            return
        if not isinstance(nested, list) or not nested:
            raise ValueError(f"{path}.{logical_key} must be a non-empty array")
        for index, item in enumerate(nested):
            _validate_filter_when_node(item, path=f"{path}.{logical_key}[{index}]")
        return

    if not operator_keys:
        raise ValueError(
            f"{path} must contain a logical operator or one of: contains, ends_with, equals, greater_than, in, is_not_null, is_null, less_than, matches_regex, not_equals, not_in, starts_with"
        )
    if len(operator_keys) != 1:
        raise ValueError(f"{path} must contain exactly one comparison operator")
    if "left" not in value:
        raise ValueError(f"{path}.left is required")
    if len(value) != 2:
        raise ValueError(f"{path} must contain only left and its comparison operator")

    operator = operator_keys[0]
    operator_value = value[operator]
    if operator in {"is_null", "is_not_null"}:
        if operator_value is not True:
            raise ValueError(f"{path}.{operator} must be true")
        return
    if operator in {"greater_than", "less_than"}:
        if isinstance(operator_value, bool) or not isinstance(operator_value, (int, float)):
            raise ValueError(f"{path}.{operator} must be a number")
        return
    if operator in {"starts_with", "ends_with", "contains", "matches_regex"} and not isinstance(
        operator_value, str
    ):
        raise ValueError(f"{path}.{operator} must be a string")
    if operator == "matches_regex":
        try:
            re.compile(operator_value)
        except re.error as exc:
            raise ValueError(f"{path}.matches_regex must be a valid regex") from exc


def _infer_schema_from_template_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, Mapping):
        return {"type": "object"}
    if isinstance(value, list):
        return {"type": "array"}
    return {"type": "object"}


def _infer_schema_from_deserialized_content(
    value: Any,
    *,
    format_name: Any = None,
) -> dict[str, dict[str, Any]] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or "${{" in text:
        return None
    parser_format = str(format_name or "auto").strip().lower()
    if parser_format not in {"auto", "json", "yaml"}:
        return None
    if parser_format == "json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
    elif parser_format == "yaml":
        parsed = _safe_load_yaml_for_schema_inference(text)
    else:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _safe_load_yaml_for_schema_inference(text)
    if not isinstance(parsed, Mapping):
        return None
    schema: dict[str, dict[str, Any]] = {}
    for key, item in parsed.items():
        if not isinstance(key, str):
            return None
        name = key.strip()
        if not name:
            return None
        schema[name] = _infer_schema_from_template_value(item)
    return schema


def _safe_load_yaml_for_schema_inference(text: str) -> Any:
    try:
        import yaml
    except ImportError:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _ensure_output_schema_bootstrap() -> None:
    global _OUTPUT_SCHEMA_BOOTSTRAP_ATTEMPTED
    if _OUTPUT_SCHEMA_BOOTSTRAP_ATTEMPTED:
        return
    _OUTPUT_SCHEMA_BOOTSTRAP_ATTEMPTED = True
    try:
        from agent_sandbox.schema_bootstrap import bootstrap_default_output_schemas
    except Exception:
        return
    bootstrap_default_output_schemas()


def normalize_directory_paths(value: object) -> object:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return value

    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        normalized.append(DirectoryPath(segments=text).as_posix())
    return normalized


def normalize_string_list(value: object) -> object:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return value

    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized
