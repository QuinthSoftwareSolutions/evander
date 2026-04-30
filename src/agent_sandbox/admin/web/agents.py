from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.shared import AdminWebAuth, json_error


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _bool_query(request: Request, name: str, *, default: bool) -> bool:
    value = request.query_params.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _string_query(request: Request, name: str, *, default: str | None = None) -> str | None:
    value = request.query_params.get(name)
    if value is None:
        return default
    normalized = value.strip()
    return normalized or default


def agent_routes(service: AdminService, _auth: AdminWebAuth) -> list[BaseRoute]:
    async def list_agents(request: Request) -> JSONResponse:
        directory_path = _string_query(request, "directory_path")
        direct_only = _bool_query(request, "direct_only", default=True)
        include_inactive = _bool_query(request, "include_inactive", default=True)
        records = await run_in_threadpool(
            service.list_agents,
            directory_path=directory_path,
            direct_only=direct_only,
            include_inactive=include_inactive,
        )
        return JSONResponse(
            {
                "items": [_jsonable(record) for record in records],
                "directory_path": directory_path or "root",
                "direct_only": direct_only,
            }
        )

    async def get_agent_log(request: Request) -> JSONResponse:
        agent_id = request.path_params["agent_id"]
        backend = _string_query(request, "backend")
        lines_text = _string_query(request, "lines", default="200") or "200"
        try:
            lines = max(1, int(lines_text))
            text = await run_in_threadpool(
                service.get_admin_agent_log_snapshot,
                agent_id,
                backend=backend,
                lines=lines,
                repo_name=_string_query(request, "repo_name"),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(
            {
                "agent_id": agent_id,
                "backend": backend,
                "lines": lines,
                "text": text,
            }
        )

    async def mutate_agent(request: Request) -> JSONResponse:
        agent_id = request.path_params["agent_id"]
        action = request.path_params["action"]
        backend = _string_query(request, "backend")
        try:
            payload = await _json_body(request)
            result = await run_in_threadpool(
                service.run_admin_agent_action,
                agent_id,
                action,
                backend=backend,
                payload=payload,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except LookupError as exc:
            return json_error(str(exc), status_code=404)
        status_code = 200 if bool(result.get("success")) else 409
        return JSONResponse(_jsonable(result), status_code=status_code)

    return [
        Route("/api/admin/agents", list_agents, methods=["GET"]),
        Route("/api/admin/agents/{agent_id:str}/log", get_agent_log, methods=["GET"]),
        Route(
            "/api/admin/agents/{agent_id:str}/actions/{action:str}",
            mutate_agent,
            methods=["POST"],
        ),
    ]


__all__ = ["agent_routes"]
