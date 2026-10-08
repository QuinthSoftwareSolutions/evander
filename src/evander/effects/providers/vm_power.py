from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def resolve_power_target(
    payload: Mapping[str, Any],
    *,
    effect_type: str,
    normalize_optional_string: Callable[[Any], str | None],
) -> str:
    for field_name in ("target", "vm_name", "session_id"):
        value = normalize_optional_string(payload.get(field_name))
        if value:
            return value
    raise ValueError(f"{effect_type} requires target, vm_name, or session_id")


def power_action_result(result: Any, *, target: str, action: str) -> dict[str, Any]:
    return {
        "success": bool(result.success),
        "action": action,
        "target": target,
        "vm_name": result.vm_name or target,
        "message": result.message or "",
    }
