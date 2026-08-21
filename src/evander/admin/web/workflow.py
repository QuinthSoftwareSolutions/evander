from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from collections.abc import Iterable
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.payloads import (
    AdminRevisionConflict,
    WorkflowNodeExecutionFieldsRequestPayload,
    WorkflowNodeExecutionSearchRequestPayload,
)
from evander.admin.services.workflow_pack_history_service import WorkflowPackChangeActor
from evander.admin.web.cache import AdminWebJsonResponseCache
from evander.admin.web.rbac import (
    WORKFLOW_PACKS_MANAGE_PERMISSION,
    WORKFLOW_RUNS_CONTROL_PERMISSION,
    WORKFLOW_RUNS_READ_PERMISSION,
    WORKFLOWS_READ_PERMISSION,
    WORKFLOWS_WRITE_PERMISSION,
)
from evander.admin.web.shared import AdminWebAuth, request_has_privileged_operations
from evander.admin.web.shared import json_error


async def json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def expected_revision_from_payload(payload: dict[str, Any]) -> str:
    if "expected_revision" not in payload:
        raise KeyError("expected_revision is required")
    expected_revision = payload.get("expected_revision")
    if not isinstance(expected_revision, str) or not expected_revision.strip():
        raise KeyError("expected_revision must be a non-empty string")
    return expected_revision.strip()


def optional_bool_from_query(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Boolean query parameters must be true or false.")


def optional_limit_from_query(value: str | None, *, default: int = 25) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("limit must be an integer.") from exc
    if parsed < 1 or parsed > 100:
        raise ValueError("limit must be between 1 and 100.")
    return parsed


def workflow_pack_actor_from_session(session: dict[str, Any]) -> WorkflowPackChangeActor:
    return WorkflowPackChangeActor.from_values(
        login=session.get("login"),
        display_name=session.get("name"),
    )


def _graph_directory_paths(graphs: object) -> tuple[str, ...]:
    if not isinstance(graphs, list):
        return ()
    directory_paths: list[str] = []
    seen_directory_paths: set[str] = set()
    for graph in graphs:
        if not isinstance(graph, dict):
            continue
        directory_path = str(graph.get("directory_path") or "").strip()
        if not directory_path or directory_path in seen_directory_paths:
            continue
        seen_directory_paths.add(directory_path)
        directory_paths.append(directory_path)
    return tuple(directory_paths)


def _readable_graphs_for_document(
    *,
    auth: AdminWebAuth,
    request: Request,
    graphs: list[object],
    permission: str = WORKFLOWS_READ_PERMISSION,
) -> list[dict[str, Any]]:
    readable_graphs: list[dict[str, Any]] = []
    for graph in graphs:
        if isinstance(graph, dict):
            graph_payload = graph
        elif hasattr(graph, "model_dump"):
            graph_payload = graph.model_dump(mode="json")
        else:
            continue
        directory_path = str(graph_payload.get("directory_path") or "").strip() or "root"
        if auth.can(request, permission, directory_path=directory_path):
            readable_graphs.append(graph_payload)
    return readable_graphs


def _graph_directory_paths_for_document_graphs(graphs: list[object]) -> tuple[str, ...]:
    directory_paths: list[str] = []
    seen_directory_paths: set[str] = set()
    for graph in graphs:
        if isinstance(graph, dict):
            graph_payload = graph
        elif hasattr(graph, "model_dump"):
            graph_payload = graph.model_dump(mode="json")
        else:
            continue
        directory_path = str(graph_payload.get("directory_path") or "").strip() or "root"
        if directory_path in seen_directory_paths:
            continue
        seen_directory_paths.add(directory_path)
        directory_paths.append(directory_path)
    return tuple(directory_paths)


def _graph_directory_paths_by_node_id(graphs: list[object]) -> dict[str, tuple[str, ...]]:
    directory_paths_by_node_id: dict[str, list[str]] = {}
    for graph in graphs:
        if isinstance(graph, dict):
            graph_payload = graph
        elif hasattr(graph, "model_dump"):
            graph_payload = graph.model_dump(mode="json")
        else:
            continue
        directory_path = str(graph_payload.get("directory_path") or "").strip() or "root"
        node_ids = graph_payload.get("node_ids")
        if not isinstance(node_ids, list):
            continue
        for raw_node_id in node_ids:
            node_id = str(raw_node_id or "").strip()
            if not node_id:
                continue
            directory_paths = directory_paths_by_node_id.setdefault(node_id, [])
            if directory_path not in directory_paths:
                directory_paths.append(directory_path)
    return {
        node_id: tuple(directory_paths)
        for node_id, directory_paths in directory_paths_by_node_id.items()
    }


def _graph_directory_paths_for_node_ids(
    graphs: list[object], node_ids: Iterable[str]
) -> tuple[str, ...]:
    node_directory_paths = _graph_directory_paths_by_node_id(graphs)
    directory_paths: list[str] = []
    seen_directory_paths: set[str] = set()
    for node_id in node_ids:
        for directory_path in node_directory_paths.get(str(node_id or "").strip(), ()):
            if directory_path in seen_directory_paths:
                continue
            seen_directory_paths.add(directory_path)
            directory_paths.append(directory_path)
    return tuple(directory_paths)


def _readable_workflow_run_graphs(
    *, auth: AdminWebAuth, request: Request, graphs: list[object]
) -> list[dict[str, Any]]:
    readable_graphs = _readable_graphs_for_document(
        auth=auth,
        request=request,
        graphs=graphs,
        permission=WORKFLOW_RUNS_READ_PERMISSION,
    )
    if readable_graphs:
        return readable_graphs
    if not graphs:
        auth.require_permission(request, WORKFLOW_RUNS_READ_PERMISSION, directory_path="root")
        return []
    raise PermissionError("Permission required: workflow_runs.read")


def _visible_workflow_run_node_ids(readable_graphs: list[dict[str, Any]]) -> set[str]:
    node_ids: set[str] = set()
    for graph in readable_graphs:
        for raw_node_id in graph.get("node_ids", []):
            node_id = str(raw_node_id or "").strip()
            if node_id:
                node_ids.add(node_id)
    return node_ids


def _filter_workflow_run_snapshot_payload(
    snapshot: Any, *, readable_node_ids: set[str]
) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    runs = payload.get("runs")
    if isinstance(runs, list):
        filtered_runs: list[dict[str, Any]] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            active_nodes = run.get("active_nodes")
            if isinstance(active_nodes, list):
                filtered_active_nodes = [
                    node
                    for node in active_nodes
                    if isinstance(node, dict)
                    and str(node.get("node_id") or "").strip() in readable_node_ids
                ]
                if not filtered_active_nodes:
                    continue
                run["active_nodes"] = filtered_active_nodes
            else:
                continue
            failed_node_id = str(run.get("failed_node_id") or "").strip()
            if failed_node_id and failed_node_id not in readable_node_ids:
                for field_name in ("failed_node_id", "failed_node_status", "failed_at", "error"):
                    run[field_name] = None
            filtered_runs.append(run)
        payload["runs"] = filtered_runs
    node_activity = payload.get("node_activity")
    if isinstance(node_activity, dict):
        payload["node_activity"] = {
            node_id: counts
            for node_id, counts in node_activity.items()
            if str(node_id or "").strip() in readable_node_ids
        }
    return payload


def _workflow_run_detail_node_ids(payload: Any) -> set[str]:
    node_ids: set[str] = set()
    payload_dict = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    if not isinstance(payload_dict, dict):
        return node_ids
    for node in payload_dict.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or "").strip()
        if node_id:
            node_ids.add(node_id)
    for node in payload_dict.get("active_nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or "").strip()
        if node_id:
            node_ids.add(node_id)
    trigger_event = payload_dict.get("trigger_event")
    if isinstance(trigger_event, dict):
        trigger_id = str(trigger_event.get("trigger_id") or "").strip()
        if trigger_id:
            node_ids.add(trigger_id)
    source_node_id = str(payload_dict.get("source_node_id") or "").strip()
    if source_node_id:
        node_ids.add(source_node_id)
    return node_ids


def _require_workflow_run_read_access(
    *, auth: AdminWebAuth, request: Request, graphs: list[object]
) -> tuple[list[dict[str, Any]], set[str]]:
    readable_graphs = _readable_workflow_run_graphs(auth=auth, request=request, graphs=graphs)
    return readable_graphs, _visible_workflow_run_node_ids(readable_graphs)


def _require_workflow_run_control_access(
    *,
    auth: AdminWebAuth,
    request: Request,
    graphs: list[object],
    node_ids: Iterable[str],
) -> None:
    if not graphs:
        auth.require_permission(request, WORKFLOW_RUNS_CONTROL_PERMISSION, directory_path="root")
        return
    directory_paths = _graph_directory_paths_for_node_ids(graphs, node_ids)
    if not directory_paths:
        raise PermissionError("Permission required: workflow_runs.control")
    for directory_path in directory_paths:
        auth.require_permission(
            request, WORKFLOW_RUNS_CONTROL_PERMISSION, directory_path=directory_path
        )


def _workflow_run_graphs_for_document(
    service: AdminRuntimeComponents, auth: AdminWebAuth, request: Request
) -> tuple[list[object], list[dict[str, Any]], set[str]]:
    document = service.workflow_config_admin.read_composed_workflow_editor_document()
    readable_graphs, readable_node_ids = _require_workflow_run_read_access(
        auth=auth, request=request, graphs=document.graphs
    )
    return document.graphs, readable_graphs, readable_node_ids


def _filter_workflow_document_payload(
    *,
    document: Any,
    readable_graphs: list[object],
) -> dict[str, Any]:
    readable_graph_ids = {
        str(graph.get("id") or "").strip()
        for graph in readable_graphs
        if isinstance(graph, dict) and str(graph.get("id") or "").strip()
    }
    readable_node_ids: set[str] = set()
    for graph in readable_graphs:
        if not isinstance(graph, dict):
            continue
        for node_id in graph.get("node_ids", []):
            normalized_node_id = str(node_id or "").strip()
            if normalized_node_id:
                readable_node_ids.add(normalized_node_id)

    payload = document.model_dump(mode="json")

    config = payload.get("config")
    if isinstance(config, dict):
        filtered_config = json.loads(json.dumps(config))
        for collection_name in ("triggers", "gates", "effects", "queries"):
            entries = filtered_config.get(collection_name)
            if not isinstance(entries, list):
                continue
            filtered_config[collection_name] = [
                entry
                for entry in entries
                if isinstance(entry, dict)
                and str(entry.get("id") or "").strip() in readable_node_ids
            ]
        edges = filtered_config.get("edges")
        if isinstance(edges, list):
            filtered_config["edges"] = [
                edge
                for edge in edges
                if isinstance(edge, dict)
                and str(edge.get("source_id") or "").strip() in readable_node_ids
                and str(edge.get("target_id") or "").strip() in readable_node_ids
            ]
        workflow_graphs = filtered_config.get("workflow_graphs")
        if isinstance(workflow_graphs, list):
            filtered_config["workflow_graphs"] = [
                graph
                for graph in workflow_graphs
                if isinstance(graph, dict)
                and (
                    str(graph.get("id") or "").strip() in readable_graph_ids
                    or (
                        not str(graph.get("id") or "").strip()
                        and str(graph.get("directory_path") or "").strip()
                        in {
                            str(candidate.get("directory_path") or "").strip()
                            for candidate in readable_graphs
                            if isinstance(candidate, dict)
                        }
                    )
                )
            ]
        payload["config"] = filtered_config

    layout = payload.get("layout")
    if isinstance(layout, dict):
        nodes = layout.get("nodes")
        if isinstance(nodes, dict):
            layout["nodes"] = {
                node_id: node_value
                for node_id, node_value in nodes.items()
                if str(node_id or "").strip() in readable_node_ids
            }

    output_schemas = payload.get("output_schemas")
    if isinstance(output_schemas, dict):
        payload["output_schemas"] = {
            node_id: schema
            for node_id, schema in output_schemas.items()
            if str(node_id or "").strip() in readable_node_ids
        }

    graphs_payload = payload.get("graphs")
    if isinstance(graphs_payload, list):
        payload["graphs"] = [
            graph
            for graph in graphs_payload
            if isinstance(graph, dict) and str(graph.get("id") or "").strip() in readable_graph_ids
        ]
    return payload


def _graph_directories_from_payload(
    service: AdminRuntimeComponents,
    payload: dict[str, Any],
    *,
    fallback_graphs: list[Any] | None = None,
) -> tuple[str, ...]:
    graphs_payload = payload.get("graphs")
    submitted_graphs = service.workflow_config_admin.coerce_workflow_editor_graphs(
        graphs_payload,
        strict=False,
    )
    directories = _graph_directory_paths(
        [graph.model_dump(mode="json") for graph in submitted_graphs]
    )
    if directories:
        return directories
    if graphs_payload is None or (isinstance(graphs_payload, list) and len(graphs_payload) == 0):
        fallback = fallback_graphs or []
        fallback_directories = _graph_directory_paths(
            [
                graph.model_dump(mode="json") if hasattr(graph, "model_dump") else graph
                for graph in fallback
            ]
        )
        if fallback_directories:
            return fallback_directories
        return ("root",)
    return ()


async def workflow_run_events(
    service: AdminRuntimeComponents,
    request: Request,
    auth: AdminWebAuth | None = None,
) -> AsyncIterator[str]:
    last_state_key: str | None = None
    journal = service.workflow_config_admin.workflow_event_journal()
    cursor = journal.cursor()

    async def snapshot_event() -> tuple[str, str] | None:
        if await request.is_disconnected():
            return None
        try:
            snapshot = service.workflow_config_admin.read_workflow_run_snapshot()
            if auth is None:
                payload = json.dumps(snapshot.model_dump(mode="json"))
            else:
                document = service.workflow_config_admin.read_composed_workflow_editor_document()
                _, readable_graphs, readable_node_ids = _workflow_run_graphs_for_document(
                    service, auth, request
                )
                if not document.graphs:
                    payload = json.dumps(snapshot.model_dump(mode="json"))
                else:
                    payload = json.dumps(
                        _filter_workflow_run_snapshot_payload(
                            snapshot, readable_node_ids=readable_node_ids
                        )
                    )
                if readable_graphs:
                    state_key = json.dumps(
                        {
                            "runs": [
                                run.model_dump(mode="json")
                                for run in snapshot.runs
                                if any(
                                    node.node_id in readable_node_ids for node in run.active_nodes
                                )
                            ],
                            "node_activity": {
                                node_id: counts
                                for node_id, counts in snapshot.node_activity.items()
                                if node_id in readable_node_ids
                            },
                        },
                        sort_keys=True,
                    )
                    return state_key, f"event: snapshot\ndata: {payload}\n\n"
        except ValueError as exc:
            payload = json.dumps({"error": str(exc)})
            return f"error:{payload}", f"event: error\ndata: {payload}\n\n"
        state_key = json.dumps(
            {
                "runs": [run.model_dump(mode="json") for run in snapshot.runs],
                "node_activity": snapshot.node_activity,
                "recent_node_activity": [
                    item.model_dump(mode="json") for item in snapshot.recent_node_activity
                ],
            },
            sort_keys=True,
        )
        return state_key, f"event: snapshot\ndata: {payload}\n\n"

    first = await snapshot_event()
    if first is None:
        return
    last_state_key, first_payload = first
    yield first_payload

    while True:
        if await request.is_disconnected():
            break
        events, cursor = await journal.wait_after(cursor, timeout_seconds=15.0)
        if not events:
            yield ": keepalive\n\n"
            continue
        event = await snapshot_event()
        if event is None:
            break
        state_key, payload = event
        if state_key != last_state_key:
            last_state_key = state_key
            yield payload
        else:
            yield ": keepalive\n\n"


def workflow_routes(
    service: AdminRuntimeComponents,
    auth: AdminWebAuth,
    cache: AdminWebJsonResponseCache | None = None,
) -> list[Route]:
    async def get_document(request: Request) -> JSONResponse:
        try:
            requested_workflow_pack = request.query_params.get("workflow_pack")
            if requested_workflow_pack is None:
                composed_document = (
                    service.workflow_config_admin.read_composed_workflow_editor_document()
                )
                document = composed_document
                readable_graphs = _readable_graphs_for_document(
                    auth=auth,
                    request=request,
                    graphs=document.graphs,
                )
                for pack in composed_document.workflow_packs:
                    candidate = service.workflow_config_admin.read_workflow_editor_document(
                        workflow_pack=pack.reference
                    )
                    candidate_readable_graphs = _readable_graphs_for_document(
                        auth=auth,
                        request=request,
                        graphs=candidate.graphs,
                    )
                    if candidate.graphs:
                        if not candidate_readable_graphs:
                            continue
                    elif not auth.can(request, WORKFLOWS_READ_PERMISSION, directory_path="root"):
                        continue
                    document = candidate
                    readable_graphs = candidate_readable_graphs
                    break
            else:
                document = service.workflow_config_admin.read_workflow_editor_document(
                    workflow_pack=requested_workflow_pack
                )
                readable_graphs = _readable_graphs_for_document(
                    auth=auth,
                    request=request,
                    graphs=document.graphs,
                )
            if document.graphs and not readable_graphs:
                raise PermissionError("Permission required: workflows.read")
            if not document.graphs and not auth.can(
                request, WORKFLOWS_READ_PERMISSION, directory_path="root"
            ):
                raise PermissionError("Permission required: workflows.read")
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(
            _filter_workflow_document_payload(
                document=document,
                readable_graphs=readable_graphs if document.graphs else [],
            )
        )

    async def get_catalog(request: Request) -> JSONResponse:
        try:
            directory_path = service.teams_admin.normalize_directory_reference(
                request.query_params.get("directory_path") or "root"
            )
            auth.require_permission(
                request, WORKFLOWS_READ_PERMISSION, directory_path=directory_path
            )
            catalog = service.workflow_config_admin.read_workflow_editor_catalog(
                directory_path=directory_path
            )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(catalog.model_dump(mode="json"))

    async def get_target_catalog(request: Request) -> JSONResponse:
        try:
            directory_path = service.teams_admin.normalize_directory_reference(
                request.query_params.get("directory_path") or "root"
            )
            auth.require_permission(
                request, WORKFLOWS_READ_PERMISSION, directory_path=directory_path
            )
            sessions_result, vms_result = await asyncio.gather(
                run_in_threadpool(
                    service.vm_agent_admin.list_sessions,
                    include_expired=True,
                    directory_path=directory_path,
                    direct_only=False,
                ),
                run_in_threadpool(
                    service.vm_agent_admin.list_vms,
                    include_inactive=True,
                    directory_path=directory_path,
                    direct_only=False,
                ),
                return_exceptions=True,
            )
            sessions = [] if isinstance(sessions_result, BaseException) else sessions_result
            vms = [] if isinstance(vms_result, BaseException) else vms_result
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(
            {
                "sessions": [
                    {
                        "session_id": session.session_id,
                        "is_expired": session.is_expired,
                    }
                    for session in sessions
                ],
                "vms": [{"vm_name": vm.vm_name} for vm in vms],
            }
        )

    async def get_runs(request: Request) -> JSONResponse:
        try:
            document = service.workflow_config_admin.read_composed_workflow_editor_document()
            snapshot = service.workflow_config_admin.read_workflow_run_snapshot()
            if not document.graphs:
                auth.require_permission(
                    request, WORKFLOW_RUNS_READ_PERMISSION, directory_path="root"
                )
                return JSONResponse(snapshot.model_dump(mode="json"))
            readable_graphs, readable_node_ids = _require_workflow_run_read_access(
                auth=auth, request=request, graphs=document.graphs
            )
            return JSONResponse(
                _filter_workflow_run_snapshot_payload(snapshot, readable_node_ids=readable_node_ids)
            )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)

    async def get_run_search(request: Request) -> JSONResponse:
        query = request.query_params
        try:
            document = service.workflow_config_admin.read_composed_workflow_editor_document()
            graph_id = query.get("graph_id")
            node_id = query.get("node_id")
            if not document.graphs:
                auth.require_permission(
                    request, WORKFLOW_RUNS_READ_PERMISSION, directory_path="root"
                )
            else:
                directory_paths: list[str] = []
                if graph_id:
                    graph = next(
                        (candidate for candidate in document.graphs if candidate.id == graph_id),
                        None,
                    )
                    if graph is None:
                        raise ValueError(f"Workflow graph not found: {graph_id}")
                    directory_paths.extend([graph.directory_path])
                if node_id:
                    directory_paths.extend(
                        _graph_directory_paths_for_node_ids(document.graphs, [node_id])
                    )
                if (graph_id or node_id) and not directory_paths:
                    raise PermissionError("Permission required: workflow_runs.read")
                if not directory_paths:
                    raise ValueError("Workflow run search requires node_id or graph_id.")
                for directory_path in dict.fromkeys(directory_paths):
                    auth.require_permission(
                        request, WORKFLOW_RUNS_READ_PERMISSION, directory_path=directory_path
                    )
            payload = service.workflow_config_admin.read_workflow_run_search(
                node_id=node_id,
                graph_id=graph_id,
                without_session=optional_bool_from_query(
                    query.get("without_session"),
                    default=False,
                ),
                include_completed=optional_bool_from_query(
                    query.get("include_completed"),
                    default=True,
                ),
                limit=optional_limit_from_query(query.get("limit")),
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    def require_node_execution_read(request: Request, *, node_id: str) -> None:
        document = service.workflow_config_admin.read_composed_workflow_editor_document()
        if not document.graphs:
            auth.require_permission(request, WORKFLOW_RUNS_READ_PERMISSION, directory_path="root")
            return
        directory_paths = _graph_directory_paths_for_node_ids(document.graphs, [node_id])
        if not directory_paths:
            raise PermissionError("Permission required: workflow_runs.read")
        for directory_path in directory_paths:
            auth.require_permission(
                request, WORKFLOW_RUNS_READ_PERMISSION, directory_path=directory_path
            )

    async def post_node_execution_search(request: Request) -> JSONResponse:
        try:
            search = WorkflowNodeExecutionSearchRequestPayload.model_validate(
                await json_body(request)
            )
            require_node_execution_read(request, node_id=search.node_id)
            payload = service.workflow_config_admin.read_workflow_node_execution_search(
                node_id=search.node_id,
                started_from=search.started_from,
                started_to=search.started_to,
                statuses=search.statuses,
                filters=[item.model_dump(mode="json") for item in search.filters],
                limit=search.limit,
                cursor=search.cursor,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    async def post_node_execution_fields(request: Request) -> JSONResponse:
        try:
            search = WorkflowNodeExecutionFieldsRequestPayload.model_validate(
                await json_body(request)
            )
            require_node_execution_read(request, node_id=search.node_id)
            payload = service.workflow_config_admin.read_workflow_node_execution_fields(
                node_id=search.node_id,
                started_from=search.started_from,
                started_to=search.started_to,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    async def get_node_execution_detail(request: Request) -> JSONResponse:
        execution_key = str(request.path_params.get("execution_key") or "").strip()
        try:
            if not execution_key:
                raise ValueError("execution_key is required")
            payload = service.workflow_config_admin.read_workflow_node_execution_detail(
                execution_key
            )
            require_node_execution_read(request, node_id=payload.node_id)
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    async def get_run_detail(request: Request) -> JSONResponse:
        query = request.query_params
        try:
            payload = service.workflow_config_admin.read_workflow_run_detail(
                run_id=query.get("run_id"),
                event_id=query.get("event_id"),
                trigger_event_id=query.get("trigger_event_id"),
                trace_id=query.get("trace_id"),
                session_id=query.get("session_id"),
                artifact_id=query.get("artifact_id"),
            )
            document = service.workflow_config_admin.read_composed_workflow_editor_document()
            if not document.graphs:
                auth.require_permission(
                    request, WORKFLOW_RUNS_READ_PERMISSION, directory_path="root"
                )
            else:
                _require_workflow_run_read_access(
                    auth=auth, request=request, graphs=document.graphs
                )
                involved_node_ids = _workflow_run_detail_node_ids(payload)
                directory_paths = _graph_directory_paths_for_node_ids(
                    document.graphs, involved_node_ids
                )
                if not directory_paths:
                    raise PermissionError("Permission required: workflow_runs.read")
                for directory_path in directory_paths:
                    auth.require_permission(
                        request, WORKFLOW_RUNS_READ_PERMISSION, directory_path=directory_path
                    )
        except ValueError as exc:
            status_code = 404 if "did not match any stored run" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    async def clear_run(request: Request) -> JSONResponse:
        event_id = request.path_params.get("event_id", "")
        try:
            document = service.workflow_config_admin.read_composed_workflow_editor_document()
            payload = service.workflow_config_admin.read_workflow_run_detail(event_id=event_id)
            _require_workflow_run_control_access(
                auth=auth,
                request=request,
                graphs=document.graphs,
                node_ids=_workflow_run_detail_node_ids(payload),
            )
            payload = service.workflow_config_admin.clear_workflow_run(event_id)
        except ValueError as exc:
            status_code = 404 if "not found or already inactive" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    async def dismiss_node_failures(request: Request) -> JSONResponse:
        node_id = request.path_params.get("node_id", "")
        try:
            document = service.workflow_config_admin.read_composed_workflow_editor_document()
            _require_workflow_run_control_access(
                auth=auth,
                request=request,
                graphs=document.graphs,
                node_ids=[node_id],
            )
            payload = service.workflow_config_admin.dismiss_workflow_node_failures(node_id)
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    async def resume_run(request: Request) -> JSONResponse:
        event_id = request.path_params.get("event_id", "")
        try:
            document = service.workflow_config_admin.read_composed_workflow_editor_document()
            payload = service.workflow_config_admin.read_workflow_run_detail(event_id=event_id)
            _require_workflow_run_control_access(
                auth=auth,
                request=request,
                graphs=document.graphs,
                node_ids=_workflow_run_detail_node_ids(payload),
            )
            payload = service.workflow_config_admin.resume_workflow_run(event_id)
        except ValueError as exc:
            status_code = 404 if "not resumable" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    async def stream_runs(request: Request) -> StreamingResponse:
        try:
            document = service.workflow_config_admin.read_composed_workflow_editor_document()
            if document.graphs:
                _require_workflow_run_read_access(
                    auth=auth, request=request, graphs=document.graphs
                )
            else:
                auth.require_permission(
                    request, WORKFLOW_RUNS_READ_PERMISSION, directory_path="root"
                )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return StreamingResponse(
            workflow_run_events(service, request, auth=auth),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    async def validate_document(request: Request) -> JSONResponse:
        try:
            auth.require(request)
            payload = await json_body(request)
            fallback_graphs = None
            if not isinstance(payload.get("graphs"), list) or not payload.get("graphs"):
                fallback_graphs = service.workflow_config_admin.read_workflow_editor_document(
                    workflow_pack=(
                        str(payload.get("workflow_pack"))
                        if payload.get("workflow_pack") is not None
                        else None
                    )
                ).graphs
            for directory_path in _graph_directories_from_payload(
                service,
                payload,
                fallback_graphs=fallback_graphs,
            ):
                auth.require_permission(
                    request, WORKFLOWS_READ_PERMISSION, directory_path=directory_path
                )
            validation = service.workflow_config_admin.validate_workflow_editor_document(
                config=payload.get("config") if isinstance(payload.get("config"), dict) else {},
                layout=payload.get("layout") if isinstance(payload.get("layout"), dict) else {},
                graphs=payload.get("graphs") if isinstance(payload.get("graphs"), list) else None,
                workflow_pack=(
                    str(payload.get("workflow_pack"))
                    if payload.get("workflow_pack") is not None
                    else None
                ),
            )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(validation.model_dump(mode="json"))

    async def create_workflow_pack(request: Request) -> JSONResponse:
        try:
            session = auth.require(request)
            payload = await json_body(request)
            auth.require_permission(
                request, WORKFLOW_PACKS_MANAGE_PERMISSION, directory_path="root"
            )
            if not request_has_privileged_operations(auth=auth, service=service, request=request):
                return json_error("Privileged operations are locked.", status_code=403)
            expected_revision = expected_revision_from_payload(payload)
            result = service.workflow_config_admin.create_workflow_pack(
                pack_id=str(payload.get("pack_id") or ""),
                pack_version=(
                    str(payload.get("pack_version"))
                    if payload.get("pack_version") is not None
                    else None
                ),
                description=(
                    str(payload.get("description"))
                    if payload.get("description") is not None
                    else None
                ),
                expected_revision=expected_revision,
                actor=workflow_pack_actor_from_session(session),
            )
        except KeyError as exc:
            return json_error(str(exc).strip("'"), status_code=428)
        except AdminRevisionConflict as exc:
            return json_error(str(exc), status_code=409, details=exc.details)
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(result.model_dump(mode="json"))

    async def update_workflow_pack(request: Request) -> JSONResponse:
        try:
            session = auth.require(request)
            payload = await json_body(request)
            auth.require_permission(
                request, WORKFLOW_PACKS_MANAGE_PERMISSION, directory_path="root"
            )
            if not request_has_privileged_operations(auth=auth, service=service, request=request):
                return json_error("Privileged operations are locked.", status_code=403)
            expected_revision = expected_revision_from_payload(payload)
            result = service.workflow_config_admin.update_workflow_pack_metadata(
                workflow_pack=str(payload.get("workflow_pack") or ""),
                pack_id=str(payload.get("pack_id") or ""),
                pack_version=str(payload.get("pack_version") or ""),
                description=(
                    str(payload.get("description"))
                    if payload.get("description") is not None
                    else None
                ),
                expected_revision=expected_revision,
                actor=workflow_pack_actor_from_session(session),
            )
        except KeyError as exc:
            return json_error(str(exc).strip("'"), status_code=428)
        except AdminRevisionConflict as exc:
            return json_error(str(exc), status_code=409, details=exc.details)
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(result.model_dump(mode="json"))

    async def remove_workflow_pack(request: Request) -> JSONResponse:
        try:
            session = auth.require(request)
            payload = await json_body(request)
            auth.require_permission(
                request, WORKFLOW_PACKS_MANAGE_PERMISSION, directory_path="root"
            )
            if not request_has_privileged_operations(auth=auth, service=service, request=request):
                return json_error("Privileged operations are locked.", status_code=403)
            expected_revision = expected_revision_from_payload(payload)
            result = service.workflow_config_admin.remove_workflow_pack(
                workflow_pack=str(payload.get("workflow_pack") or ""),
                expected_revision=expected_revision,
                actor=workflow_pack_actor_from_session(session),
            )
        except KeyError as exc:
            return json_error(str(exc).strip("'"), status_code=428)
        except AdminRevisionConflict as exc:
            return json_error(str(exc), status_code=409, details=exc.details)
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(result.model_dump(mode="json"))

    async def list_workflow_pack_history(request: Request) -> JSONResponse:
        try:
            auth.require_permission(request, WORKFLOWS_READ_PERMISSION, directory_path="root")
            payload = service.workflow_config_admin.list_workflow_pack_history(
                workflow_pack=request.query_params.get("workflow_pack"),
                limit=optional_limit_from_query(request.query_params.get("limit"), default=50),
                cursor=request.query_params.get("cursor"),
            )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    async def get_workflow_pack_history_detail(request: Request) -> JSONResponse:
        try:
            auth.require_permission(request, WORKFLOWS_READ_PERMISSION, directory_path="root")
            payload = service.workflow_config_admin.get_workflow_pack_history_detail(
                str(request.path_params.get("change_id") or "")
            )
        except ValueError as exc:
            status_code = 404 if "Unknown workflow pack history change" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(payload.model_dump(mode="json"))

    async def generate_draft(request: Request) -> JSONResponse:
        try:
            auth.require(request)
            payload = await json_body(request)
            directory_path = service.teams_admin.normalize_directory_reference(
                str(payload.get("directory_path") or "").strip() or "root"
            )
            auth.require_permission(
                request, WORKFLOWS_WRITE_PERMISSION, directory_path=directory_path
            )
            draft = await service.workflow_generation_admin.generate_workflow_editor_draft(
                description=str(payload.get("description") or ""),
                directory_path=directory_path,
                include_current_packs=bool(payload.get("include_current_packs", True)),
            )
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(draft.model_dump(mode="json"))

    async def save_document(request: Request) -> JSONResponse:
        try:
            session = auth.require(request)
            payload = await json_body(request)
            fallback_graphs = None
            if not isinstance(payload.get("graphs"), list) or not payload.get("graphs"):
                fallback_graphs = service.workflow_config_admin.read_workflow_editor_document(
                    workflow_pack=(
                        str(payload.get("workflow_pack"))
                        if payload.get("workflow_pack") is not None
                        else None
                    )
                ).graphs
            for directory_path in _graph_directories_from_payload(
                service,
                payload,
                fallback_graphs=fallback_graphs,
            ):
                auth.require_permission(
                    request, WORKFLOWS_WRITE_PERMISSION, directory_path=directory_path
                )
            if not request_has_privileged_operations(auth=auth, service=service, request=request):
                return json_error("Privileged operations are locked.", status_code=403)
            expected_revision = expected_revision_from_payload(payload)
            save_result = service.workflow_config_admin.save_workflow_editor_document(
                config=payload.get("config") if isinstance(payload.get("config"), dict) else {},
                layout=payload.get("layout") if isinstance(payload.get("layout"), dict) else {},
                graphs=payload.get("graphs") if isinstance(payload.get("graphs"), list) else None,
                expected_revision=expected_revision,
                workflow_pack=(
                    str(payload.get("workflow_pack"))
                    if payload.get("workflow_pack") is not None
                    else None
                ),
                actor=workflow_pack_actor_from_session(session),
            )
        except KeyError as exc:
            return json_error(str(exc).strip("'"), status_code=428)
        except AdminRevisionConflict as exc:
            return json_error(str(exc), status_code=409, details=exc.details)
        except ValueError as exc:
            return json_error(str(exc))
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return JSONResponse(save_result.model_dump(mode="json"))

    return [
        Route(
            "/api/workflow-editor/document",
            cache.wrap("workflow.document", get_document) if cache else get_document,
            methods=["GET"],
        ),
        Route(
            "/api/workflow-editor/catalog",
            cache.wrap("workflow.catalog", get_catalog) if cache else get_catalog,
            methods=["GET"],
        ),
        Route("/api/workflow-editor/target-catalog", get_target_catalog, methods=["GET"]),
        Route(
            "/api/workflow-editor/runs",
            cache.wrap("workflow.runs", get_runs) if cache else get_runs,
            methods=["GET"],
        ),
        Route("/api/workflow-editor/run-search", get_run_search, methods=["GET"]),
        Route(
            "/api/workflow-editor/node-executions/search",
            post_node_execution_search,
            methods=["POST"],
        ),
        Route(
            "/api/workflow-editor/node-executions/fields",
            post_node_execution_fields,
            methods=["POST"],
        ),
        Route(
            "/api/workflow-editor/node-executions/{execution_key:path}",
            get_node_execution_detail,
            methods=["GET"],
        ),
        Route("/api/workflow-editor/run-detail", get_run_detail, methods=["GET"]),
        Route(
            "/api/workflow-editor/node-failures/{node_id:path}",
            dismiss_node_failures,
            methods=["DELETE"],
        ),
        Route("/api/workflow-editor/runs/{event_id:path}", clear_run, methods=["DELETE"]),
        Route("/api/workflow-editor/runs/{event_id:path}/resume", resume_run, methods=["POST"]),
        Route("/api/workflow-editor/runs/stream", stream_runs, methods=["GET"]),
        Route("/api/workflow-editor/packs/history", list_workflow_pack_history, methods=["GET"]),
        Route(
            "/api/workflow-editor/packs/history/{change_id}",
            get_workflow_pack_history_detail,
            methods=["GET"],
        ),
        Route("/api/workflow-editor/packs", create_workflow_pack, methods=["POST"]),
        Route("/api/workflow-editor/packs", update_workflow_pack, methods=["PUT"]),
        Route("/api/workflow-editor/packs", remove_workflow_pack, methods=["DELETE"]),
        Route("/api/workflow-editor/validate", validate_document, methods=["POST"]),
        Route("/api/workflow-editor/generate-draft", generate_draft, methods=["POST"]),
        Route("/api/workflow-editor/document", save_document, methods=["PUT"]),
    ]
