from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs


def _request_from_context(ctx: Any | None) -> Any | None:
    request_context = None
    if ctx is not None:
        try:
            request_context = ctx.request_context
        except ValueError:
            request_context = getattr(ctx, "_request_context", None)
        except AttributeError:
            request_context = getattr(ctx, "request_context", None)
    return getattr(request_context, "request", None) if request_context is not None else None


def _string_from_headers(headers: Any, *names: str) -> str | None:
    if headers is None:
        return None
    for name in names:
        try:
            candidate = headers.get(name)
        except AttributeError:
            candidate = None
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def _string_from_query_params(query_params: Any, *names: str) -> str | None:
    if query_params is None:
        return None
    for name in names:
        try:
            candidate = query_params.get(name)
        except AttributeError:
            candidate = None
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def _string_from_raw_query(query: str | None, *names: str) -> str | None:
    if not query:
        return None
    parsed = parse_qs(query, keep_blank_values=True)
    for name in names:
        candidates = parsed.get(name)
        if not candidates:
            continue
        text = str(candidates[0] or "").strip()
        if text:
            return text
    return None


def _first_string_in_payload(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            text = str(candidate or "").strip()
            if text:
                return text
        for nested in value.values():
            found = _first_string_in_payload(nested, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _first_string_in_payload(item, keys)
            if found:
                return found
    return None
