"""Provider values."""

from __future__ import annotations

import json
from collections.abc import (
    Mapping,
)
from datetime import (
    UTC,
    datetime,
)
from pathlib import (
    Path,
)
from typing import (
    Any,
)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2)


def _definition_payload(definition: Any, *field_names: str) -> dict[str, Any]:
    return {
        field_name: getattr(definition, field_name)
        for field_name in field_names
        if definition is not None and hasattr(definition, field_name)
    }


def _configured_definition_fields(definition: Any) -> set[str]:
    fields_set = getattr(definition, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(definition, "__fields_set__", None)
    if isinstance(fields_set, set):
        return {str(field) for field in fields_set}
    return set()


def _payload_field_configured(
    field_name: str,
    *,
    definition_fields: set[str],
    resolved_fields: set[str],
) -> bool:
    return field_name in definition_fields or field_name in resolved_fields


def _team_datetime_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _runtime_dir(context: Any) -> Path:
    resolved = getattr(context, "resolved_config", None)
    runtime_dir = getattr(resolved, "runtime_dir", None)
    if runtime_dir is None:
        raise RuntimeError("team add-on provider requires resolved_config.runtime_dir")
    return Path(runtime_dir)


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object, *, default: int | None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("integer value is required")
    return int(value)


def _optional_float(value: object, *, default: float | None) -> float | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("number value is required")
    return float(value)


def _number_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _number_or_zero(value: object) -> float:
    return _number_or_none(value) or 0.0


def _optional_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value is required")


def _optional_mapping(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _list_of_mappings(value: object, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must contain JSON objects")
        records.append(dict(item))
    return records


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _consolidation_failure(
    target_branch: str | None,
    error: str,
    *,
    error_code: str | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "target_branch": target_branch,
        "merged_branches": [],
        "commit_sha": None,
        "conflict_branch": None,
        "output": None,
        "error": error,
        "error_code": error_code,
    }


def _review_publish_failure(
    target_branch: str | None,
    managed_repo_id: str | None,
    error: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "review_branch": target_branch,
        "target_branch": target_branch,
        "commit_sha": None,
        "managed_repo_id": managed_repo_id,
        "message": None,
        "error": error,
    }
