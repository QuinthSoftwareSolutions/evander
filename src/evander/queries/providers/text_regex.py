from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .base import QueryProviderBase


class TextRegexQueryProvider(QueryProviderBase):
    """Match or transform text using an operator-configured regular expression."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self.query_definition.model_dump()
        payload.update(resolved_config or {})
        value, pattern = payload["text"], payload["pattern"]
        if not isinstance(value, str) or not isinstance(pattern, str):
            raise ValueError("text and pattern must be strings")
        if len(value) > 32768 or len(pattern) > 4096:
            raise ValueError("Text or pattern exceeds the regex query size limit")
        regex = re.compile(pattern)
        operation = payload.get("operation", "fullmatch")
        if operation == "replace":
            replacement = payload.get("replacement", "")
            if not isinstance(replacement, str):
                raise ValueError("replacement must be a string")
            result, count = regex.subn(replacement, value)
            return {"value": result, "matched": count > 0, "groups": {}}
        if operation != "fullmatch":
            raise ValueError("operation must be fullmatch or replace")
        require_match = payload.get("require_match", False)
        if not isinstance(require_match, bool):
            raise ValueError("require_match must be a boolean")
        match = regex.fullmatch(value)
        if not match and require_match:
            raise ValueError("Text does not match the configured pattern")
        return {
            "value": value,
            "matched": match is not None,
            "groups": match.groupdict() if match else {},
        }
