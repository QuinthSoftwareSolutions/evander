from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.service import ISSUE_STATES, AdminService
from agent_sandbox.admin.web.shared import AdminWebAuth, json_error
from agent_sandbox.models import LocalIssueRecord, VmActionResult, VmSummary

_INACTIVE_ISSUE_STATES = frozenset({"Completed", "Closed", "Done"})


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


def _find_vm_summary(
    service: AdminService,
    vm_name: str,
    *,
    directory_path: str | None = None,
    direct_only: bool = False,
) -> VmSummary:
    summaries = service.vm_agent_admin.list_vms(
        include_inactive=True,
        directory_path=directory_path,
        direct_only=direct_only,
    )
    for summary in summaries:
        if summary.vm_name == vm_name:
            return summary
    raise ValueError(f"Unknown VM: {vm_name}")


def _issue_setup_default_model(service: AdminService, directory_path: str) -> str | None:
    effective_result = service.operations_admin.compute_directory_effective_config(
        directory_path, "issue_setup"
    )
    if not effective_result.ok or effective_result.data is None:
        return None
    return (
        str(effective_result.data.resolution.effective_config.get("default_model") or "").strip()
        or None
    )


def _validate_vm_model_configuration(
    service: AdminService,
    summary: VmSummary,
    *,
    directory_path: str | None = None,
    direct_only: bool = False,
) -> str | None:
    session_ids = [
        str(session_id).strip() for session_id in summary.session_ids if str(session_id).strip()
    ]
    if not session_ids:
        return None
    selected_directory = directory_path or next(iter(summary.directory_paths), "root")
    session_lookup = {
        item.session_id: item
        for item in service.operations_admin.list_sessions(
            include_expired=True,
            directory_path=selected_directory,
            direct_only=direct_only,
        )
    }
    default_model = _issue_setup_default_model(service, selected_directory)
    missing_sessions = [
        session_id
        for session_id in session_ids
        if not str(getattr(session_lookup.get(session_id), "model", "") or "").strip()
        and not default_model
    ]
    if not missing_sessions:
        return None
    joined_sessions = ", ".join(missing_sessions)
    return (
        "Cannot start/resume agent: missing session model and missing issue_setup.default_model. "
        f"Set a model for session(s): {joined_sessions}, or configure issue_setup.default_model."
    )


def _selected_issue_state_filters(
    state_filter: str | None,
) -> tuple[set[str] | None, set[str] | None]:
    normalized = str(state_filter or "active").strip()
    if not normalized or normalized.lower() == "active":
        return None, set(_INACTIVE_ISSUE_STATES)
    if normalized.lower() == "all":
        return None, None
    if normalized not in ISSUE_STATES:
        allowed = ", ".join(["active", "all", *ISSUE_STATES])
        raise ValueError(f"Invalid issue state filter: {normalized!r}. Allowed values: {allowed}.")
    return {normalized}, None


def _vm_payload(summary: VmSummary) -> dict[str, Any]:
    return {
        "vm_name": summary.vm_name,
        "provider": summary.provider,
        "issue_number": summary.issue_number,
        "session_ids": list(summary.session_ids),
        "labels": dict(summary.labels),
        "directory_paths": list(summary.directory_paths),
        "power_state": summary.power_state,
        "libvirt_state": summary.libvirt_state,
        "provider_state": summary.provider_state,
        "ip_address": summary.ip_address,
        "agent": {
            "state": summary.agent_state,
            "pid": summary.agent_pid,
            "dispatch_id": summary.dispatch_id,
            "repo_names": list(summary.agent_repo_names),
            "preferred_repo_name": summary.agent_repo_names[0]
            if len(summary.agent_repo_names) == 1
            else None,
        },
        "last_state_update_at": summary.last_state_update_at.isoformat()
        if summary.last_state_update_at
        else None,
    }


def _issue_record_payload(issue: LocalIssueRecord) -> dict[str, Any]:
    return {
        "issue_id": issue.issue_id,
        "issue_number": issue.issue_number,
        "title": issue.title,
        "body": issue.body,
        "description": issue.description,
        "reason": issue.reason,
        "rejection_reason": issue.rejection_reason,
        "yaml_path": issue.yaml_path,
        "directory_path": issue.directory_path,
        "repo_source": issue.repo_source,
        "source_type": issue.source_type,
        "state": issue.state,
        "url": issue.url,
        "tags": list(issue.tags),
        "comments": _jsonable(issue.comments),
        "source": issue.source,
        "metadata": _jsonable(issue.metadata),
        "created_at": issue.created_at.isoformat(),
        "updated_at": issue.updated_at.isoformat(),
        "remote_reference": _jsonable(issue.remote_reference)
        if issue.remote_reference is not None
        else None,
        "can_edit": issue.source_type == "local_manual",
        "can_delete": True,
        "can_create_vm": True,
    }


