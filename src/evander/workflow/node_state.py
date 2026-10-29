from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def workflow_node_is_enabled(node: Any) -> bool:
    """Return False only for an explicit boolean enabled=false flag."""
    value = _node_value(node, "enabled")
    if value is None:
        config = _node_value(node, "config")
        if isinstance(config, Mapping):
            value = config.get("enabled")
    if isinstance(value, bool):
        return value
    return True


def _node_value(node: Any, key: str) -> Any:
    if isinstance(node, Mapping):
        return node.get(key)
    try:
        return getattr(node, key)
    except AttributeError:
        return None
