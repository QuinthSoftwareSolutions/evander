from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.addons import AddonContext
from agent_sandbox.models import digest_payload

from .catalog import catalog_payload
from .client import SkatteverketClient
from .repository import (
    SkatteverketRepository,
    SkatteverketStateConflictError,
    SkatteverketStateError,
)

LIST_CATALOG_TOOL = "skatteverket_list_catalog"
CONNECTION_STATUS_TOOL = "skatteverket_connection_status"
GET_TAX_ACCOUNT_TOOL = "skatteverket_get_tax_account"
CREATE_DRAFT_TOOL = "skatteverket_create_draft"
VALIDATE_DRAFT_TOOL = "skatteverket_validate_draft"
REQUEST_SUBMISSION_APPROVAL_TOOL = "skatteverket_request_submission_approval"
SUBMIT_APPROVED_DRAFT_TOOL = "skatteverket_submit_approved_draft"

MCP_TOOL_NAMES = (
    LIST_CATALOG_TOOL,
    CONNECTION_STATUS_TOOL,
    GET_TAX_ACCOUNT_TOOL,
    CREATE_DRAFT_TOOL,
    VALIDATE_DRAFT_TOOL,
    REQUEST_SUBMISSION_APPROVAL_TOOL,
    SUBMIT_APPROVED_DRAFT_TOOL,
)


def register_mcp_tools(server: FastMCP, context: AddonContext) -> tuple[str, ...]:
    repo = SkatteverketRepository(context.runtime_dir)

    def _runtime() -> Any:
        return context.service

    def _require_tool(session_id: str | None, tool_name: str) -> str:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ToolError("session_id is required")
        runtime = _runtime()
        if runtime is None or not hasattr(runtime, "policy"):
            raise ToolError("Skatteverket MCP tools require a runtime policy context")
        runtime.policy.require_tool(normalized_session_id, tool_name)
        return normalized_session_id

    def _submission_approval_payload(draft_id: str) -> dict[str, Any]:
        return repo.get_draft(draft_id).approval_payload()

    @server.tool()
    def skatteverket_list_catalog(session_id: str | None = None) -> dict[str, Any]:
        _require_tool(session_id, LIST_CATALOG_TOOL)
        return catalog_payload()

    @server.tool()
    def skatteverket_connection_status(session_id: str | None = None) -> dict[str, Any]:
        _require_tool(session_id, CONNECTION_STATUS_TOOL)
        connection = repo.default_connection()
        return SkatteverketClient(connection).connection_status()

    @server.tool()
    def skatteverket_get_tax_account(
        session_id: str | None = None,
        subject_id: str | None = None,
        period: str | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, GET_TAX_ACCOUNT_TOOL)
        connection = repo.default_connection()
        return SkatteverketClient(connection).query_placeholder(
            "skattekonto",
            {"subject_id": subject_id, "period": period},
        )

    @server.tool()
    def skatteverket_create_draft(
        api_name: str,
        payload: dict[str, Any],
        session_id: str | None = None,
        period: str | None = None,
        subject_id: str | None = None,
    ) -> dict[str, Any]:
        source_session_id = _require_tool(session_id, CREATE_DRAFT_TOOL)
        try:
            return repo.create_draft(
                api_name=api_name,
                payload=payload,
                period=period,
                subject_id=subject_id,
                source_session_id=source_session_id,
            )
        except SkatteverketStateError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    def skatteverket_validate_draft(
        draft_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, VALIDATE_DRAFT_TOOL)
        try:
            return repo.validate_draft(draft_id)
        except SkatteverketStateError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool()
    def skatteverket_request_submission_approval(
        draft_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, REQUEST_SUBMISSION_APPROVAL_TOOL)
        runtime = _runtime()
        approval_payload = _submission_approval_payload(draft_id)
        draft = repo.get_draft(draft_id)
        if draft.status != "validated":
            raise ToolError(
                f"draft {draft_id} must be validated before approval; current status is {draft.status}"
            )
        approval = runtime.policy.create_approval(
            resolved_session_id,
            "skatteverket_submission",
            approval_payload,
            f"Submit Skatteverket draft {draft_id} for {draft.api_name}",
            tool_name=SUBMIT_APPROVED_DRAFT_TOOL,
            intended_effect=(
                f"Submit Skatteverket {draft.api_name} draft {draft_id} "
                f"at revision {draft.revision}."
            ),
        )
        return {
            "status": "pending_approval",
            "approval_request_id": approval.request_id,
            "payload_digest": digest_payload(approval_payload),
            "draft": repo.get_draft_payload(draft_id),
        }

    @server.tool()
    def skatteverket_submit_approved_draft(
        draft_id: str,
        submission_approval_request_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, SUBMIT_APPROVED_DRAFT_TOOL)
        normalized_approval_request_id = str(submission_approval_request_id or "").strip()
        if not normalized_approval_request_id:
            raise ToolError("submission_approval_request_id is required")
        runtime = _runtime()
        try:
            draft = repo.get_draft(draft_id)
            approval_payload = draft.approval_payload()
            approval = runtime.policy.require_approved(
                normalized_approval_request_id,
                approval_payload,
                consume=False,
            )
            if approval.session_id != resolved_session_id:
                raise ToolError(
                    f"approval_request_id {normalized_approval_request_id} belongs to "
                    f"session {approval.session_id}, not {resolved_session_id}"
                )
            if str(approval.tool_name or "").strip() != SUBMIT_APPROVED_DRAFT_TOOL:
                raise ToolError(
                    f"approval_request_id {normalized_approval_request_id} is not for "
                    f"{SUBMIT_APPROVED_DRAFT_TOOL}"
                )
            approval = runtime.policy.require_approved(
                normalized_approval_request_id,
                approval_payload,
                consume=True,
            )
            idempotency_key = f"{draft.draft_id}:{draft.revision}:{approval.request_id}"
            response = SkatteverketClient(repo.default_connection()).submit_draft(
                draft,
                idempotency_key=idempotency_key,
            )
            submission = repo.record_submission(
                draft_id=draft.draft_id,
                approval_request_id=approval.request_id,
                response=response,
                idempotency_key=idempotency_key,
            )
        except (SkatteverketStateConflictError, SkatteverketStateError) as exc:
            raise ToolError(str(exc)) from exc
        return {
            "status": submission["status"],
            "submission": submission,
            "draft": repo.get_draft_payload(draft_id),
        }

    return MCP_TOOL_NAMES
