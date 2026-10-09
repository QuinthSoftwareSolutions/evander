"""Shared JSON revision and overlay semantics for directory admin services."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


def _revision(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _deep_merge(current: Any, overlay: Any) -> Any:
    if isinstance(current, dict) and isinstance(overlay, dict):
        merged = dict(current)
        for key, value in overlay.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return overlay
