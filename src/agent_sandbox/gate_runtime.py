from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any


class GateRuntimeError(ValueError):
    """Raised when gate runtime evaluation cannot produce a downstream payload."""


def filter_payload_matches(match_value: Any, payload: Any) -> bool:
    """Return True when `payload` satisfies the configured filter `match_value`.

    Objects use recursive subset semantics. Arrays and scalars use exact equality.
    """

    if isinstance(match_value, Mapping):
        if not isinstance(payload, Mapping):
            return False
        for key, expected in match_value.items():
            if key not in payload or not filter_payload_matches(expected, payload[key]):
                return False
        return True
    if isinstance(match_value, list):
        return isinstance(payload, list) and payload == match_value
    return payload == match_value


def filter_when_matches(predicate: Any) -> bool:
    if not isinstance(predicate, Mapping):
        raise GateRuntimeError("gate:filter when must resolve to a JSON object")

    if "all" in predicate:
        values = predicate["all"]
        if not isinstance(values, list) or not values:
            raise GateRuntimeError("gate:filter when.all must resolve to a non-empty array")
        return all(filter_when_matches(item) for item in values)

    if "any" in predicate:
        values = predicate["any"]
        if not isinstance(values, list) or not values:
            raise GateRuntimeError("gate:filter when.any must resolve to a non-empty array")
        return any(filter_when_matches(item) for item in values)

    if "not" in predicate:
        return not filter_when_matches(predicate["not"])

    if "left" not in predicate:
        raise GateRuntimeError("gate:filter when predicate must define left")
    left = predicate["left"]
    if "equals" in predicate:
        return left == predicate["equals"]
    if "not_equals" in predicate:
        return left != predicate["not_equals"]
    if "is_null" in predicate:
        if predicate["is_null"] is not True:
            raise GateRuntimeError("gate:filter when.is_null must resolve to true")
        return left is None
    if "is_not_null" in predicate:
        if predicate["is_not_null"] is not True:
            raise GateRuntimeError("gate:filter when.is_not_null must resolve to true")
        return left is not None
    if "in" in predicate:
        return left in _require_membership_container(predicate["in"], operator_name="in")
    if "not_in" in predicate:
        return left not in _require_membership_container(predicate["not_in"], operator_name="not_in")
    if "starts_with" in predicate:
        return _require_string_operand(left, operator_name="starts_with").startswith(
            _require_string_operand(predicate["starts_with"], operator_name="starts_with")
        )
    if "ends_with" in predicate:
        return _require_string_operand(left, operator_name="ends_with").endswith(
            _require_string_operand(predicate["ends_with"], operator_name="ends_with")
        )
    if "contains" in predicate:
        return _require_string_operand(predicate["contains"], operator_name="contains") in _require_string_operand(
            left, operator_name="contains"
        )
    raise GateRuntimeError("gate:filter when predicate must define a supported comparison operator")


def require_object_payload(value: Any, *, gate_type: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GateRuntimeError(f"{gate_type} must resolve to an object payload")
    return dict(value)


def _require_membership_container(value: Any, *, operator_name: str) -> list[Any] | tuple[Any, ...] | set[Any]:
    if isinstance(value, (list, tuple, set)):
        return value
    raise GateRuntimeError(f"gate:filter when.{operator_name} must resolve to an array")


def _require_string_operand(value: Any, *, operator_name: str) -> str:
    if isinstance(value, str):
        return value
    raise GateRuntimeError(f"gate:filter when.{operator_name} requires string operands")


def merged_payload_from_sources(
    *,
    latest_inputs_by_source: Mapping[str, Mapping[str, Any]],
    required_source_ids: Sequence[str],
    shared_output_keys: Sequence[str],
    source_updated_at: Mapping[str, datetime | str | None] | None = None,
) -> dict[str, Any]:
    """Project merge output to the validated shared-key set.

    When multiple sources provide a shared key, the most recently updated source wins.
    """

    if not required_source_ids:
        return {}

    order_by_source = {source_id: index for index, source_id in enumerate(required_source_ids)}
    merged: dict[str, Any] = {}
    for output_key in shared_output_keys:
        candidates: list[tuple[tuple[int, str], int, Any]] = []
        for source_id in required_source_ids:
            payload = latest_inputs_by_source.get(source_id)
            if payload is None:
                raise GateRuntimeError(f"merge input for source {source_id!r} is not available")
            if output_key not in payload:
                raise GateRuntimeError(
                    f"merge input from source {source_id!r} is missing shared output {output_key!r}"
                )
            updated_at = source_updated_at.get(source_id) if source_updated_at is not None else None
            candidates.append((_updated_at_sort_key(updated_at), order_by_source[source_id], payload[output_key]))
        _, _, value = max(candidates, key=lambda item: (item[0], item[1]))
        merged[output_key] = value
    return merged


def _updated_at_sort_key(value: datetime | str | None) -> tuple[int, str]:
    if value is None:
        return (0, "")
    if isinstance(value, datetime):
        return (1, value.astimezone(UTC).isoformat())
    text = str(value).strip()
    if not text:
        return (0, "")
    try:
        normalized = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        normalized = text
    return (1, normalized)
