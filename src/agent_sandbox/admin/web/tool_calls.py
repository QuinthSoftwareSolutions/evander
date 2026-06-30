from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_sandbox.admin.composition import AdminRuntimeComponents
from agent_sandbox.admin.web.cache import DASHBOARD_CACHE_PARAM, AdminWebJsonResponseCache
from agent_sandbox.admin.web.shared import json_error


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return int(normalized)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _dashboard_cache_requested(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def tool_call_routes(
    service: AdminRuntimeComponents,
    cache: AdminWebJsonResponseCache | None = None,
) -> list[Route]:
    async def list_tool_calls(request: Request) -> JSONResponse:
        params = request.query_params
        try:
            entries = await run_in_threadpool(
                service.approval_audit_admin.list_tool_call_audit_entries,
                session_id=_optional_text(params.get("session_id")),
                tool_name=_optional_text(params.get("tool_name")),
                status=_optional_text(params.get("status")),
                directory_path=_optional_text(params.get("directory_path")),
                direct_only=_parse_bool(params.get("direct_only")),
                created_after=_parse_datetime(params.get("created_after")),
                created_before=_parse_datetime(params.get("created_before")),
                q=_optional_text(params.get("q")),
                limit=_parse_int(params.get("limit")),
                recent_only=_parse_bool(
                    params.get("recent_only"),
                    default=_dashboard_cache_requested(params.get(DASHBOARD_CACHE_PARAM)),
                ),
            )
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(entries.model_dump(mode="json"))

    return [
        Route(
            "/api/admin/tool-calls",
            cache.wrap("admin.tool_calls.list", list_tool_calls) if cache else list_tool_calls,
            methods=["GET"],
        )
    ]
