from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from evander.approval_diagnostics import DEFAULT_SENSITIVE_PAYLOAD_MARKERS

_REDACTED = "[REDACTED]"
# Context must retain behavior fields (prompt/body/parameters), unlike audit storage.
_SENSITIVE_MARKERS = tuple(
    marker for marker in DEFAULT_SENSITIVE_PAYLOAD_MARKERS if marker != "pat"
) + ("api_key", "apikey", "passwd", "credential", "connection_string", "connectionstring")
_SENSITIVE_NAMES = frozenset({"headers", "env", "environment"})
_AUTHORIZATION_VALUE = re.compile(r"\b(Bearer|Basic)\s+\S+", re.IGNORECASE)


def sanitize_chat_context(value: Any, *, key: str = "") -> Any:
    """Mask credentials while preserving the workflow behavior being explained."""
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    normalized = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", normalized)
    normalized = normalized.casefold().replace("-", "_")
    if (
        normalized in _SENSITIVE_NAMES
        or any(marker in normalized for marker in _SENSITIVE_MARKERS)
        or set(normalized.split("_")) & {"pat", "pwd"}
    ):
        return _REDACTED
    if isinstance(value, dict):
        return {
            str(name): sanitize_chat_context(item, key=str(name))
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_chat_context(item) for item in value]
    if isinstance(value, str):
        return _sanitize_context_text(value)
    return value


def _sanitize_context_text(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc:
            authority = parsed.netloc.rsplit("@", 1)
            netloc = f"{_REDACTED}@{authority[-1]}" if len(authority) == 2 else parsed.netloc
            query = urlencode(
                [(name, _REDACTED) for name, _ in parse_qsl(parsed.query, keep_blank_values=True)]
            )
            fragment = _REDACTED if parsed.fragment else ""
            if netloc != parsed.netloc or parsed.query or parsed.fragment:
                value = urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))
    except ValueError:
        pass
    return _AUTHORIZATION_VALUE.sub(lambda match: f"{match.group(1)} {_REDACTED}", value)
