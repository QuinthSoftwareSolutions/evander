"""Official hosted MCP transport plus separately scoped Figma REST requests."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
import httpx2
import jsonschema
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation, PaginatedRequestParams

from .auth import MCP_URL, OAuth
from .models import FigmaError, FigmaRejected, FigmaUnavailable

MAX_BYTES = 8 * 1024 * 1024
MAX_DESIGN_BYTES = 64 * 1024 * 1024


class FigmaClient:
    def __init__(self, oauth: OAuth, directory, connection_id, config, *, transport=None):
        self.oauth, self.directory, self.connection_id, self.config = (
            oauth,
            directory,
            connection_id,
            config,
        )
        self.transport = transport

    def _mcp(self, name, arguments):
        token = self.oauth.token(self.directory, self.connection_id, self.config)

        async def invoke():
            async with (
                httpx2.AsyncClient(
                    headers={"Authorization": "Bearer " + token},
                    timeout=httpx2.Timeout(30, read=90),
                    follow_redirects=False,
                ) as http_client,
                # Base64 screenshots within the decoded image limit exceed the
                # SDK's default 1 MiB SSE cap; keep our bounded result allowance.
                streamable_http_client(
                    MCP_URL, http_client=http_client, max_sse_event_size=MAX_BYTES * 2
                ) as (read, write),
            ):
                async with ClientSession(
                    read, write, client_info=Implementation(name="evander", version="1")
                ) as session:
                    await session.initialize()
                    cursor, tool = None, None
                    for _ in range(16):
                        listing = await session.list_tools(
                            params=PaginatedRequestParams(cursor=cursor) if cursor else None
                        )
                        tool = next((tool for tool in listing.tools if tool.name == name), None)
                        if tool or not listing.next_cursor:
                            break
                        cursor = listing.next_cursor
                    if not tool:
                        raise FigmaUnavailable(
                            "The Figma connection does not expose the required tool"
                        )
                    jsonschema.validate(arguments, tool.input_schema)
                    return await session.call_tool(name, arguments)

        try:
            result = asyncio.run(asyncio.wait_for(invoke(), timeout=120))
            if result.is_error:
                raise FigmaError("Figma rejected the operation; inspect before retrying any write")
            if len(result.model_dump_json()) > MAX_BYTES * 2:
                raise FigmaError("Figma result exceeds the response limit")
            return result
        except FigmaError:
            raise
        except Exception:
            # SDK exception groups can contain HTTP headers or upstream response bodies.
            raise FigmaUnavailable(
                "Figma MCP request failed; check authorization, access, and limits"
            ) from None

    def script(self, file_key, payload):
        script = Path(__file__).with_name("canvas.js").read_text()
        code = (
            "const input = "
            + json.dumps(payload, ensure_ascii=True, allow_nan=False)
            + ";\n"
            + script
        )
        if len(code.encode()) > 512 * 1024:
            raise FigmaRejected("Design operation is too large; work on a smaller frame")
        result = self._mcp(
            "use_figma",
            {
                "fileKey": file_key,
                "code": code,
                "description": "Evander bounded design " + payload["action"],
                "skillNames": "figma-use",
            },
        )
        value = result.structured_content
        if value is None:
            for part in result.content:
                if part.type == "text":
                    try:
                        value = json.loads(part.text)
                        break
                    except ValueError:
                        continue
        # Require our own return envelope. Never treat arbitrary success text as a completed edit.
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            value = value["result"]
        if not isinstance(value, dict) or value.get("evander") != 1:
            raise FigmaError("Figma returned an unrecognized result; inspect before retrying")
        if value.get("rejected") == "node_limit":
            raise FigmaRejected(
                "Edit would exceed the 1000-node inspection limit; select a smaller area "
                "or reduce the batch"
            )
        if value.get("error"):
            raise FigmaError(
                "Figma operation could not be completed; inspect its recorded operation"
            )
        return value

    def inspect(self, file_key, node_id):
        chunks, offset, token = [], 0, None
        while True:
            page = self.script(
                file_key, {"action": "inspect", "node_id": node_id, "offset": offset}
            )
            if token is not None and token != page["read_token"]:
                raise FigmaError("Design changed during the read; inspect again")
            token = page["read_token"]
            chunk = page.get("nodes_json")
            if not isinstance(chunk, str) or not chunk or not chunk.isascii() or len(chunk) > 8000:
                raise FigmaError("Invalid Figma design read chunk")
            chunks.append(chunk)
            offset += len(chunk)
            if offset > MAX_DESIGN_BYTES:
                raise FigmaError("Design exceeds the read size limit; select a smaller frame")
            if page.get("next_offset") is None:
                try:
                    nodes = json.loads("".join(chunks))
                except ValueError:
                    raise FigmaError("Invalid Figma design read result") from None
                if not isinstance(nodes, list) or not nodes or len(nodes) > 1000:
                    raise FigmaError("Design must contain between 1 and 1000 nodes")
                return nodes
            if type(page["next_offset"]) is not int or page["next_offset"] != offset:
                raise FigmaError("Invalid Figma read cursor")

    def screenshot(self, file_key, node_id):
        result = self._mcp("get_screenshot", {"fileKey": file_key, "nodeId": node_id})
        for part in result.content:
            if part.type == "image" and part.mime_type == "image/png":
                data = base64.b64decode(part.data, validate=True)
                if len(data) <= MAX_BYTES and data.startswith(b"\x89PNG\r\n\x1a\n"):
                    return data
        raise FigmaError("Figma did not return a bounded PNG preview")

    def variables(self, file_key, node_id):
        result = self._mcp("get_variable_defs", {"fileKey": file_key, "nodeId": node_id})
        return {
            "variables_and_styles": result.structured_content
            or [part.text for part in result.content if part.type == "text"]
        }

    def rest(self, method, path, *, body=None):
        token = os.environ.get(self.config.rest_token_env or "")
        if not token:
            raise FigmaUnavailable("Configure the separate Figma REST credential on the host")
        headers = (
            {"X-Figma-Token": token}
            if self.config.rest_token_kind == "personal"
            else {"Authorization": "Bearer " + token}
        )
        try:
            with httpx.Client(
                transport=self.transport, timeout=30, follow_redirects=False
            ) as client:
                with client.stream(
                    method, "https://api.figma.com/v1/" + path, headers=headers, json=body
                ) as response:
                    content = bytearray()
                    for part in response.iter_bytes():
                        content.extend(part)
                        if len(content) > MAX_BYTES:
                            raise FigmaError("Figma REST response exceeds the size limit")
                    if response.status_code == 429:
                        raise FigmaUnavailable(
                            "Figma rate limit reached; retry after the polling interval"
                        )
                    response.raise_for_status()
                    return json.loads(content)
        except (httpx.HTTPError, ValueError):
            raise FigmaUnavailable(
                "Figma REST request failed; check access and authorization"
            ) from None

    def user_id(self):
        user = self.rest("GET", "me")
        user_id = user.get("id") if isinstance(user, dict) else None
        if not isinstance(user_id, str) or not user_id:
            raise FigmaUnavailable("Figma did not identify the REST credential's account")
        return user_id

    def comments(self, file_key):
        return self.rest("GET", "files/" + quote(file_key, safe="") + "/comments").get(
            "comments", []
        )

    def comment(self, file_key, message, node_id=None, comment_id=None):
        body = {"message": message}
        if comment_id:
            body["comment_id"] = comment_id
        elif node_id:
            body["client_meta"] = {"node_id": node_id, "node_offset": {"x": 0, "y": 0}}
        return self.rest("POST", "files/" + quote(file_key, safe="") + "/comments", body=body)

    def versions(self, file_key):
        path = "files/" + quote(file_key, safe="") + "/versions"
        expected_path, results = "/v1/" + path, []
        for _ in range(10):
            page = self.rest("GET", path)
            results.extend(page.get("versions", []))
            next_page = page.get("pagination", {}).get("next_page")
            if not next_page:
                return results
            url = urlsplit(next_page)
            if (
                url.scheme != "https"
                or url.netloc != "api.figma.com"
                or url.path != expected_path
                or url.fragment
            ):
                raise FigmaError("Figma returned an invalid version pagination link")
            path = url.path.removeprefix("/v1/") + "?" + url.query
        raise FigmaUnavailable("Version history exceeds the bounded polling limit")
