from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
import time

from evander.gateway.request_context import _first_string_in_payload
from evander.models import McpToolName, ToolName, tool_name_value


@dataclass(slots=True)
class _ToolCallAuditContext:
    tool_name: str
    started_at: float
    raw_args: dict[str, Any]
    session_id: str | None = None
    argument_session_id: str | None = None
    ambient_session_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    transport_session_id: str | None = None
    mcp_session_token: str | None = None
    workflow_run_id: str | None = None
    mcp_tool_approval_lease_id: str | None = None
    audit_emitted: bool = False


_TOOL_CALL_AUDIT_CONTEXT: ContextVar[_ToolCallAuditContext | None] = ContextVar(
    "evander_tool_call_audit_context",
    default=None,
)

_TOOL_CALL_APPROVAL_NAMES = {
    ToolName.APPROVAL_SUBMIT.value,
    ToolName.APPROVAL_CHECK.value,
    ToolName.EMAIL_SEND_APPROVED.value,
    ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS.value,
    ToolName.DISCORD_POST_FILE.value,
    ToolName.TEAMS_POST_FILE.value,
}


def _tool_call_context() -> _ToolCallAuditContext | None:
    return _TOOL_CALL_AUDIT_CONTEXT.get()


def _mark_tool_call_audited() -> None:
    current = _tool_call_context()
    if current is not None:
        current.audit_emitted = True


def _tool_call_arguments_dict(arguments: Any) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return dict(arguments)
    if hasattr(arguments, "model_dump"):
        dumped = arguments.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    try:
        return dict(arguments)
    except Exception:
        return {"value": arguments}


def _tool_call_duration_ms(current: _ToolCallAuditContext | None) -> int | None:
    if current is None:
        return None
    elapsed_ms = int((time.perf_counter() - current.started_at) * 1000)
    return max(elapsed_ms, 0)


def _tool_call_audit_fields(
    *,
    session: Any | None,
    tool_name: McpToolName | None,
    args: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    current = _tool_call_context()
    metadata = session.metadata if isinstance(getattr(session, "metadata", None), dict) else {}
    approval_request_id = _first_string_in_payload(args, ("approval_request_id",))
    if approval_request_id is None:
        approval_request_id = _first_string_in_payload(result, ("approval_request_id",))
    if approval_request_id is None and current is not None:
        approval_request_id = _first_string_in_payload(
            current.raw_args,
            ("approval_request_id",),
        )
    current_tool_name = tool_name_value(tool_name) if tool_name is not None else ""
    if approval_request_id is None and current_tool_name in _TOOL_CALL_APPROVAL_NAMES:
        approval_request_id = _first_string_in_payload(args, ("request_id",))
        if approval_request_id is None:
            approval_request_id = _first_string_in_payload(result, ("request_id",))
    mcp_tool_approval_lease_id = _first_string_in_payload(
        args,
        ("mcp_tool_approval_lease_id", "tool_approval_lease_id"),
    )
    if mcp_tool_approval_lease_id is None:
        mcp_tool_approval_lease_id = _first_string_in_payload(
            result,
            ("mcp_tool_approval_lease_id", "tool_approval_lease_id"),
        )
    if mcp_tool_approval_lease_id is None and current is not None:
        mcp_tool_approval_lease_id = current.mcp_tool_approval_lease_id
    request_id = (
        current.request_id
        if current is not None and current.request_id
        else _first_string_in_payload(args, ("request_id",))
    )
    if request_id is None:
        request_id = _first_string_in_payload(result, ("request_id",))
    correlation_id = (
        current.correlation_id
        if current is not None and current.correlation_id
        else _first_string_in_payload(args, ("correlation_id",))
    )
    if correlation_id is None:
        correlation_id = _first_string_in_payload(result, ("correlation_id",))
    workflow_run_id = _first_string_in_payload(args, ("workflow_run_id",))
    if workflow_run_id is None:
        workflow_run_id = _first_string_in_payload(result, ("workflow_run_id",))
    if workflow_run_id is None and current is not None:
        workflow_run_id = current.workflow_run_id
    if workflow_run_id is None:
        workflow_run_id = str(metadata.get("workflow_run_id") or "").strip() or None
    trace_id = _first_string_in_payload(args, ("workflow_trace_id", "trace_id"))
    if trace_id is None:
        trace_id = _first_string_in_payload(result, ("workflow_trace_id", "trace_id"))
    if trace_id is None:
        trace_id = (
            str(metadata.get("workflow_trace_id") or metadata.get("trace_id") or "").strip()
            or None
        )
    return {
        "request_id": request_id,
        "correlation_id": correlation_id,
        "approval_request_id": approval_request_id,
        "mcp_tool_approval_lease_id": mcp_tool_approval_lease_id,
        "workflow_run_id": workflow_run_id,
        "trace_id": trace_id,
        "duration_ms": _tool_call_duration_ms(current),
    }


def _redact_nested_value(redacted_keys: set[str], value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested_value in value.items():
            if key in redacted_keys:
                redacted[key] = "<redacted>"
                continue
            redacted[key] = _redact_nested_value(redacted_keys, nested_value)
        return redacted

    if isinstance(value, list):
        return [_redact_nested_value(redacted_keys, item) for item in value]

    return value


def _redact_args(redacted_keys: set[str], args: dict[str, Any]) -> dict[str, Any]:
    return _redact_nested_value(redacted_keys, args)
