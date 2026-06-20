from __future__ import annotations

from typing import Any

from agent_sandbox.addons import AddonContext

from .api import _execute_prepared_action
from .client import BokioClient
from .state import PreparedActionStore


READ_TOOLS = (
    "bokio_get_company",
    "bokio_list_accounts",
    "bokio_list_journal_entries",
    "bokio_list_invoices",
)
WRITE_TOOLS = (
    "bokio_upload_document",
    "bokio_prepare_journal_entry",
    "bokio_create_approved_journal_entry",
    "bokio_prepare_supplier_invoice",
    "bokio_create_approved_supplier_invoice",
    "bokio_prepare_bank_payment",
    "bokio_create_approved_bank_payment",
)
MCP_TOOL_NAMES = (*READ_TOOLS, *WRITE_TOOLS)


def register_mcp_tools(server: Any, context: AddonContext) -> tuple[str, ...]:
    @server.tool(
        name="bokio_get_company",
        description="Read Bokio company information for the configured private integration company.",
    )
    def bokio_get_company(session_id: str | None = None) -> dict[str, Any]:
        del session_id
        return {"result": _client(context).get_company()}

    @server.tool(
        name="bokio_list_accounts",
        description="List Bokio chart-of-accounts records, optionally using a Bokio query filter.",
    )
    def bokio_list_accounts(
        query: str | None = None, session_id: str | None = None
    ) -> dict[str, Any]:
        del session_id
        return {"result": _client(context).list_accounts(query=query)}

    @server.tool(
        name="bokio_list_journal_entries",
        description="List Bokio journal entries with optional pagination and query filter.",
    )
    def bokio_list_journal_entries(
        page: int = 1,
        page_size: int = 25,
        query: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del session_id
        return {
            "result": _client(context).list_journal_entries(
                page=page,
                page_size=page_size,
                query=query,
            )
        }

    @server.tool(
        name="bokio_list_invoices",
        description="List Bokio invoices with optional pagination and query filter.",
    )
    def bokio_list_invoices(
        page: int = 1,
        page_size: int = 25,
        query: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del session_id
        return {"result": _client(context).list_invoices(page=page, page_size=page_size, query=query)}

    @server.tool(
        name="bokio_upload_document",
        description="Upload a host-local PDF, JPEG, or PNG document to Bokio.",
    )
    def bokio_upload_document(
        file_path: str,
        description: str | None = None,
        journal_entry_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del session_id
        return {
            "result": _client(context).upload_document(
                file_path=file_path,
                description=description,
                journal_entry_id=journal_entry_id,
            )
        }

    @server.tool(
        name="bokio_prepare_journal_entry",
        description="Create a local prepared Bokio journal entry action for human approval.",
    )
    def bokio_prepare_journal_entry(
        payload: dict[str, Any],
        note: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return _prepare_action(
            context,
            kind="journal_entry",
            payload=payload,
            source=session_id or "agent",
            note=note,
        )

    @server.tool(
        name="bokio_create_approved_journal_entry",
        description="Create a Bokio journal entry from an approved local prepared action.",
    )
    def bokio_create_approved_journal_entry(
        prepared_action_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del session_id
        return _execute_prepared_action(context, prepared_action_id)

    @server.tool(
        name="bokio_prepare_supplier_invoice",
        description="Create a local prepared Bokio supplier invoice action for human approval.",
    )
    def bokio_prepare_supplier_invoice(
        payload: dict[str, Any],
        note: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return _prepare_action(
            context,
            kind="supplier_invoice",
            payload=payload,
            source=session_id or "agent",
            note=note,
        )

    @server.tool(
        name="bokio_create_approved_supplier_invoice",
        description="Create a Bokio supplier invoice from an approved local prepared action.",
    )
    def bokio_create_approved_supplier_invoice(
        prepared_action_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del session_id
        return _execute_prepared_action(context, prepared_action_id)

    @server.tool(
        name="bokio_prepare_bank_payment",
        description="Create a local prepared Bokio bank payment action for human approval.",
    )
    def bokio_prepare_bank_payment(
        payload: dict[str, Any],
        note: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return _prepare_action(
            context,
            kind="bank_payment",
            payload=payload,
            source=session_id or "agent",
            note=note,
        )

    @server.tool(
        name="bokio_create_approved_bank_payment",
        description=(
            "Create a Bokio bank payment from an approved prepared action. "
            "The payment still requires BankID signing in Bokio."
        ),
    )
    def bokio_create_approved_bank_payment(
        prepared_action_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del session_id
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
