"""Resolve gate persistence paths from provider-owned state."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def provider_state_path(provider_state: Any, *, namespace: tuple[str, ...], default: Path) -> Path:
    cursor: Any = provider_state if isinstance(provider_state, dict) else {}
    for key in namespace:
        if not isinstance(cursor, dict):
            return default
        cursor = cursor.get(key)
    if cursor is None:
        return default
    text = str(cursor).strip()
    return Path(text) if text else default
