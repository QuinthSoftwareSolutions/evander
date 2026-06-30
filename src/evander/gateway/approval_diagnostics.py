from __future__ import annotations

from typing import Any

from evander.approval_diagnostics import build_payload_diagnostic
from evander.models import digest_payload
from evander.policy import PolicyError


def _approval_gate_arguments(args: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(args)
    sanitized.pop("approval_request_id", None)
    return sanitized


def _approval_gate_payload(
    *,
    tool_name: str,
    session_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "session_id": session_id,
        "arguments": arguments,
    }


def _approval_gate_human_summary(tool_name: str, session_id: str) -> str:
    return f"Approve MCP tool {tool_name} for session {session_id}"


def _approval_gate_intended_effect(tool_name: str, session_id: str) -> str:
    return (
        f"Execute the exact approved MCP tool invocation {tool_name!r} "
        f"for session {session_id!r}."
    )


def _approval_gate_pending_diagnostic(
    *,
    approval_request_id: str,
    approval_payload: dict[str, Any],
    redacted_keys: set[str],
    status: str,
    reason: str,
) -> dict[str, Any]:
    diagnostic = build_payload_diagnostic(
        approved_payload=approval_payload,
        attempted_payload=approval_payload,
        redacted_keys=redacted_keys,
        status=status,
        reason=reason,
        approved_payload_digest=digest_payload(approval_payload),
        attempted_payload_digest=digest_payload(approval_payload),
    )
    diagnostic["approval_request_id"] = approval_request_id
    return diagnostic


def _approval_gate_blocked_diagnostic(
    *,
    approval_request_id: str,
    attempted_payload: dict[str, Any],
    error: PolicyError,
) -> dict[str, Any]:
    diagnostic = getattr(error, "approval_diagnostic", None)
    if isinstance(diagnostic, dict):
        result = dict(diagnostic)
    else:
        approval_request = getattr(error, "approval_request", None)
        status = None
        if approval_request is not None:
            approval_status = getattr(approval_request, "status", None)
            status = getattr(approval_status, "value", approval_status)
        result = {
            "approval_request_id": approval_request_id,
            "rejection_kind": getattr(error, "rejection_kind", None) or "blocked",
            "status": status,
            "reason": str(error),
            "attempted_payload_digest": digest_payload(attempted_payload),
        }
    result.setdefault("approval_request_id", approval_request_id)
    rejection_kind = getattr(error, "rejection_kind", None)
    if rejection_kind and not result.get("rejection_kind"):
        result["rejection_kind"] = rejection_kind
    if "reason" not in result:
        result["reason"] = str(error)
    if "attempted_payload_digest" not in result:
        result["attempted_payload_digest"] = digest_payload(attempted_payload)
    return result
