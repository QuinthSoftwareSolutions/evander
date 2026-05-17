from __future__ import annotations

from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.shared import json_error


def _parse_optional_datetime(value: str | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime.") from exc
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_optional_limit(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("limit must be an integer.") from exc
    if parsed < 1 or parsed > 500:
        raise ValueError("limit must be between 1 and 500.")
    return parsed


def report_routes(service: AdminService) -> list[Route]:
    async def workflow_graph_activity(request: Request) -> JSONResponse:
        try:
            payload = service.read_workflow_graph_activity_report(
                period_start=_parse_optional_datetime(
                    request.query_params.get("start"),
                    field_name="start",
                ),
                period_end=_parse_optional_datetime(
                    request.query_params.get("end"),
                    field_name="end",
                ),
                limit=_parse_optional_limit(request.query_params.get("limit")),
            )
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(payload.model_dump(mode="json"))

    async def conformance(_: Request) -> JSONResponse:
        payload = service.read_conformance_reports()
        return JSONResponse(payload.model_dump(mode="json"))

    async def run_conformance(request: Request) -> JSONResponse:
        kind = request.path_params["kind"]
        try:
            payload = await run_in_threadpool(service.run_conformance_report, kind)
        except ValueError as exc:
            return json_error(str(exc))
        except RuntimeError as exc:
            return json_error(str(exc), status_code=500)
        return JSONResponse(payload.model_dump(mode="json"))

    return [
        Route(
            "/api/admin/reports/workflow-graph-activity",
            workflow_graph_activity,
            methods=["GET"],
        ),
        Route("/api/admin/reports/conformance", conformance, methods=["GET"]),
        Route("/api/admin/reports/conformance/{kind}/run", run_conformance, methods=["POST"]),
    ]


__all__ = ["report_routes"]
