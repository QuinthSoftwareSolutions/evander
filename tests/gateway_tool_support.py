"""Shared test adapter; every call retains its caller's fresh runtime/transport."""

from __future__ import annotations

import asyncio


def call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    structured = result.structured_content
    if structured is None:
        return result.content
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured
