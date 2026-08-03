from __future__ import annotations

import base64
import binascii
import json
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.cache import AdminWebJsonResponseCache
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import (
    AdminWebAuth,
    json_error,
    request_has_privileged_operations,
)

DASHBOARD_AGENT_LIST_CACHE_TTL_SECONDS = 10.0
DASHBOARD_AGENT_INVENTORY_PARAM = "_dashboard_agent_inventory"
AGENT_EXECUTION_HISTORY_DEFAULT_LIMIT = 50
AGENT_EXECUTION_HISTORY_MAX_LIMIT = 100


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


def _compact_agent_codex_usage(host: dict[str, Any]) -> dict[str, Any] | None:
    session_policy = host.get("session_policy")
    if not isinstance(session_policy, dict):
        return None
    compact = {
        key: _jsonable(session_policy[key])
        for key in ("effective_codex_auth_mode", "codex_auth_mode_source")
        if key in session_policy
    }
    metadata = session_policy.get("metadata")
    if isinstance(metadata, dict) and "agent_provider" in metadata:
        compact["agent_provider"] = _jsonable(metadata["agent_provider"])
    return compact or None


def _compact_agent_host(
    payload: dict[str, Any],
    *,
    include_codex_usage: bool,
) -> dict[str, Any]:
    host = payload.get("host")
    if not isinstance(host, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "kind",
        "vm_name",
        "provider",
        "issue_number",
        "inventory_status",
        "power_state",
        "libvirt_state",
        "provider_state",
        "latest_execution_id",
        "worker_job_id",
        "last_status",
        "execution_count",
        "active_execution_count",
        "image",
        "image_alias",
        "labels",
        "directory_paths",
        "session_ids",
        "runtime_image",
        "inventory_error",
        "reconcile_error",
    ):
        if key in host:
            compact[key] = _jsonable(host[key])
    if include_codex_usage:
        codex_usage = _compact_agent_codex_usage(host)
        if codex_usage is not None:
            compact["codex_usage"] = codex_usage
    return compact


def _compact_prompt_queue(payload: dict[str, Any]) -> dict[str, Any]:
    prompt_queue = payload.get("prompt_queue")
    if not isinstance(prompt_queue, dict):
        return {}
    return {
        key: _jsonable(prompt_queue.get(key))
        for key in (
            "record_count",
            "coalesced_count",
            "oldest_created_at",
            "newest_updated_at",
            "truncated_count",
            "read_error",
        )
        if key in prompt_queue
    }


def _compact_agent_record_payload(
    record: Any,
    *,
    include_codex_usage: bool = False,
) -> dict[str, Any]:
    payload = _jsonable(record)
    if not isinstance(payload, dict):
        return {}
    compact = {
        key: payload.get(key)
        for key in (
            "agent_id",
            "backend",
            "target_name",
            "session_id",
            "directory_path",
            "status",
            "started_at",
            "finished_at",
            "updated_at",
            "capabilities",
        )
        if key in payload
    }
    compact["host"] = _compact_agent_host(
        payload,
        include_codex_usage=include_codex_usage,
    )
    compact["prompt_queue"] = _compact_prompt_queue(payload)
    return compact


def _encode_agent_execution_cursor(created_at: Any, execution_id: Any) -> str:
    created_at_text = str(created_at or "").strip().replace(" ", "T")
    if created_at_text.endswith("+00:00"):
        created_at_text = created_at_text.removesuffix("+00:00") + "Z"
    payload = json.dumps(
        {"created_at": created_at_text, "execution_id": str(execution_id or "")},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_agent_execution_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if cursor is None or not cursor.strip():
        return None, None
    try:
        padded = cursor.strip() + "=" * (-len(cursor.strip()) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (UnicodeEncodeError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("Invalid agent execution cursor.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid agent execution cursor.")
    created_at = str(payload.get("created_at") or "").strip()
    execution_id = str(payload.get("execution_id") or "").strip()
    if not created_at or not execution_id:
        raise ValueError("Invalid agent execution cursor.")
    return created_at, execution_id


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


def _optional_bool_query(request: Request, name: str) -> bool | None:
    value = request.query_params.get(name)
    if value is None:
        return None
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _string_query(request: Request, name: str, *, default: str | None = None) -> str | None:
    value = request.query_params.get(name)
    if value is None:
        return default
    normalized = value.strip()
    return normalized or default


def _int_query(request: Request, name: str, *, default: int) -> int:
    value = request.query_params.get(name)
    if value is None:
        return default
    return int(value.strip())


def agent_routes(
    service: AdminRuntimeComponents,
    _auth: AdminWebAuth,
    cache: AdminWebJsonResponseCache | None = None,
) -> list[BaseRoute]:
    def require_privileged_unlocked(request: Request) -> JSONResponse | None:
        if request_has_privileged_operations(auth=_auth, service=service, request=request):
            return None
        return json_error("Privileged operations are locked.", status_code=403)

    def require_owner_privileged(
        request: Request, *, directory_path: str | None = None
    ) -> JSONResponse | None:
        if request_has_privileged_operations(
            auth=_auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return None
        return json_error("Privileged operations are locked.", status_code=403)

    def payload_directory_path(payload: dict[str, Any]) -> str:
        return str(payload.get("directory_path") or "root").strip() or "root"

    def _unique_directory_paths(paths: list[object]) -> tuple[str, ...]:
        normalized = [str(path or "root").strip() or "root" for path in paths]
        return tuple(dict.fromkeys(normalized)) or ("root",)

    def agent_directory_paths(
        agent_id: str,
        *,
        backend: str | None = None,
        allow_ambiguous: bool = False,
    ) -> tuple[str, ...]:
        selected_backend = str(backend or "all").strip() or "all"
        if selected_backend in {"all", "libvirt_vm"}:
            direct_session = service.runtime.session_repository.get_session(agent_id)
            if direct_session is not None:
                return _unique_directory_paths([direct_session.directory_path])
            matches = service.runtime.session_repository.list_sessions_for_vm(agent_id)
            if matches:
                if len(matches) > 1 and not allow_ambiguous:
                    session_ids = ", ".join(sorted(session.session_id for session in matches))
                    raise ValueError(
                        f"VM {agent_id!r} is associated with multiple sessions: {session_ids}"
                    )
                return _unique_directory_paths([session.directory_path for session in matches])
            summaries = service.vm_agent_admin.list_vms(
                include_inactive=True,
                omit_state=True,
                directory_path=None,
                direct_only=False,
            )
            for summary in summaries:
                if summary.vm_name == agent_id:
                    return _unique_directory_paths(list(summary.directory_paths))

        if selected_backend in {"all", "container_worker_vm"}:
            resolve_container = getattr(
                service.vm_agent_admin, "resolve_container_agent_session", None
            )
            if callable(resolve_container):
                session, record = resolve_container(agent_id, require_session=False)
                if session is not None:
                    return _unique_directory_paths([session.directory_path])
                if record is not None:
                    return _unique_directory_paths([getattr(record, "directory_path", None)])

        return ("root",)

    def agent_directory_path(agent_id: str, *, backend: str | None = None) -> str:
        return agent_directory_paths(agent_id, backend=backend)[0]

    def agent_record_directory_paths(record: Any) -> tuple[str, ...]:
        host = getattr(record, "host", None)
        if isinstance(host, dict):
            directory_paths = host.get("directory_paths")
            if isinstance(directory_paths, (list, tuple)) and directory_paths:
                return _unique_directory_paths(list(directory_paths))
        return _unique_directory_paths([getattr(record, "directory_path", None)])

    def require_owner_for_directory_paths(
        request: Request, directory_paths: tuple[str, ...]
    ) -> JSONResponse | None:
        for directory_path in directory_paths:
            if error_response := require_owner_privileged(request, directory_path=directory_path):
                return error_response
        return None

    def require_owner_read_for_directory_paths(
        request: Request, directory_paths: tuple[str, ...]
    ) -> JSONResponse | None:
        for directory_path in directory_paths:
            try:
                _auth.require_permission(
                    request,
                    ADMIN_OWNER_PERMISSION,
                    directory_path=directory_path,
                )
            except PermissionError as exc:
                return json_error(str(exc), status_code=403)
        return None

    def require_owner_read(request: Request, *, directory_path: str) -> JSONResponse | None:
        return require_owner_read_for_directory_paths(request, (directory_path,))

    async def list_agents(request: Request) -> JSONResponse:
        directory_path = _string_query(request, "directory_path")
        direct_only = _bool_query(request, "direct_only", default=True)
        include_inactive = _bool_query(request, "include_inactive", default=True)
        backend = _string_query(request, "backend", default="all")
        summary_only = _bool_query(request, "summary_only", default=False)
        dashboard_request = _bool_query(request, "_dashboard", default=False)
        dashboard_agent_inventory = _bool_query(
            request, DASHBOARD_AGENT_INVENTORY_PARAM, default=False
        )
        reconcile_containers = _optional_bool_query(request, "reconcile_containers")
        if reconcile_containers is None:
            reconcile_containers = not summary_only
        omit_vm_state = _optional_bool_query(request, "omit_vm_state")
        if omit_vm_state is None:
            omit_vm_state = summary_only
        try:
            records = await run_in_threadpool(
                service.vm_agent_admin.list_agents,
                directory_path=directory_path,
                direct_only=direct_only,
                include_inactive=include_inactive,
                reconcile_containers=reconcile_containers,
                omit_vm_state=omit_vm_state,
                summary_only=summary_only,
                backend=backend,
                include_prompt_queue=True,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(
            {
                "items": [
                    _compact_agent_record_payload(
                        record,
                        include_codex_usage=not (
                            dashboard_request and dashboard_agent_inventory
                        ),
                    )
                    if summary_only
                    else _jsonable(record)
                    for record in records
                ],
                "directory_path": directory_path or "root",
                "direct_only": direct_only,
                "backend": backend,
                "summary_only": summary_only,
                "reconcile_containers": reconcile_containers,
                "omit_vm_state": omit_vm_state,
            }
        )

    async def get_agent(request: Request) -> JSONResponse:
        agent_id = request.path_params["agent_id"]
        backend = _string_query(request, "backend")
        try:
            record = await run_in_threadpool(
                service.vm_agent_admin.get_agent,
                agent_id,
                backend=backend,
                directory_path=_string_query(request, "directory_path"),
                reconcile_containers=_bool_query(request, "reconcile_containers", default=False),
                omit_vm_state=_bool_query(request, "omit_vm_state", default=True),
            )
            directory_paths = agent_record_directory_paths(record)
            if error_response := require_owner_read_for_directory_paths(request, directory_paths):
                return error_response
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(_jsonable(record))

    async def get_agent_executions(request: Request) -> JSONResponse:
        agent_id = request.path_params["agent_id"]
        try:
            directory_path = await run_in_threadpool(
                agent_directory_path,
                agent_id,
                backend="container_worker_vm",
            )
            if error_response := require_owner_read(request, directory_path=directory_path):
                return error_response
            limit = min(
                AGENT_EXECUTION_HISTORY_MAX_LIMIT,
                max(
                    1,
                    _int_query(
                        request,
                        "limit",
                        default=AGENT_EXECUTION_HISTORY_DEFAULT_LIMIT,
                    ),
                ),
            )
            before_created_at, before_execution_id = _decode_agent_execution_cursor(
                _string_query(request, "cursor")
            )
            rows = await run_in_threadpool(
                service.vm_agent_admin.list_container_agent_execution_page,
                agent_id,
                directory_path=_string_query(request, "directory_path"),
                limit=limit + 1,
                before_created_at=before_created_at,
                before_execution_id=before_execution_id,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more and items:
            last_item = items[-1]
            next_cursor = _encode_agent_execution_cursor(
                last_item.get("created_at"), last_item.get("execution_id")
            )
        return JSONResponse(
            {
                "agent_id": agent_id,
                "items": _jsonable(items),
                "limit": limit,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }
        )

    async def get_agent_log(request: Request) -> JSONResponse:
        agent_id = request.path_params["agent_id"]
        backend = _string_query(request, "backend")
        view = _string_query(request, "view", default="executions") or "executions"
        lines_text = _string_query(request, "lines", default="200") or "200"
        try:
            lines = max(1, int(lines_text))
            repo_name = _string_query(request, "repo_name")
            limit_requested = request.query_params.get("limit") is not None
            offset_requested = request.query_params.get("offset") is not None
            query_requested = request.query_params.get("query") is not None
            cursor_requested = request.query_params.get("cursor") is not None
            if limit_requested or offset_requested or query_requested or cursor_requested:
                page = await run_in_threadpool(
                    service.vm_agent_admin.get_admin_agent_log_page,
                    agent_id,
                    backend=backend,
                    view=view,
                    limit=max(1, _int_query(request, "limit", default=lines)),
                    offset=max(0, _int_query(request, "offset", default=0)),
                    query=_string_query(request, "query"),
                    cursor=_string_query(request, "cursor"),
                    repo_name=repo_name,
                )
                return JSONResponse(
                    {
                        "agent_id": agent_id,
                        "backend": backend,
                        "view": page["view"],
                        "lines": page["limit"],
                        "text": page["text"],
                        "limit": page["limit"],
                        "offset": page["offset"],
                        "returned_lines": page["returned_lines"],
                        "total_lines": page["total_lines"],
                        "start_line": page["start_line"],
                        "end_line": page["end_line"],
                        "has_more_before": page["has_more_before"],
                        "query": page["query"],
                        "next_cursor": page.get("next_cursor"),
                    }
                )
            text = await run_in_threadpool(
                service.vm_agent_admin.get_admin_agent_log_snapshot,
                agent_id,
                backend=backend,
                view=view,
                lines=lines,
                repo_name=repo_name,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(
            {
                "agent_id": agent_id,
                "backend": backend,
                "view": view,
                "lines": lines,
                "text": text,
            }
        )

    async def create_options(request: Request) -> JSONResponse:
        directory_path = _string_query(request, "directory_path")
        try:
            options = await run_in_threadpool(
                service.vm_agent_admin.agent_create_options,
                directory_path,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(_jsonable(options))

    async def create_agent(request: Request) -> JSONResponse:
        if error_response := require_privileged_unlocked(request):
            return error_response
        try:
            payload = await _json_body(request)
            if error_response := require_owner_privileged(
                request, directory_path=payload_directory_path(payload)
            ):
                return error_response
            result = await run_in_threadpool(service.vm_agent_admin.create_admin_agent, payload)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        status_code = 201 if bool(result.get("success")) else 409
        return JSONResponse(_jsonable(result), status_code=status_code)

    async def submit_create_operation(request: Request) -> JSONResponse:
        if error_response := require_privileged_unlocked(request):
            return error_response
        try:
            payload = await _json_body(request)
            if error_response := require_owner_privileged(
                request, directory_path=payload_directory_path(payload)
            ):
                return error_response
            operation = await run_in_threadpool(
                service.vm_agent_admin.submit_admin_agent_create, payload
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"operation": _jsonable(operation)}, status_code=202)

    async def list_create_operations(request: Request) -> JSONResponse:
        directory_path = _string_query(request, "directory_path")
        try:
            operations = await run_in_threadpool(
                service.vm_agent_admin.list_admin_agent_create_operations,
                limit=_int_query(request, "limit", default=50),
                directory_path=directory_path,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"items": [_jsonable(operation) for operation in operations]})

    async def get_create_operation(request: Request) -> JSONResponse:
        operation_id = request.path_params["operation_id"]
        try:
            operation = await run_in_threadpool(
                service.vm_agent_admin.get_admin_agent_create_operation,
                operation_id,
            )
        except LookupError as exc:
            return json_error(str(exc), status_code=404)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"operation": _jsonable(operation)})

    async def patch_agent(request: Request) -> JSONResponse:
        if error_response := require_privileged_unlocked(request):
            return error_response
        agent_id = request.path_params["agent_id"]
        backend = _string_query(request, "backend")
        try:
            if error_response := require_owner_privileged(
                request,
                directory_path=await run_in_threadpool(
                    agent_directory_path, agent_id, backend=backend
                ),
            ):
                return error_response
            payload = await _json_body(request)
            result = await run_in_threadpool(
                service.vm_agent_admin.update_admin_agent,
                agent_id,
                backend=backend,
                payload=payload,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except LookupError as exc:
            return json_error(str(exc), status_code=404)
        return JSONResponse(_jsonable(result), status_code=200)

    async def delete_agent(request: Request) -> JSONResponse:
        if error_response := require_privileged_unlocked(request):
            return error_response
        agent_id = request.path_params["agent_id"]
        backend = _string_query(request, "backend")
        try:
            directory_paths = await run_in_threadpool(
                agent_directory_paths,
                agent_id,
                backend=backend,
                allow_ambiguous=True,
            )
            if error_response := require_owner_for_directory_paths(request, directory_paths):
                return error_response
            result = await run_in_threadpool(
                service.vm_agent_admin.delete_admin_agent,
                agent_id,
                backend=backend,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except LookupError as exc:
            return json_error(str(exc), status_code=404)
        status_code = 200 if bool(result.get("success")) else 409
        return JSONResponse(_jsonable(result), status_code=status_code)

    async def mutate_agent(request: Request) -> JSONResponse:
        if error_response := require_privileged_unlocked(request):
            return error_response
        agent_id = request.path_params["agent_id"]
        action = request.path_params["action"]
        backend = _string_query(request, "backend")
        try:
            if error_response := require_owner_privileged(
                request,
                directory_path=await run_in_threadpool(
                    agent_directory_path, agent_id, backend=backend
                ),
            ):
                return error_response
            payload = await _json_body(request)
            result = await run_in_threadpool(
                service.vm_agent_admin.run_admin_agent_action,
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
        Route(
            "/api/admin/agents",
            cache.wrap(
                "admin.agents.list",
                list_agents,
                ttl_seconds=DASHBOARD_AGENT_LIST_CACHE_TTL_SECONDS,
            )
            if cache
            else list_agents,
            methods=["GET"],
        ),
        Route("/api/admin/agents", create_agent, methods=["POST"]),
        Route("/api/admin/agents/create-options", create_options, methods=["GET"]),
        Route("/api/admin/agents/create-operations", submit_create_operation, methods=["POST"]),
        Route("/api/admin/agents/create-operations", list_create_operations, methods=["GET"]),
        Route(
            "/api/admin/agents/create-operations/{operation_id:str}",
            get_create_operation,
            methods=["GET"],
        ),
        Route(
            "/api/admin/agents/{agent_id:str}/executions",
            get_agent_executions,
            methods=["GET"],
        ),
        Route("/api/admin/agents/{agent_id:str}", get_agent, methods=["GET"]),
        Route("/api/admin/agents/{agent_id:str}", patch_agent, methods=["PATCH"]),
        Route("/api/admin/agents/{agent_id:str}", delete_agent, methods=["DELETE"]),
        Route("/api/admin/agents/{agent_id:str}/log", get_agent_log, methods=["GET"]),
        Route(
            "/api/admin/agents/{agent_id:str}/actions/{action:str}",
            mutate_agent,
            methods=["POST"],
        ),
    ]


__all__ = ["agent_routes"]
