from __future__ import annotations

from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool

from agent_sandbox.admin.composition import AdminRuntimeComponents
from agent_sandbox.admin.web.cache import AdminWebJsonResponseCache
from agent_sandbox.admin.web.rbac import WORKFLOW_RUNS_READ_PERMISSION
from agent_sandbox.admin.web.shared import AdminWebAuth
from agent_sandbox.admin.web.shared import json_error
from agent_sandbox.admin.web.workflow import _require_workflow_run_read_access


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


def _parse_optional_recent_seconds(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("recent_seconds must be an integer.") from exc
    if parsed < 1:
        raise ValueError("recent_seconds must be greater than 0.")
    return parsed


def _filter_workflow_failure_clusters_report_payload(
    payload: object,
    *,
    readable_node_ids: set[str],
    can_read_root_workflow_runs: bool = False,
) -> dict[str, object]:
    payload_dict = payload.model_dump(mode="json")
    clusters = payload_dict.get("clusters")
    if isinstance(clusters, list):
        payload_dict["clusters"] = [
            cluster
            for cluster in clusters
            if isinstance(cluster, dict)
            and (
                str(cluster.get("node_id") or "").strip() in readable_node_ids
                or (
                    can_read_root_workflow_runs
                    and str(cluster.get("graph_id") or "").strip() == "root"
                    and str(cluster.get("directory_path") or "").strip() == "root"
                )
            )
        ]
    return payload_dict


def report_routes(
    service: AdminRuntimeComponents,
    auth: AdminWebAuth,
    cache: AdminWebJsonResponseCache | None = None,
) -> list[Route]:
    async def workflow_graph_activity(request: Request) -> JSONResponse:
        try:
            payload = await run_in_threadpool(
                service.workflow_reports_admin.read_workflow_graph_activity_report,
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

    async def workflow_failure_clusters(request: Request) -> JSONResponse:
        try:
            period_start = _parse_optional_datetime(
                request.query_params.get("start"),
                field_name="start",
            )
            period_end = _parse_optional_datetime(
                request.query_params.get("end"),
                field_name="end",
            )
            limit = _parse_optional_limit(request.query_params.get("limit"))
            recent_seconds = _parse_optional_recent_seconds(
                request.query_params.get("recent_seconds")
            )

            document = service.workflow_config_admin.read_composed_workflow_editor_document()
            readable_node_ids: set[str] | None = None
            if not document.graphs:
                auth.require_permission(
                    request, WORKFLOW_RUNS_READ_PERMISSION, directory_path="root"
                )
            else:
                readable_graphs, readable_node_ids = _require_workflow_run_read_access(
                    auth=auth,
                    request=request,
                    graphs=document.graphs,
                )
                if not readable_graphs:
                    raise PermissionError("Permission required: workflow_runs.read")

            payload = await run_in_threadpool(
                service.workflow_reports_admin.read_workflow_failure_clusters_report,
                period_start=period_start,
                period_end=period_end,
                limit=limit,
                recent_threshold_seconds=recent_seconds,
            )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        if readable_node_ids is not None:
            return JSONResponse(
                _filter_workflow_failure_clusters_report_payload(
                    payload,
                    readable_node_ids=readable_node_ids,
                    can_read_root_workflow_runs=auth.can(
                        request, WORKFLOW_RUNS_READ_PERMISSION, directory_path="root"
                    ),
                )
            )
        return JSONResponse(payload.model_dump(mode="json"))

    async def conformance(_: Request) -> JSONResponse:
        payload = service.workflow_reports_admin.read_conformance_reports()
        return JSONResponse(payload.model_dump(mode="json"))

    async def run_conformance(request: Request) -> JSONResponse:
        kind = request.path_params["kind"]
        try:
            payload = await run_in_threadpool(service.workflow_reports_admin.run_conformance_report, kind)
        except ValueError as exc:
            return json_error(str(exc))
        except RuntimeError as exc:
            return json_error(str(exc), status_code=500)
        return JSONResponse(payload.model_dump(mode="json"))

    return [
        Route(
            "/api/admin/reports/workflow-graph-activity",
            cache.wrap("admin.reports.workflow_graph_activity", workflow_graph_activity)
            if cache
            else workflow_graph_activity,
            methods=["GET"],
        ),
        Route(
            "/api/admin/reports/workflow-failure-clusters",
            cache.wrap("admin.reports.workflow_failure_clusters", workflow_failure_clusters)
            if cache
            else workflow_failure_clusters,
            methods=["GET"],
        ),
        Route("/api/admin/reports/conformance", conformance, methods=["GET"]),
        Route("/api/admin/reports/conformance/{kind}/run", run_conformance, methods=["POST"]),
    ]


__all__ = ["report_routes"]
