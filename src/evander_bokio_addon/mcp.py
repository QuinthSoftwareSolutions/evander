from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.policy import PolicyError

from .api import _execute_prepared_action
from .client import BokioClient
from .state import PreparedActionStore, upload_staging_dir


GET_COMPANY_TOOL = "bokio_get_company"
LIST_ACCOUNTS_TOOL = "bokio_list_accounts"
LIST_JOURNAL_ENTRIES_TOOL = "bokio_list_journal_entries"
LIST_INVOICES_TOOL = "bokio_list_invoices"
UPLOAD_DOCUMENT_TOOL = "bokio_upload_document"
PREPARE_JOURNAL_ENTRY_TOOL = "bokio_prepare_journal_entry"
CREATE_APPROVED_JOURNAL_ENTRY_TOOL = "bokio_create_approved_journal_entry"
PREPARE_SUPPLIER_INVOICE_TOOL = "bokio_prepare_supplier_invoice"
CREATE_APPROVED_SUPPLIER_INVOICE_TOOL = "bokio_create_approved_supplier_invoice"
PREPARE_BANK_PAYMENT_TOOL = "bokio_prepare_bank_payment"
CREATE_APPROVED_BANK_PAYMENT_TOOL = "bokio_create_approved_bank_payment"

READ_TOOLS = (
    GET_COMPANY_TOOL,
    LIST_ACCOUNTS_TOOL,
    LIST_JOURNAL_ENTRIES_TOOL,
    LIST_INVOICES_TOOL,
)
WRITE_TOOLS = (
    UPLOAD_DOCUMENT_TOOL,
    PREPARE_JOURNAL_ENTRY_TOOL,
    CREATE_APPROVED_JOURNAL_ENTRY_TOOL,
    PREPARE_SUPPLIER_INVOICE_TOOL,
    CREATE_APPROVED_SUPPLIER_INVOICE_TOOL,
    PREPARE_BANK_PAYMENT_TOOL,
    CREATE_APPROVED_BANK_PAYMENT_TOOL,
)
MCP_TOOL_NAMES = (*READ_TOOLS, *WRITE_TOOLS)


