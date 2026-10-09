from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def coerce_git_result(result: Any) -> dict[str, Any]:
    """Copy a Git runtime result while preserving supported result shapes."""
    if result is None:
        return {}
    if isinstance(result, Mapping):
        return dict(result)
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if hasattr(result, "__dict__"):
        return dict(vars(result))
    return {"result": result}