def _resolve_local_issue_record(service: AdminService, issue_identifier: str) -> LocalIssueRecord:
    try:
        return service._resolve_local_issue_record(issue_identifier)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


def _issue_detail_payload(payload: dict[str, Any]) -> dict[str, Any]:
    local_source_type = str(
        payload.get("local_source_type") or payload.get("source_type") or ""
    ).strip()
    return {
        "issue_id": str(payload.get("issue_id") or payload.get("issue_number") or ""),
        "issue_number": str(payload.get("issue_number") or ""),
        "title": str(payload.get("title") or ""),
        "body": str(payload.get("body") or ""),
        "description": payload.get("description"),
        "state": payload.get("state"),
        "directory_path": payload.get("directory_path"),
        "repo_source": payload.get("repo_source"),
        "yaml_path": payload.get("yaml_path"),
        "url": payload.get("url"),
        "source": payload.get("source"),
        "source_type": local_source_type or payload.get("source_type"),
        "detail_source_type": payload.get("source_type"),
        "provider": payload.get("provider"),
        "remote_issue_id": payload.get("remote_issue_id"),
        "reason": payload.get("reason"),
        "rejection_reason": payload.get("rejection_reason"),
        "tags": _jsonable(payload.get("tags") or []),
        "comments": _jsonable(payload.get("comments") or []),
        "metadata": _jsonable(payload.get("metadata") or {}),
        "remote_reference": _jsonable(payload.get("remote_reference")),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "can_edit": local_source_type == "local_manual",
        "can_delete": True,
        "can_create_vm": True,
        "raw": _jsonable(payload),
    }


def _local_issue_detail_payload(service: AdminService, issue: LocalIssueRecord) -> dict[str, Any]:
    payload = issue.model_dump(mode="json")
    payload["local_source_type"] = issue.source_type
    proxy_target = service._proxy_target_for_issue(issue)
    proxy_cache = (
        issue.metadata.get("proxy_cache")
        if isinstance(issue.metadata.get("proxy_cache"), dict)
        else {}
    )
    if proxy_target is None:
        payload["source_type"] = "local"
        return _issue_detail_payload(payload)

    provider, remote_issue_id = proxy_target
    payload.update(
        {
            "source_type": "proxy",
            "provider": provider,
            "remote_issue_id": remote_issue_id,
            "title": str(proxy_cache.get("title") or issue.title),
            "description": str(proxy_cache.get("description") or issue.description or issue.body),
            "body": str(proxy_cache.get("description") or issue.body),
            "state": str(proxy_cache.get("state") or issue.state or "") or None,
            "url": str(proxy_cache.get("url") or issue.url or "") or None,
            "comments": _jsonable(proxy_cache.get("comments") or issue.comments or []),
        }
    )
    return _issue_detail_payload(payload)


def _action_payload(result: VmActionResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "vm_name": result.vm_name,
        "action": result.action,
        "message": result.message,
        "provider": result.provider,
        "provider_operation_id": result.provider_operation_id,
        "thread_id": result.thread_id,
        "target_dir": result.target_dir,
        "guest_repo_name": result.guest_repo_name,
        "remote_url": result.remote_url,
    }


