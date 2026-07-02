from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request

from evander.gateway.request_context import (
    _request_from_context,
    _string_from_headers,
    _string_from_query_params,
    _string_from_raw_query,
)
from evander.policy import PolicyError
from evander.session_auth import (
    MCP_SESSION_TOKEN_HEADER_NAMES,
    MCP_SESSION_TOKEN_QUERY_PARAM_NAMES,
)

_SESSION_ID_CONTEXT_CONFLICT_MESSAGE = (
    "session_id is bound by the MCP connection; omit it or use the connected session"
)
_SESSION_ID_PARAMETER_DESCRIPTION = (
    "Optional legacy caller session id. Agents should omit this field; the MCP gateway supplies "
    "the connected session id and rejects conflicting values."
)


def _ambient_session_id_from_request(
    request: Any | None,
    transport_session_map: dict[str, str] | None = None,
) -> str | None:
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    query_params = getattr(request, "query_params", None)
    raw_query = str(getattr(getattr(request, "url", None), "query", "") or "").strip()
    transport_session_id = _string_from_headers(headers, "mcp-session-id")
    declared_session_id: str | None = None
    if query_params is not None:
        candidate = str(query_params.get("session_id") or "").strip()
        if candidate:
            declared_session_id = candidate
    if declared_session_id is None:
        declared_session_id = _string_from_raw_query(raw_query, "session_id")
    if declared_session_id is None:
        declared_session_id = _string_from_headers(headers, "x-evander-session-id")
    if transport_session_id and transport_session_map is not None:
        bound_session_id = str(transport_session_map.get(transport_session_id) or "").strip()
        if bound_session_id:
            if declared_session_id and declared_session_id != bound_session_id:
                raise ToolError(_SESSION_ID_CONTEXT_CONFLICT_MESSAGE)
            return bound_session_id
        if declared_session_id:
            transport_session_map[transport_session_id] = declared_session_id
            return declared_session_id
    if declared_session_id:
        return declared_session_id
    return None


def _ambient_session_id_from_context(
    ctx: Any | None,
    transport_session_map: dict[str, str] | None = None,
) -> str | None:
    return _ambient_session_id_from_request(
        _request_from_context(ctx),
        transport_session_map=transport_session_map,
    )


def _mcp_session_token_from_request(request: Any | None) -> str | None:
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    query_params = getattr(request, "query_params", None)
    raw_query = str(getattr(getattr(request, "url", None), "query", "") or "").strip()
    token = _string_from_query_params(query_params, *MCP_SESSION_TOKEN_QUERY_PARAM_NAMES)
    if token is None:
        token = _string_from_raw_query(raw_query, *MCP_SESSION_TOKEN_QUERY_PARAM_NAMES)
    if token is None:
        token = _string_from_headers(headers, *MCP_SESSION_TOKEN_HEADER_NAMES)
    return token


def _mcp_session_token_from_context(ctx: Any | None) -> str | None:
    return _mcp_session_token_from_request(_request_from_context(ctx))


def _resolve_bound_session_id(
    argument_session_id: str | None,
    ambient_session_id: str | None,
) -> str:
    explicit = str(argument_session_id or "").strip()
    ambient = str(ambient_session_id or "").strip()
    if ambient and explicit and explicit != ambient:
        raise ToolError(_SESSION_ID_CONTEXT_CONFLICT_MESSAGE)
    if ambient:
        return ambient
    if explicit:
        return explicit
    raise ToolError("session_id is required")


def _resolve_session_id_from_context(
    session_id: str | None,
    ctx: Context | None,
    *,
    transport_session_map: dict[str, str] | None = None,
) -> str:
    return _resolve_bound_session_id(
        session_id,
        _ambient_session_id_from_context(ctx, transport_session_map=transport_session_map),
    )


def _resolve_session_id_from_request(request: Request) -> str:
    candidate = str(request.headers.get("x-evander-session-id") or "").strip()
    if candidate:
        return candidate
    candidate = str(request.query_params.get("session_id") or "").strip()
    if candidate:
        return candidate
    raise PolicyError("session_id is required")
