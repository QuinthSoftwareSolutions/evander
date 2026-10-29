"""Narrow MCP adapter. Tool schemas are checked before any external mutation."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import re
from typing import Any

import httpx2
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams
from referencing import Registry

from .auth import MiroOAuth
from .models import MAX_CONTENT_BYTES, MCP_URL, MiroError, UPSTREAM_TOOLS, board_url
from .state import MiroStore, encoded


# Translate semantic inputs using only fields actually advertised by the server.
# Unknown required fields fail closed rather than guessing an argument or destination.
ALIASES = {
    "board_url": ("miro_url", "board_url", "boardUrl", "boardURL", "url"),
    "board_id": ("board_id", "boardId"),
    "svg": ("svg", "svg_content", "svgContent", "svg_document"),
    "title": ("title", "name", "board_name"),
    "text": ("text", "content", "body", "message"),
    "comment_id": ("comment_id", "commentId", "thread_id", "threadId"),
    "resolved": ("resolved", "is_resolved", "resolve"),
    "item_ids": ("widget_ids", "item_ids", "itemIds"),
    "frame_id": ("frame_id", "frameId", "parent_id", "parentId"),
    "format_name": ("format_name", "format", "name", "formatName"),
}


def bind_arguments(schema: dict, values: dict) -> dict:
    properties = schema.get("properties", {})
    arguments = {}
    for key, value in values.items():
        if value is None:
            continue
        field = next((alias for alias in ALIASES.get(key, (key,)) if alias in properties), None)
        if value == [] and field not in schema.get("required", []):
            continue
        if field:
            arguments[field] = value
        elif key not in {"board_id", "board_url", "frame_id"}:
            raise MiroError(
                "unsupported_capability", f"Miro does not advertise the {key} parameter"
            )
    if values.get("board_id") and not any(
        alias in arguments for alias in ALIASES["board_id"] + ALIASES["board_url"]
    ):
        raise MiroError(
            "unsupported_capability", "Miro tool does not accept a scoped board reference"
        )
    if values.get("frame_id") and not any(
        alias in arguments for alias in ALIASES["frame_id"] + ALIASES["board_url"]
    ):
        raise MiroError("unsupported_capability", "Miro tool cannot scope this request to a frame")
    try:
        # Validate inline/local references without allowing a remote schema to
        # cause additional host requests outside the fixed Miro endpoint.
        Draft202012Validator(schema, registry=Registry()).validate(arguments)
    except Exception:
        raise MiroError(
            "unsupported_capability", "Miro tool schema changed; check connection capabilities"
        ) from None
    return arguments


def find_miro_error(error: Exception) -> MiroError | None:
    if isinstance(error, MiroError):
        return error
    for child in getattr(error, "exceptions", ()):
        if found := find_miro_error(child):
            return found
    return None


def result_data(result: Any) -> Any:
    if result.is_error:
        raise MiroError(
            "upstream_error",
            "Miro rejected the operation; inspect the board before retrying a write",
        )
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        data = structured
    else:
        chunks = [part.text for part in result.content if getattr(part, "type", None) == "text"]
        text = "\n".join(chunks)
        try:
            data = json.loads(text)
        except ValueError:
            data = {"text": text}
    if len(encoded(data).encode()) > MAX_CONTENT_BYTES:
        raise MiroError(
            "response_too_large", "Miro response is too large; read a smaller frame or selection"
        )
    return data


class MiroClient:
    def __init__(self, store: MiroStore, record: dict, *, http_transport=None):
        self.store, self.record = store, record
        self.http_transport = http_transport
        self.session: ClientSession | None = None
        self.schemas: dict[str, dict] = {}

    @asynccontextmanager
    async def open(self):
        token = await MiroOAuth(self.store).access_token(self.record)
        try:
            async with asyncio.timeout(120):
                async with httpx2.AsyncClient(
                    transport=self.http_transport,
                    headers={"Authorization": "Bearer " + token},
                    timeout=httpx2.Timeout(45, read=60),
                    follow_redirects=False,
                ) as client:
                    # Allow paired structured/text output and JSON escaping on
                    # the wire; result_data still enforces the 1 MiB data limit.
                    async with streamable_http_client(
                        MCP_URL, http_client=client, max_sse_event_size=MAX_CONTENT_BYTES * 16
                    ) as streams:
                        async with ClientSession(streams[0], streams[1]) as session:
                            self.session = session
                            await session.initialize()
                            cursor = None
                            for _ in range(10):
                                response = await session.list_tools(
                                    params=PaginatedRequestParams(cursor=cursor) if cursor else None
                                )
                                for tool in response.tools:
                                    if tool.name in set(UPSTREAM_TOOLS.values()) | {
                                        "canvas_load_format_skill"
                                    }:
                                        self.schemas[tool.name] = tool.input_schema
                                cursor = response.next_cursor
                                if not cursor:
                                    break
                            else:
                                raise MiroError(
                                    "unsupported_capability",
                                    "Miro tool catalog exceeded pagination limit",
                                )
                            self.store.put(
                                "capabilities",
                                self.record["key"],
                                {
                                    "tools": sorted(self.schemas),
                                    "supported_operations": [
                                        op
                                        for op, tool in UPSTREAM_TOOLS.items()
                                        if tool in self.schemas
                                    ],
                                },
                            )
                            yield self
        except MiroError:
            raise
        except Exception as exc:
            known = find_miro_error(exc)
            if known is not None:
                raise known from None
            raise MiroError(
                "upstream_unavailable", "Miro request did not complete; check connection health"
            ) from None
        finally:
            self.session = None

    def prepare(self, operation: str, **values) -> tuple[str, dict]:
        tool = (
            "canvas_load_format_skill" if operation == "load_format" else UPSTREAM_TOOLS[operation]
        )
        schema = self.schemas.get(tool)
        if schema is None:
            raise MiroError("unsupported_capability", f"Miro does not currently expose {tool}")
        if operation == "load_format" and values.get("format_name") == "diagram":
            properties = schema.get("properties", {})
            field = next((key for key in ALIASES["format_name"] if key in properties), None)
            formats = properties.get(field, {}).get("enum", [])
            if "diagramming" in formats and "diagram" not in formats:
                values["format_name"] = "diagramming"
        if (
            operation == "load_format"
            and values.get("format_name") in {"diagram", "diagramming"}
            and values.get("notation") is None
            and "notation" in schema.get("properties", {})
        ):
            # Request guidance for separately editable shapes/connectors unless a caller
            # explicitly selects a notation. Older schemas can omit notation.
            values["notation"] = "free_form"
        if values.get("board_id"):
            values["board_url"] = board_url(values["board_id"], values.get("frame_id"))
        return tool, bind_arguments(schema, values)

    async def call(self, operation: str, **values) -> Any:
        tool, arguments = self.prepare(operation, **values)
        self.store.assert_current(self.record)
        self.store.consume_call(self.record)
        if self.session is None:
            raise RuntimeError("Miro client is not open")
        return result_data(await self.session.call_tool(tool, arguments))


def unwrap(data: Any) -> Any:
    while isinstance(data, dict) and len(data) == 1 and next(iter(data)) in {"result", "data"}:
        data = next(iter(data.values()))
    return data


def svg_from_result(data: Any) -> str:
    data = unwrap(data)
    candidates = (
        [data]
        if isinstance(data, str)
        else [
            data.get(key)
            for key in ("svg", "result_svg", "svg_content", "svgContent", "text", "content")
        ]
        if isinstance(data, dict)
        else []
    )
    for candidate in candidates:
        if isinstance(candidate, str) and "<svg" in candidate and "</svg>" in candidate:
            return candidate[candidate.index("<svg") : candidate.rindex("</svg>") + 6]
        if isinstance(candidate, str) and (empty := re.search(r"<svg\b[^<>]*/>", candidate)):
            return empty.group(0)
    raise MiroError("unsupported_response", "Miro did not return a readable SVG canvas")


COMMENT_PAGE_SIZE = 50


def comment_page(
    data: Any, *, offset: int = 0, limit: int = COMMENT_PAGE_SIZE
) -> tuple[list[dict], int | None]:
    """Read offset pagination without committing a partial observation.

    A full page without metadata requires another request, including when the
    final page is exactly full. Inconsistent metadata fails the whole read.
    """
    data = unwrap(data)
    if isinstance(data, list):
        items, paging = data, {}
    elif isinstance(data, dict):
        items = data.get("comments", data.get("items", data.get("data")))
        paging = data.get("pagination", {}) or {}
        if not isinstance(paging, dict):
            raise MiroError("incomplete_response", "Miro returned invalid comment pagination")
        paging = {**data, **paging}
        if paging.get("next_cursor") or paging.get("nextCursor"):
            raise MiroError(
                "incomplete_response",
                "Miro returned unsupported cursor pagination for comments",
            )
    else:
        items, paging = None, {}
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not item.get("id") for item in items
    ):
        raise MiroError("unsupported_response", "Miro comment response format is unsupported")
    page_offset, page_limit = paging.get("offset", offset), paging.get("limit", limit)
    total = paging.get("total", paging.get("total_count", paging.get("totalCount")))
    more = paging.get("has_more", paging.get("hasMore"))
    if (
        type(page_offset) is not int
        or page_offset != offset
        or type(page_limit) is not int
        or not 1 <= page_limit <= limit
        or len(items) > page_limit
        or (total is not None and (type(total) is not int or total < offset + len(items)))
        or (more is not None and type(more) is not bool)
    ):
        raise MiroError("incomplete_response", "Miro returned inconsistent comment pagination")
    next_offset = offset + len(items)
    if total is not None:
        expected_more = next_offset < total
        if more is not None and more != expected_more:
            raise MiroError("incomplete_response", "Miro returned inconsistent comment totals")
        more = expected_more
    elif more is None:
        more = len(items) == page_limit
    if more and not items:
        raise MiroError("incomplete_response", "Miro comment pagination made no progress")
    return items, next_offset if more else None