def vm_issue_routes(service: AdminService, _auth: AdminWebAuth) -> list[BaseRoute]:
    async def list_vms(request: Request) -> JSONResponse:
        directory_path = _string_query(request, "directory_path")
        direct_only = _bool_query(request, "direct_only", default=True)
        include_inactive = _bool_query(request, "include_inactive", default=True)
        summaries = await run_in_threadpool(
            service.vm_agent_admin.list_vms,
            include_inactive=include_inactive,
            directory_path=directory_path,
            direct_only=direct_only,
        )
        return JSONResponse(
            {
                "items": [_vm_payload(summary) for summary in summaries],
                "directory_path": directory_path or "root",
                "direct_only": direct_only,
            }
        )

    async def get_vm(request: Request) -> JSONResponse:
        vm_name = request.path_params["vm_name"]
        try:
            summary = await run_in_threadpool(
                _find_vm_summary,
                service,
                vm_name,
                directory_path=_string_query(request, "directory_path"),
                direct_only=_bool_query(request, "direct_only", default=False),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        return JSONResponse(_vm_payload(summary))

    async def get_vm_agent_log(request: Request) -> JSONResponse:
        vm_name = request.path_params["vm_name"]
        lines_text = _string_query(request, "lines", default="200") or "200"
        try:
            lines = max(1, int(lines_text))
            text = await run_in_threadpool(
                service.get_agent_log_snapshot,
                vm_name,
                lines=lines,
                repo_name=_string_query(request, "repo_name"),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"vm_name": vm_name, "lines": lines, "text": text})

    async def mutate_vm(request: Request) -> JSONResponse:
        vm_name = request.path_params["vm_name"]
        action = request.path_params["action"]
        directory_path = _string_query(request, "directory_path")
        direct_only = _bool_query(request, "direct_only", default=False)
        try:
            payload = await _json_body(request)

            def _mutate_vm() -> VmActionResult:
                summary = _find_vm_summary(
                    service,
                    vm_name,
                    directory_path=directory_path,
                    direct_only=direct_only,
                )
                if action == "start":
                    validation_error = _validate_vm_model_configuration(
                        service,
                        summary,
                        directory_path=directory_path,
                        direct_only=direct_only,
                    )
                    if validation_error:
                        raise ValueError(validation_error)
                    return service.start_vm(vm_name)
                if action == "shutdown":
                    return service.shutdown_vm(vm_name)
                if action == "enable-agent":
                    return service.vm_agent_admin.enable_agent(vm_name)
                if action == "disable-agent":
                    return service.vm_agent_admin.disable_agent(vm_name)
                if action == "kill-agent":
                    pid = payload.get("pid")
                    if pid in {None, ""}:
                        pid = summary.agent_pid
                    if pid in {None, ""}:
                        raise ValueError("kill-agent requires a running agent pid")
                    return service.vm_agent_admin.kill_agent(vm_name, int(pid))
                if action == "resume-agent":
                    prompt = str(payload.get("prompt") or "").strip()
                    if not prompt:
                        raise ValueError("resume-agent requires a non-empty prompt")
                    validation_error = _validate_vm_model_configuration(
                        service,
                        summary,
                        directory_path=directory_path,
                        direct_only=direct_only,
                    )
                    if validation_error:
                        raise ValueError(validation_error)
                    return service.vm_agent_admin.resume_agent(
                        vm_name,
                        prompt,
                        model=str(payload.get("model") or "").strip() or None,
                        resume_mode=str(payload.get("resume_mode") or "same_context").strip()
                        or "same_context",
                    )
                if action == "destroy":
                    return service.destroy_vm(vm_name)
                raise LookupError(f"Unknown VM action: {action}")

            result = await run_in_threadpool(_mutate_vm)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except LookupError as exc:
            return json_error(str(exc), status_code=404)
        status_code = 200 if result.success else 409
        return JSONResponse(_action_payload(result), status_code=status_code)

    async def create_vm(request: Request) -> JSONResponse:
        try:
            payload = await _json_body(request)
            vm_name = str(payload.get("vm_name") or "").strip()
            if not vm_name:
                raise ValueError("VM name is required.")
            result = await run_in_threadpool(
                service.vm_agent_admin.create_vm,
                vm_name=vm_name,
                session_id=str(payload.get("session_id") or "").strip() or None,
                model=str(payload.get("model") or "").strip() or None,
                dotnet=bool(payload.get("dotnet")),
                nuget=bool(payload.get("nuget")),
                mssql=bool(payload.get("mssql")),
                mssql_init_root=str(payload.get("mssql_init_root") or "").strip() or None,
                playwright=bool(payload.get("playwright")),
                directory_path=str(payload.get("directory_path") or "root").strip() or "root",
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        status_code = 201 if result.success else 409
        return JSONResponse(_action_payload(result), status_code=status_code)

    async def list_issues(request: Request) -> JSONResponse:
        directory_path = _string_query(request, "directory_path")
        direct_only = _bool_query(request, "direct_only", default=True)
        try:
            states, exclude_states = _selected_issue_state_filters(
                _string_query(request, "state_filter", default="active")
            )
            issues = await run_in_threadpool(
                service.issue_git_admin.list_local_issues,
                directory_path=directory_path,
                direct_only=direct_only,
                states=states,
                exclude_states=exclude_states,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(
            {
                "items": [_issue_record_payload(issue) for issue in issues],
                "directory_path": directory_path or "root",
                "direct_only": direct_only,
            }
        )

    async def get_issue(request: Request) -> JSONResponse:
        issue_id = request.path_params["issue_id"]
        try:
            local_issue = _resolve_local_issue_record(service, issue_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        return JSONResponse(_local_issue_detail_payload(service, local_issue))

    async def create_issue(request: Request) -> JSONResponse:
        try:
            payload = await _json_body(request)

            def _create_issue() -> tuple[LocalIssueRecord, VmActionResult | None]:
                issue = service.issue_git_admin.create_local_issue(
                    title=str(payload.get("title") or ""),
                    body=str(payload.get("body") or ""),
                    repo_source=str(payload.get("repo_source") or "").strip() or None,
                    yaml_path=str(payload.get("yaml_path") or "").strip() or None,
                    directory_path=str(payload.get("directory_path") or "root").strip() or "root",
                )
                if not bool(payload.get("start_vm")):
                    return issue, None
                vm_result = service.vm_agent_admin.create_issue_vm_from_local_issue(
                    issue.issue_id,
                    model=str(payload.get("model") or "").strip() or None,
                    dotnet=bool(payload.get("dotnet")),
                    nuget=str(payload.get("nuget") or "").strip() or None,
                    mssql=bool(payload.get("mssql")),
                    mssql_init_root=str(payload.get("mssql_init_root") or "").strip() or None,
                    playwright=bool(payload.get("playwright")),
                )
                return issue, vm_result

            issue, vm_result = await run_in_threadpool(_create_issue)
            response_payload: dict[str, Any] = {"issue": _issue_record_payload(issue)}
            if vm_result is not None:
                response_payload["vm_action"] = _action_payload(vm_result)
            return JSONResponse(response_payload, status_code=201)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)

    async def update_issue(request: Request) -> JSONResponse:
        issue_id = request.path_params["issue_id"]
        try:
            payload = await _json_body(request)
            issue = service.issue_git_admin.update_local_issue(
                issue_id,
                title=str(payload.get("title") or ""),
                body=str(payload.get("body") or ""),
                state=str(payload.get("state") or "New"),
                repo_source=str(payload.get("repo_source") or "").strip() or None,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(_issue_record_payload(issue))

    async def delete_issue(request: Request) -> JSONResponse:
        issue_id = request.path_params["issue_id"]
        result = service.issue_git_admin.delete_local_issue(issue_id)
        status_code = 200 if result.success else 409
        return JSONResponse(_action_payload(result), status_code=status_code)

    async def get_issue_yaml(request: Request) -> JSONResponse:
        issue_id = request.path_params["issue_id"]
        try:
            issue = _resolve_local_issue_record(service, issue_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        yaml_value = str(issue.yaml_path or "").strip()
        if not yaml_value:
            return json_error(
                f"No YAML file recorded for issue {issue.issue_number}", status_code=404
            )
        yaml_path = Path(yaml_value)
        if not yaml_path.exists():
            return json_error(
                f"YAML file not found for issue {issue.issue_number}", status_code=404
            )
        return JSONResponse(
            {
                "issue_id": issue.issue_id,
                "issue_number": issue.issue_number,
                "path": str(yaml_path),
                "yaml_text": yaml_path.read_text(encoding="utf-8"),
            }
        )

    async def create_vm_from_issue(request: Request) -> JSONResponse:
        issue_id = request.path_params["issue_id"]
        try:
            payload = await _json_body(request)
            result = await run_in_threadpool(
                service.vm_agent_admin.create_issue_vm_from_local_issue,
                issue_id,
                model=str(payload.get("model") or "").strip() or None,
                dotnet=bool(payload.get("dotnet")),
                nuget=str(payload.get("nuget") or "").strip() or None,
                mssql=bool(payload.get("mssql")),
                mssql_init_root=str(payload.get("mssql_init_root") or "").strip() or None,
                playwright=bool(payload.get("playwright")),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        status_code = 200 if result.success else 409
        return JSONResponse(_action_payload(result), status_code=status_code)

    return [
        Route("/api/admin/vms", list_vms, methods=["GET"]),
        Route("/api/admin/vms", create_vm, methods=["POST"]),
        Route("/api/admin/vms/{vm_name:str}", get_vm, methods=["GET"]),
        Route("/api/admin/vms/{vm_name:str}/agent-log", get_vm_agent_log, methods=["GET"]),
        Route("/api/admin/vms/{vm_name:str}/actions/{action:str}", mutate_vm, methods=["POST"]),
        Route("/api/admin/issues", list_issues, methods=["GET"]),
        Route("/api/admin/issues", create_issue, methods=["POST"]),
        Route("/api/admin/issues/{issue_id:path}/yaml", get_issue_yaml, methods=["GET"]),
        Route(
            "/api/admin/issues/{issue_id:path}/create-vm", create_vm_from_issue, methods=["POST"]
        ),
        Route("/api/admin/issues/{issue_id:path}", get_issue, methods=["GET"]),
        Route("/api/admin/issues/{issue_id:path}", update_issue, methods=["PUT"]),
        Route("/api/admin/issues/{issue_id:path}", delete_issue, methods=["DELETE"]),
    ]


__all__ = ["vm_issue_routes"]