def register_mcp_tools(server: Any, context: AddonContext) -> tuple[str, ...]:
    def _runtime() -> Any:
        return context.service

    def _transport_session_map() -> dict[str, str] | None:
        value = getattr(server, "_evander_transport_session_map", None)
        return value if isinstance(value, dict) else None

    def _require_tool(
        session_id: str | None,
        ctx: Context | None,
        tool_name: str,
    ) -> str:
        resolved_session_id = _resolve_session_id_from_context(
            session_id,
            ctx,
            transport_session_map=_transport_session_map(),
        )
        runtime = _runtime()
        if runtime is None or not hasattr(runtime, "policy"):
            raise ToolError("Bokio MCP tools require a runtime policy context")
        try:
            runtime.policy.require_tool(resolved_session_id, tool_name)
        except PolicyError as exc:
            raise ToolError(str(exc)) from exc
        return resolved_session_id

    @server.tool(
        name=GET_COMPANY_TOOL,
        description="Read Bokio company information for the configured private integration company.",
    )
    def bokio_get_company(
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, GET_COMPANY_TOOL)
        return {"result": _client(context).get_company()}

    @server.tool(
        name=LIST_ACCOUNTS_TOOL,
        description="List Bokio chart-of-accounts records, optionally using a Bokio query filter.",
    )
    def bokio_list_accounts(
        query: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, LIST_ACCOUNTS_TOOL)
        return {"result": _client(context).list_accounts(query=query)}

    @server.tool(
        name=LIST_JOURNAL_ENTRIES_TOOL,
        description="List Bokio journal entries with optional pagination and query filter.",
    )
    def bokio_list_journal_entries(
        page: int = 1,
        page_size: int = 25,
        query: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, LIST_JOURNAL_ENTRIES_TOOL)
        return {
            "result": _client(context).list_journal_entries(
                page=page,
                page_size=page_size,
                query=query,
            )
        }

    @server.tool(
        name=LIST_INVOICES_TOOL,
        description="List Bokio invoices with optional pagination and query filter.",
    )
    def bokio_list_invoices(
        page: int = 1,
        page_size: int = 25,
        query: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, LIST_INVOICES_TOOL)
        return {"result": _client(context).list_invoices(page=page, page_size=page_size, query=query)}

    @server.tool(
        name=UPLOAD_DOCUMENT_TOOL,
        description="Upload a host-local PDF, JPEG, or PNG document to Bokio.",
    )
    def bokio_upload_document(
        file_path: str,
        description: str | None = None,
        journal_entry_id: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, ctx, UPLOAD_DOCUMENT_TOOL)
        upload_root = upload_staging_dir(
            context.runtime_dir,
            session_id=resolved_session_id,
        )
        upload_root.mkdir(parents=True, exist_ok=True)
        return {
            "result": _client(context).upload_document(
                file_path=file_path,
                allowed_roots=[upload_root],
                description=description,
                journal_entry_id=journal_entry_id,
            )
        }

    @server.tool(
        name=PREPARE_JOURNAL_ENTRY_TOOL,
        description="Create a local prepared Bokio journal entry action for human approval.",
    )
    def bokio_prepare_journal_entry(
        payload: dict[str, Any],
        note: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, ctx, PREPARE_JOURNAL_ENTRY_TOOL)
        return _prepare_action(
            context,
            kind="journal_entry",
            payload=payload,
            source=resolved_session_id,
            note=note,
        )

    @server.tool(
        name=CREATE_APPROVED_JOURNAL_ENTRY_TOOL,
        description="Create a Bokio journal entry from an approved local prepared action.",
    )
    def bokio_create_approved_journal_entry(
        prepared_action_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, CREATE_APPROVED_JOURNAL_ENTRY_TOOL)
        return _execute_prepared_action(context, prepared_action_id)

    @server.tool(
        name=PREPARE_SUPPLIER_INVOICE_TOOL,
        description="Create a local prepared Bokio supplier invoice action for human approval.",
    )
    def bokio_prepare_supplier_invoice(
        payload: dict[str, Any],
        note: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, ctx, PREPARE_SUPPLIER_INVOICE_TOOL)
        return _prepare_action(
            context,
            kind="supplier_invoice",
            payload=payload,
            source=resolved_session_id,
            note=note,
        )

    @server.tool(
        name=CREATE_APPROVED_SUPPLIER_INVOICE_TOOL,
        description="Create a Bokio supplier invoice from an approved local prepared action.",
    )
    def bokio_create_approved_supplier_invoice(
        prepared_action_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, CREATE_APPROVED_SUPPLIER_INVOICE_TOOL)
        return _execute_prepared_action(context, prepared_action_id)

    @server.tool(
        name=PREPARE_BANK_PAYMENT_TOOL,
        description="Create a local prepared Bokio bank payment action for human approval.",
    )
    def bokio_prepare_bank_payment(
        payload: dict[str, Any],
        note: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, ctx, PREPARE_BANK_PAYMENT_TOOL)
        return _prepare_action(
            context,
            kind="bank_payment",
            payload=payload,
            source=resolved_session_id,
            note=note,
        )

    @server.tool(
        name=CREATE_APPROVED_BANK_PAYMENT_TOOL,
        description=(
            "Create a Bokio bank payment from an approved prepared action. "
            "The payment still requires BankID signing in Bokio."
        ),
    )
    def bokio_create_approved_bank_payment(
        prepared_action_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, CREATE_APPROVED_BANK_PAYMENT_TOOL)
        return _execute_prepared_action(context, prepared_action_id)

    return MCP_TOOL_NAMES


def _client(context: AddonContext) -> BokioClient:
    return BokioClient.from_context(context)


def _prepare_action(
    context: AddonContext,
    *,
    kind: str,
    payload: dict[str, Any],
    source: str,
    note: str | None,
) -> dict[str, Any]:
    action = PreparedActionStore(context.runtime_dir).create_action(
        kind=kind,
        payload=payload,
        source=source,
        note=note,
    )
    return {
        "action": action,
        "message": "Prepared Bokio action created. Approve it in Admin Web before execution.",
    }
