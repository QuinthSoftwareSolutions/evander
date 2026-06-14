from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.service import (
    COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
    AdminService,
)
from agent_sandbox.admin.web.cache import AdminWebJsonResponseCache
from agent_sandbox.admin.web.shared import (
    AdminWebAuth,
    json_error,
    request_has_privileged_operations,
)
from agent_sandbox.models import (
    ApprovalGrantSettings,
    ApprovalPayloadMode,
    ApprovalRequest,
    ApprovalStatus,
    McpToolPolicyRule,
    SessionSummary,
    ToolName,
    normalize_mcp_tool_policies,
)


_TOOL_DESCRIPTIONS: dict[ToolName, str] = {
    ToolName.MSSQL_PROVISION_DATABASE: (
        "Provision a session-owned SQL Server database through the container worker."
    ),
    ToolName.MSSQL_LIST_DATABASES: "List SQL Server databases owned by this session.",
    ToolName.MSSQL_DROP_DATABASE: "Drop a SQL Server database owned by this session.",
    ToolName.POST_ADO_BACKLOG_COMMENT: (
        "Post comments to Azure DevOps backlog items, including staged image artifacts when enabled."
    ),
    ToolName.DELETE_ADO_BACKLOG_ITEM: (
        "Delete Azure DevOps backlog items; soft delete by default and permanent only with destroy=true."
    ),
    ToolName.UPDATE_ADO_BACKLOG_TEXT: (
        "Update Azure DevOps backlog description or acceptance criteria text."
    ),
    ToolName.UPDATE_ADO_BACKLOG_TAGS: "Add or remove Azure DevOps backlog item tags.",
    ToolName.GET_ADO_DAILY_STATUS_REPORT_DATA: (
        "Build the daily status report JSON from Azure DevOps current-iteration data."
    ),
    ToolName.LIST_CONTACTS: (
        "List directory-configured contact ids without exposing addresses or phone numbers."
    ),
    ToolName.MAKE_PHONE_CALL: (
        "Start host-side phone calls to directory-configured contact ids."
    ),
    ToolName.GET_PHONE_CALL_RESULT: (
        "Read phone call status and transcripts for calls owned by the session."
    ),
    ToolName.SUBMIT_VOICE_QUERY_RESPONSE: (
        "Answer delegated live voice questions for phone calls projected from this session."
    ),
}


_SESSION_POLICY_FIELD_KINDS: dict[str, str] = {
    "web_search_enabled": "bool",
    "communication_file_receive_enabled": "bool",
    "communication_file_send_enabled": "bool",
    "communication_file_max_bytes": "int_or_null",
    "communication_file_allowed_mime_types": "string_list",
    "communication_file_allowed_extensions": "string_list",
    "communication_file_blocked_extensions": "string_list",
    "communication_file_require_approval_for_send": "bool",
    "tool_argument_policies": "json",
}

_SESSION_POLICY_FIELD_LABELS: dict[str, str] = {
    field_name: field_name.removeprefix("communication_file_").replace("_", " ")
    for field_name in COMMUNICATION_FILE_SESSION_POLICY_FIELDS
}
_SESSION_POLICY_FIELD_LABELS["web_search_enabled"] = "web search"
_SESSION_POLICY_FIELD_LABELS["tool_argument_policies"] = "tool argument policy"


def _session_policy_field_catalog() -> list[dict[str, str]]:
    catalog = [
        {
            "field_name": "web_search_enabled",
            "label": _SESSION_POLICY_FIELD_LABELS["web_search_enabled"],
            "kind": _SESSION_POLICY_FIELD_KINDS["web_search_enabled"],
            "section": "Hosted tools",
        },
    ]
    catalog.extend(
        [
            {
                "field_name": field_name,
                "label": _SESSION_POLICY_FIELD_LABELS[field_name],
                "kind": _SESSION_POLICY_FIELD_KINDS[field_name],
                "section": "Communication files",
            }
            for field_name in COMMUNICATION_FILE_SESSION_POLICY_FIELDS
        ]
    )
    catalog.append(
        {
            "field_name": "tool_argument_policies",
            "label": _SESSION_POLICY_FIELD_LABELS["tool_argument_policies"],
            "kind": _SESSION_POLICY_FIELD_KINDS["tool_argument_policies"],
            "section": "Tool argument policy",
        }
    )
    return catalog


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
    if value is None or not value.strip():
        return None
    return int(value)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("Datetime values must be ISO-8601 strings.")
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid datetime value: {value}") from exc


def _parse_statuses(value: str | None) -> set[ApprovalStatus] | None:
    if value is None or not value.strip():
        return None
    statuses: set[ApprovalStatus] = set()
    for part in value.split(","):
        normalized = part.strip().lower()
        if not normalized:
            continue
        statuses.add(ApprovalStatus(normalized))
    return statuses or None


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_iso(item) for item in value]
    if isinstance(value, dict):
        return {key: _iso(item) for key, item in value.items()}
    return value


def _approval_payload(request: ApprovalRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    payload["tool_name"] = request.tool_name or request.action_type
    return payload


def _approval_review_payload(review: dict[str, Any]) -> dict[str, Any]:
    payload = _iso(review)
    request_payload = payload.get("request")
    if isinstance(request_payload, dict):
        normalized_request = dict(request_payload)
        normalized_request["tool_name"] = normalized_request.get(
            "tool_name"
        ) or normalized_request.get("action_type")
        if isinstance(payload.get("redacted_payload"), dict):
            normalized_request["payload"] = payload["redacted_payload"]
        payload["request"] = normalized_request
    return payload


def _json_revision(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _rule_effective_settings(rule: McpToolPolicyRule | None) -> ApprovalGrantSettings:
    settings = ApprovalGrantSettings()
    if rule is not None and rule.default_settings is not None:
        settings = rule.default_settings
    if rule is not None:
        ttl_seconds = settings.ttl_seconds
        if rule.max_ttl_seconds is not None:
            ttl_seconds = min(ttl_seconds, rule.max_ttl_seconds)
        max_uses = settings.max_uses
        if rule.max_uses is not None:
            max_uses = rule.max_uses if max_uses is None else min(max_uses, rule.max_uses)
        payload_mode = settings.payload_mode
        if rule.require_exact_payload:
            payload_mode = ApprovalPayloadMode.EXACT
        settings = ApprovalGrantSettings(
            ttl_seconds=ttl_seconds,
            max_uses=max_uses,
            payload_mode=payload_mode,
        )
    return settings


def _tool_option(tool: ToolName) -> dict[str, Any]:
    return {
        "value": tool.value,
        "label": tool.value.replace("_", " "),
        "description": _TOOL_DESCRIPTIONS.get(tool, ""),
    }


def _approval_actor_from_request(
    auth: AdminWebAuth, request: Request, payload: dict[str, Any]
) -> str:
    if not auth.config.auth_required:
        return str(payload.get("approver") or "").strip()
    session = auth.require(request)
    return str(
        session.get("name")
        or session.get("login")
        or session.get("sub")
        or session.get("sid")
        or ""
    ).strip()


def _session_payload(service: AdminService, summary: SessionSummary) -> dict[str, Any]:
    payload = summary.model_dump(mode="json")
    payload["allowed_tools"] = [
        tool.value if isinstance(tool, ToolName) else str(tool) for tool in summary.allowed_tools
    ]
    payload["tool_summary"] = service.runtime.policy.allowed_tool_summary_for_session(summary)
    payload["approval_notification_target_options"] = [
        option.model_dump(mode="json")
        for option in service._approval_notification_target_options(summary)
    ]
    payload["effective_tool_policy"] = service.effective_tool_policy_payload(summary)
    payload["runtime_image"] = service.session_runtime_image_payload(summary)
    return payload


def _wake_schedule_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = _iso(item)
    schedule = payload.pop("schedule", {}) if isinstance(payload.get("schedule"), dict) else {}
    resume_prompt = (
        payload.pop("resume_prompt", {}) if isinstance(payload.get("resume_prompt"), dict) else {}
    )
    payload["cron"] = schedule.get("cron", payload.get("cron"))
    payload["resume_at"] = schedule.get("resume_at", payload.get("resume_at"))
    payload["prompt"] = resume_prompt.get("prompt", payload.get("prompt"))
    payload["metadata"] = resume_prompt.get("metadata", payload.get("metadata", {}))
    payload["schedule_kind"] = "cron" if payload.get("cron") else "resume_at"
    return payload


_WAKE_LEDGER_STATUSES = {
    "pending",
    "due",
    "dispatching",
    "queued",
    "dispatched",
    "failed",
    "missed_or_late",
    "completed_or_acknowledged",
    "cancelled",
}


def _parse_wake_statuses(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    statuses: list[str] = []
    seen: set[str] = set()
    for part in value.split(","):
        normalized = part.strip().lower()
        if not normalized:
            continue
        if normalized not in _WAKE_LEDGER_STATUSES:
            raise ValueError(f"Invalid wake status: {part.strip()}")
        if normalized in seen:
            continue
        seen.add(normalized)
        statuses.append(normalized)
    return statuses or None


def _wake_ledger_payload(item: dict[str, Any]) -> dict[str, Any]:
    return _iso(item)


def session_approval_routes(
    service: AdminService,
    auth: AdminWebAuth,
    cache: AdminWebJsonResponseCache | None = None,
) -> list[BaseRoute]:
    async def approvals_index(request: Request) -> JSONResponse:
        params = request.query_params
        try:
            statuses = _parse_statuses(params.get("statuses"))
            direct_only = _parse_bool(params.get("direct_only"))
            min_age_seconds = _parse_int(params.get("min_age_seconds"))
            items = service.list_approval_requests(
                statuses=statuses,
                directory_path=params.get("directory_path"),
                direct_only=direct_only,
                session_id=params.get("session_id"),
                vm_id=params.get("vm_id"),
                tool_name=params.get("tool_name"),
                min_age_seconds=min_age_seconds,
            )
            health = service.approval_queue_health(
                statuses=statuses,
                directory_path=params.get("directory_path"),
                direct_only=direct_only,
                session_id=params.get("session_id"),
                vm_id=params.get("vm_id"),
                tool_name=params.get("tool_name"),
                min_age_seconds=min_age_seconds,
            )
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(
            {
                "items": [_approval_payload(item) for item in items],
                "health": health.model_dump(mode="json"),
            }
        )

    async def approval_detail(_: Request) -> JSONResponse:
        raise AssertionError("unreachable")

    async def get_approval_detail(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        try:
            review = service.get_approval_review(request_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        return JSONResponse(_approval_review_payload(review))

    async def decide_approval(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        approver = _approval_actor_from_request(auth, request, payload)
        reason = str(payload.get("reason") or "").strip()
        approve_raw = payload.get("approve")
        if not approver:
            return json_error("Approver is required.")
        if not reason:
            return json_error("Reason is required.")
        if not isinstance(approve_raw, bool):
            return json_error("approve must be a boolean.")
        try:
            updated = service.decide_approval_request(
                request_id,
                approver=approver,
                approve=approve_raw,
                reason=reason,
            )
        except ValueError as exc:
            status_code = 404 if "Unknown approval request" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse({"request": _approval_payload(updated)})

    async def revoke_approval(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        approver = _approval_actor_from_request(auth, request, payload)
        reason = str(payload.get("reason") or "").strip()
        if not approver:
            return json_error("Approver is required.")
        if not reason:
            return json_error("Reason is required.")
        try:
            updated = service.revoke_approval_request(
                request_id,
                approver=approver,
                reason=reason,
            )
        except ValueError as exc:
            status_code = 404 if "Unknown approval request" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse({"request": _approval_payload(updated)})

    async def sessions_catalog(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "tool_options": [
                    _tool_option(tool)
                    for tool in ToolName
                ],
                "resume_mode_options": [
                    {"value": "same_context", "label": "same_context"},
                    {"value": "new_context", "label": "new_context"},
                ],
                "renew_extension_presets": ["1h", "6h", "12h", "1d", "7d"],
                "session_policy_fields": _session_policy_field_catalog(),
            }
        )

    async def list_sessions(request: Request) -> JSONResponse:
        params = request.query_params
        try:
            items = service.list_sessions(
                include_expired=_parse_bool(params.get("include_expired")),
                expired_only=_parse_bool(params.get("expired_only")),
                directory_path=params.get("directory_path"),
                direct_only=_parse_bool(params.get("direct_only")),
            )
        except ValueError as exc:
            return json_error(str(exc))
        active_items = [item for item in items if not item.is_expired]
        expired_items = [item for item in items if item.is_expired]
        return JSONResponse(
            {
                "items": [_session_payload(service, item) for item in items],
                "active_items": [_session_payload(service, item) for item in active_items],
                "expired_items": [_session_payload(service, item) for item in expired_items],
                "counts": {
                    "total": len(items),
                    "active": len(active_items),
                    "expired": len(expired_items),
                },
            }
        )

    def _get_session_summary(session_id: str) -> SessionSummary:
        for item in service.list_sessions(include_expired=True):
            if item.session_id == session_id:
                return item
        raise ValueError(f"Unknown session: {session_id}")

    async def get_session_detail(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            summary = _get_session_summary(session_id)
            wake_schedules = service.list_wake_schedules(session_id=session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        return JSONResponse(
            {
                "session": _session_payload(service, summary),
                "wake_schedules": [_wake_schedule_payload(item) for item in wake_schedules],
                "summary_json": _iso(summary.model_dump(mode="json")),
            }
        )

    async def renew_session(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            result = service.renew_session(
                session_id, extension=str(payload.get("extension") or "1d")
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(result.model_dump(mode="json"))

    async def revoke_session(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            result = service.revoke_session(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(result.model_dump(mode="json"))

    async def delete_session(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            result = service.delete_session(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(result.model_dump(mode="json"))

    async def cleanup_session(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            result = service.cleanup_container_session(session_id)
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        payload = {
            **result.model_dump(mode="json"),
            "session": _session_payload(service, summary),
        }
        if not result.success:
            payload["error"] = result.message
        status_code = 200 if result.success else 502
        return JSONResponse(payload, status_code=status_code)

    async def update_session_model(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        model = str(payload.get("model") or "").strip()
        if not model:
            return json_error("Model is required.")
        try:
            result = service.set_session_model(session_id, model)
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(
            {
                **result.model_dump(mode="json"),
                "session": _session_payload(service, summary),
            }
        )

    async def update_session_tools(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        raw_tools = payload.get("allowed_tools", [])
        if not isinstance(raw_tools, list):
            return json_error("allowed_tools must be a list.")
        try:
            result = service.set_session_allowed_tools(
                session_id, [str(item) for item in raw_tools]
            )
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(
            {
                **result.model_dump(mode="json"),
                "session": _session_payload(service, summary),
            }
        )

    async def update_session_policy(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            result = service.update_session_policy(session_id, payload)
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(
            {
                **result.model_dump(mode="json"),
                "session": _session_payload(service, summary),
            }
        )

    def _mcp_tools_payload(directory_path: str) -> dict[str, Any]:
        raw_result = service.read_directory_domain_config(directory_path, "policy")
        raw_config = (
            raw_result.data.config
            if raw_result.ok and raw_result.data is not None and raw_result.data.config is not None
            else {}
        )
        raw_policies = raw_config.get("mcp_tool_policies")
        if not isinstance(raw_policies, dict):
            raw_policies = {}
        effective_policy = service.runtime.policy_config.resolve_for_directory(directory_path)
        items: list[dict[str, Any]] = []
        for tool in sorted(ToolName, key=lambda item: item.value):
            effective_rule = effective_policy.mcp_tool_policies.get(tool.value)
            effective_settings = _rule_effective_settings(effective_rule)
            items.append(
                {
                    **_tool_option(tool),
                    "local_policy": raw_policies.get(tool.value),
                    "effective_policy": effective_rule.model_dump(mode="json")
                    if effective_rule is not None
                    else None,
                    "effective_settings": effective_settings.model_dump(mode="json"),
                    "approval_required": (
                        tool in set(effective_policy.approval_required_tools)
                        or bool(effective_rule and effective_rule.require_approval)
                    ),
                    "denied": bool(effective_rule and effective_rule.denied),
                }
            )
        return {
            "directory_path": directory_path,
            "revision": _json_revision({"directory_path": directory_path, "policy": raw_config}),
            "mcp_tool_policies": raw_policies,
            "items": items,
        }

    async def mcp_tools_index(request: Request) -> JSONResponse:
        directory_path = str(request.query_params.get("directory_path") or "root").strip() or "root"
        try:
            payload = _mcp_tools_payload(directory_path)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(payload)

    async def update_mcp_tools(request: Request) -> JSONResponse:
        directory_path = str(request.query_params.get("directory_path") or "root").strip() or "root"
        if not request_has_privileged_operations(auth=auth, service=service, request=request):
            return json_error("Privileged operations are locked.", status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        expected_revision = str(payload.get("expected_revision") or "").strip()
        if not expected_revision:
            return json_error("expected_revision is required.", status_code=428)
        try:
            current = _mcp_tools_payload(directory_path)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        if current["revision"] != expected_revision:
            return json_error(
                "MCP tool policy changed before save. Reload before saving again.",
                status_code=409,
            )
        try:
            normalized_policies = normalize_mcp_tool_policies(
                payload.get("mcp_tool_policies"),
                field_name="mcp_tool_policies",
            )
            validated_policies = {
                tool_name: McpToolPolicyRule.model_validate(policy).model_dump(mode="json")
                for tool_name, policy in normalized_policies.items()
            }
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        raw_result = service.read_directory_domain_config(directory_path, "policy")
        raw_config = (
            dict(raw_result.data.config)
            if raw_result.ok and raw_result.data is not None and raw_result.data.config is not None
            else {}
        )
        raw_config["mcp_tool_policies"] = validated_policies
        result = service.write_directory_domain_config(directory_path, "policy", raw_config)
        if not result.ok:
            message = result.errors[0].message if result.errors else "Unable to save MCP tool policy."
            return json_error(message, status_code=400)
        return JSONResponse(_mcp_tools_payload(directory_path))

    async def list_wake_schedules(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            items = service.list_wake_schedules(
                session_id=session_id,
                include_disabled=_parse_bool(
                    request.query_params.get("include_disabled"), default=True
                ),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"items": [_wake_schedule_payload(item) for item in items]})

    async def list_scheduled_wakes(request: Request) -> JSONResponse:
        params = request.query_params
        limit = _parse_int(params.get("limit"))
        try:
            items = service.list_scheduled_wake_ledger(
                statuses=_parse_wake_statuses(params.get("statuses")),
                session_id=params.get("session_id"),
                limit=100 if limit is None else limit,
                include_completed=_parse_bool(
                    params.get("include_completed"), default=True
                ),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"items": [_wake_ledger_payload(item) for item in items]})

    async def create_wake_schedule(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            created = service.create_wake_schedule(
                session_id=session_id,
                prompt=str(payload.get("prompt") or ""),
                resume_at=_parse_datetime(payload.get("resume_at")),
                cron=str(payload.get("cron")).strip() if payload.get("cron") is not None else None,
                resume_mode=str(payload.get("resume_mode") or "same_context"),
                enabled=bool(payload.get("enabled", True)),
                source="admin_web",
            )
        except ValueError as exc:
            status_code = 404 if "Unknown" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse({"item": _wake_schedule_payload(created.model_dump(mode="json"))})

    async def update_wake_schedule(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            updated = service.edit_wake_schedule(
                request_id,
                resume_at=_parse_datetime(payload.get("resume_at"))
                if "resume_at" in payload
                else None,
                cron=str(payload.get("cron")).strip()
                if "cron" in payload and payload.get("cron") is not None
                else payload.get("cron"),
                prompt=str(payload.get("prompt")).strip()
                if "prompt" in payload and payload.get("prompt") is not None
                else None,
                resume_mode=str(payload.get("resume_mode"))
                if "resume_mode" in payload and payload.get("resume_mode") is not None
                else None,
                enabled=bool(payload.get("enabled")) if "enabled" in payload else None,
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse({"item": _wake_schedule_payload(updated.model_dump(mode="json"))})

    async def retry_scheduled_wake(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            resume_at = _parse_datetime(payload.get("resume_at")) if "resume_at" in payload else None
            if "run_now" in payload:
                run_now_raw = payload.get("run_now")
                if not isinstance(run_now_raw, bool):
                    return json_error("run_now must be a boolean.")
                run_now = bool(run_now_raw)
                if resume_at is not None and run_now:
                    return json_error("Provide either resume_at or run_now=true, not both.")
            else:
                run_now = resume_at is None
            if resume_at is None and not run_now:
                return json_error("Either resume_at or run_now=true is required.")
            updated = service.retry_scheduled_wake(
                request_id,
                resume_at=resume_at,
                run_now=run_now,
            )
        except ValueError as exc:
            message = str(exc)
            if "not retryable" in message.lower():
                return json_error(message, status_code=409)
            status_code = 404 if "not found" in message.lower() else 400
            return json_error(message, status_code=status_code)
        return JSONResponse({"item": _wake_ledger_payload(updated)})

    async def remove_wake_schedule(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        try:
            deleted = service.delete_wake_schedule(request_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "not found" in str(exc).lower() else 400)
        if not deleted:
            return json_error(f"Wake schedule not found: {request_id}", status_code=404)
        return JSONResponse({"ok": True, "request_id": request_id})

    return [
        Route(
            "/api/admin/approvals",
            cache.wrap("admin.approvals.list", approvals_index) if cache else approvals_index,
            methods=["GET"],
        ),
        Route("/api/admin/approvals/{request_id:str}", get_approval_detail, methods=["GET"]),
        Route("/api/admin/approvals/{request_id:str}/decision", decide_approval, methods=["POST"]),
        Route("/api/admin/approvals/{request_id:str}/revoke", revoke_approval, methods=["POST"]),
        Route("/api/admin/mcp-tools", mcp_tools_index, methods=["GET"]),
        Route("/api/admin/mcp-tools", update_mcp_tools, methods=["PUT"]),
        Route("/api/admin/sessions/catalog", sessions_catalog, methods=["GET"]),
        Route(
            "/api/admin/sessions",
            cache.wrap("admin.sessions.list", list_sessions) if cache else list_sessions,
            methods=["GET"],
        ),
        Route("/api/admin/sessions/{session_id:str}", get_session_detail, methods=["GET"]),
        Route("/api/admin/sessions/{session_id:str}", update_session_policy, methods=["PATCH"]),
        Route("/api/admin/sessions/{session_id:str}/renew", renew_session, methods=["POST"]),
        Route("/api/admin/sessions/{session_id:str}/revoke", revoke_session, methods=["POST"]),
        Route("/api/admin/sessions/{session_id:str}/cleanup", cleanup_session, methods=["POST"]),
        Route("/api/admin/sessions/{session_id:str}", delete_session, methods=["DELETE"]),
        Route("/api/admin/sessions/{session_id:str}/model", update_session_model, methods=["PUT"]),
        Route(
            "/api/admin/sessions/{session_id:str}/allowed-tools",
            update_session_tools,
            methods=["PUT"],
        ),
        Route(
            "/api/admin/sessions/{session_id:str}/wake-schedules",
            list_wake_schedules,
            methods=["GET"],
        ),
        Route(
            "/api/admin/scheduled-wakes",
            list_scheduled_wakes,
            methods=["GET"],
        ),
        Route(
            "/api/admin/sessions/{session_id:str}/wake-schedules",
            create_wake_schedule,
            methods=["POST"],
        ),
        Route(
            "/api/admin/sessions/wake-schedules/{request_id:str}",
            update_wake_schedule,
            methods=["PATCH"],
        ),
        Route(
            "/api/admin/scheduled-wakes/{request_id:str}/retry",
            retry_scheduled_wake,
            methods=["POST"],
        ),
        Route(
            "/api/admin/sessions/wake-schedules/{request_id:str}",
            remove_wake_schedule,
            methods=["DELETE"],
        ),
    ]


__all__ = ["session_approval_routes"]
