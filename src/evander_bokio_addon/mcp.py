from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext, AddonMcpTool
from evander.communication_artifacts import CommunicationArtifactStore
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.policy import PolicyError

from .api import _execute_prepared_action
from .client import BokioClient
from .duplicate_checks import (
    DEFAULT_DUPLICATE_CHECK_MAX_AGE_SECONDS,
    BokioDuplicateCheckError,
    BokioDuplicateCheckStore,
    check_journal_duplicates,
)
from .evidence import (
    BokioEvidenceStore,
    attach_evidence as _attach_evidence,
    create_journal_entry_with_evidence as _create_journal_entry_with_evidence,
    normalize_stored_evidence_ids,
)
from .operations import (
    BokioOperationStore,
    bokio_error_payload,
    bokio_write_failure_is_uncertain,
)
from .state import PreparedActionStore, upload_staging_dir


GET_COMPANY_TOOL = "bokio_get_company"
LIST_ACCOUNTS_TOOL = "bokio_list_accounts"
LIST_JOURNAL_ENTRIES_TOOL = "bokio_list_journal_entries"
CHECK_JOURNAL_DUPLICATES_TOOL = "bokio_check_journal_duplicates"
LIST_CUSTOMERS_TOOL = "bokio_list_customers"
GET_CUSTOMER_TOOL = "bokio_get_customer"
LIST_INVOICES_TOOL = "bokio_list_invoices"
LIST_SUPPLIERS_TOOL = "bokio_list_suppliers"
GET_SUPPLIER_TOOL = "bokio_get_supplier"
LIST_UPLOADS_TOOL = "bokio_list_uploads"
GET_UPLOAD_TOOL = "bokio_get_upload"
DOWNLOAD_UPLOAD_TOOL = "bokio_download_upload"
LIST_EVIDENCE_TOOL = "bokio_list_evidence"
GET_EVIDENCE_TOOL = "bokio_get_evidence"
STAGE_EVIDENCE_TOOL = "bokio_stage_evidence_for_workspace"
IMPORT_EVIDENCE_ARTIFACT_TOOL = "bokio_import_evidence_artifact"
UPLOAD_DOCUMENT_TOOL = "bokio_upload_document"
ATTACH_EVIDENCE_TOOL = "bokio_attach_evidence"
CREATE_JOURNAL_ENTRY_WITH_EVIDENCE_TOOL = "bokio_create_journal_entry_with_evidence"
PREPARE_JOURNAL_ENTRY_TOOL = "bokio_prepare_journal_entry"
CREATE_APPROVED_JOURNAL_ENTRY_TOOL = "bokio_create_approved_journal_entry"
PREPARE_SUPPLIER_INVOICE_TOOL = "bokio_prepare_supplier_invoice"
CREATE_APPROVED_SUPPLIER_INVOICE_TOOL = "bokio_create_approved_supplier_invoice"
CREATE_SUPPLIER_INVOICE_TOOL = "bokio_create_supplier_invoice"
PREPARE_BANK_PAYMENT_TOOL = "bokio_prepare_bank_payment"
CREATE_APPROVED_BANK_PAYMENT_TOOL = "bokio_create_approved_bank_payment"

READ_TOOLS = (
    GET_COMPANY_TOOL,
    LIST_ACCOUNTS_TOOL,
    LIST_JOURNAL_ENTRIES_TOOL,
    CHECK_JOURNAL_DUPLICATES_TOOL,
    LIST_CUSTOMERS_TOOL,
    GET_CUSTOMER_TOOL,
    LIST_INVOICES_TOOL,
    LIST_SUPPLIERS_TOOL,
    GET_SUPPLIER_TOOL,
    LIST_UPLOADS_TOOL,
    GET_UPLOAD_TOOL,
    LIST_EVIDENCE_TOOL,
    GET_EVIDENCE_TOOL,
)
WRITE_TOOLS = (
    DOWNLOAD_UPLOAD_TOOL,
    STAGE_EVIDENCE_TOOL,
    IMPORT_EVIDENCE_ARTIFACT_TOOL,
    UPLOAD_DOCUMENT_TOOL,
    ATTACH_EVIDENCE_TOOL,
    CREATE_JOURNAL_ENTRY_WITH_EVIDENCE_TOOL,
    PREPARE_JOURNAL_ENTRY_TOOL,
    CREATE_APPROVED_JOURNAL_ENTRY_TOOL,
    PREPARE_SUPPLIER_INVOICE_TOOL,
    CREATE_APPROVED_SUPPLIER_INVOICE_TOOL,
    CREATE_SUPPLIER_INVOICE_TOOL,
    PREPARE_BANK_PAYMENT_TOOL,
    CREATE_APPROVED_BANK_PAYMENT_TOOL,
)
MCP_TOOL_NAMES = (*READ_TOOLS, *WRITE_TOOLS)


def _catalog(name: str, label: str, description: str) -> AddonMcpTool:
    return AddonMcpTool(name=name, label=label, description=description)


MCP_TOOL_CATALOG = (
    _catalog(GET_COMPANY_TOOL, "Get company", "Read the configured Bokio company."),
    _catalog(LIST_ACCOUNTS_TOOL, "List accounts", "List Bokio chart-of-accounts records."),
    _catalog(
        LIST_JOURNAL_ENTRIES_TOOL,
        "List journal entries",
        "List Bokio journal entries with optional paging and filtering.",
    ),
    _catalog(
        CHECK_JOURNAL_DUPLICATES_TOOL,
        "Check journal duplicates",
        "Scan all Bokio journal pages for matching entries and retain durable check evidence.",
    ),
    _catalog(
        LIST_CUSTOMERS_TOOL,
        "List customers",
        "List Bokio customers with optional paging and filtering.",
    ),
    _catalog(GET_CUSTOMER_TOOL, "Get customer", "Read one Bokio customer."),
    _catalog(LIST_INVOICES_TOOL, "List invoices", "List Bokio customer invoices."),
    _catalog(
        LIST_SUPPLIERS_TOOL,
        "List suppliers",
        "List Bokio suppliers with optional paging and filtering. This API is in preview.",
    ),
    _catalog(
        GET_SUPPLIER_TOOL,
        "Get supplier",
        "Read one Bokio supplier. This API is in preview.",
    ),
    _catalog(
        LIST_UPLOADS_TOOL,
        "List uploads",
        "List receipt and invoice uploads stored remotely in Bokio.",
    ),
    _catalog(GET_UPLOAD_TOOL, "Get upload", "Read one remote Bokio upload's metadata."),
    _catalog(
        DOWNLOAD_UPLOAD_TOOL,
        "Download upload",
        "Download a remote Bokio upload as a session communication artifact.",
    ),
    _catalog(LIST_EVIDENCE_TOOL, "List evidence", "List managed Bokio evidence records."),
    _catalog(GET_EVIDENCE_TOOL, "Get evidence", "Read one managed Bokio evidence record."),
    _catalog(
        STAGE_EVIDENCE_TOOL,
        "Stage evidence for workspace",
        "Stage managed Bokio evidence as a session communication artifact.",
    ),
    _catalog(
        IMPORT_EVIDENCE_ARTIFACT_TOOL,
        "Import evidence artifact",
        "Import a session-owned communication artifact into durable Bokio evidence.",
    ),
    _catalog(UPLOAD_DOCUMENT_TOOL, "Upload document", "Upload a staged document to Bokio."),
    _catalog(
        ATTACH_EVIDENCE_TOOL,
        "Attach evidence",
        "Upload managed evidence and attach it to a Bokio journal entry.",
    ),
    _catalog(
        CREATE_JOURNAL_ENTRY_WITH_EVIDENCE_TOOL,
        "Create journal entry with evidence",
        "Create a Bokio journal entry and attach managed evidence idempotently.",
    ),
    _catalog(
        PREPARE_JOURNAL_ENTRY_TOOL,
        "Prepare journal entry",
        "Prepare a local Bokio journal-entry and managed-evidence action for operator review.",
    ),
    _catalog(
        CREATE_APPROVED_JOURNAL_ENTRY_TOOL,
        "Create approved journal entry",
        "Execute an approved prepared Bokio journal-entry action.",
    ),
    _catalog(
        PREPARE_SUPPLIER_INVOICE_TOOL,
        "Prepare supplier invoice",
        "Prepare a local Bokio supplier-invoice action for operator review.",
    ),
    _catalog(
        CREATE_APPROVED_SUPPLIER_INVOICE_TOOL,
        "Create approved supplier invoice",
        "Execute an approved prepared Bokio supplier-invoice action.",
    ),
    _catalog(
        CREATE_SUPPLIER_INVOICE_TOOL,
        "Create supplier invoice",
        "Create a Bokio supplier invoice autonomously with a durable operation key.",
    ),
    _catalog(
        PREPARE_BANK_PAYMENT_TOOL,
        "Prepare bank payment",
        "Prepare a local Bokio bank-payment action for operator review.",
    ),
    _catalog(
        CREATE_APPROVED_BANK_PAYMENT_TOOL,
        "Create approved bank payment",
        "Execute an approved Bokio bank payment; BankID signing remains required.",
    ),
)


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
        name=CHECK_JOURNAL_DUPLICATES_TOOL,
        description=(
            "Check all Bokio journal-entry pages for entries matching date, account, amount, "
            "title, invoice_number, or receipt_number criteria. The result includes a durable "
            "evidence reference and is reused for up to 15 minutes by default."
        ),
    )
    def bokio_check_journal_duplicates(
        criteria: dict[str, Any],
        max_age_seconds: int = DEFAULT_DUPLICATE_CHECK_MAX_AGE_SECONDS,
        refresh: bool = False,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, CHECK_JOURNAL_DUPLICATES_TOOL)
        try:
            return {
                "result": check_journal_duplicates(
                    _client(context),
                    BokioDuplicateCheckStore(context.runtime_dir),
                    criteria=criteria,
                    max_age_seconds=max_age_seconds,
                    refresh=refresh,
                )
            }
        except BokioDuplicateCheckError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name=LIST_CUSTOMERS_TOOL,
        description="List Bokio customers with optional pagination and query filter.",
    )
    def bokio_list_customers(
        page: int = 1,
        page_size: int = 25,
        query: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, LIST_CUSTOMERS_TOOL)
        return {
            "result": _client(context).list_customers(
                page=page,
                page_size=page_size,
                query=query,
            )
        }

    @server.tool(
        name=GET_CUSTOMER_TOOL,
        description="Read one Bokio customer by customer ID.",
    )
    def bokio_get_customer(
        customer_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, GET_CUSTOMER_TOOL)
        return {"result": _client(context).get_customer(customer_id)}

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
        return {
            "result": _client(context).list_invoices(page=page, page_size=page_size, query=query)
        }

    @server.tool(
        name=LIST_SUPPLIERS_TOOL,
        description=(
            "List Bokio suppliers with optional pagination and query filter. "
            "The Bokio suppliers API is in preview."
        ),
    )
    def bokio_list_suppliers(
        page: int = 1,
        page_size: int = 25,
        query: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, LIST_SUPPLIERS_TOOL)
        return {
            "result": _client(context).list_suppliers(
                page=page,
                page_size=page_size,
                query=query,
            )
        }

    @server.tool(
        name=GET_SUPPLIER_TOOL,
        description="Read one Bokio supplier by supplier ID. The Bokio suppliers API is in preview.",
    )
    def bokio_get_supplier(
        supplier_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, GET_SUPPLIER_TOOL)
        return {"result": _client(context).get_supplier(supplier_id)}

    @server.tool(
        name=LIST_UPLOADS_TOOL,
        description="List remote Bokio uploads with optional pagination and query filter.",
    )
    def bokio_list_uploads(
        page: int = 1,
        page_size: int = 25,
        query: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, LIST_UPLOADS_TOOL)
        return {
            "result": _client(context).list_uploads(
                page=page,
                page_size=page_size,
                query=query,
            )
        }

    @server.tool(
        name=GET_UPLOAD_TOOL,
        description="Read metadata for one upload stored remotely in Bokio.",
    )
    def bokio_get_upload(
        upload_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, GET_UPLOAD_TOOL)
        return {"result": _client(context).get_upload(upload_id)}

    @server.tool(
        name=DOWNLOAD_UPLOAD_TOOL,
        description="Download one remote Bokio upload as a session-owned communication artifact.",
    )
    def bokio_download_upload(
        upload_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, ctx, DOWNLOAD_UPLOAD_TOOL)
        client = _client(context)
        upload = client.get_upload(upload_id)
        downloaded = client.download_upload(upload_id)
        artifact = CommunicationArtifactStore(context.runtime_dir).stage(
            provider="bokio_upload",
            session_id=resolved_session_id,
            content=downloaded.content,
            original_filename=downloaded.filename,
            mime_type=downloaded.mime_type,
            source_attachment_id=upload_id,
            metadata={"bokio_upload_id": upload_id, "bokio_upload": upload},
        )
        return {
            "upload": upload,
            "artifact": artifact.model_dump(mode="json"),
        }

    @server.tool(
        name=LIST_EVIDENCE_TOOL,
        description="List managed Bokio receipt and invoice evidence with association status.",
    )
    def bokio_list_evidence(
        status: str | None = None,
        limit: int = 100,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, LIST_EVIDENCE_TOOL)
        return {"items": _evidence_store(context).list(status=status, limit=limit)}

    @server.tool(
        name=GET_EVIDENCE_TOOL,
        description="Read one managed Bokio evidence record and its source provenance.",
    )
    def bokio_get_evidence(
        evidence_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, GET_EVIDENCE_TOOL)
        return {"evidence": _evidence_store(context).get(evidence_id)}

    @server.tool(
        name=STAGE_EVIDENCE_TOOL,
        description=(
            "Stage canonical Bokio evidence as a session-scoped communication artifact so the "
            "agent can copy it to its workspace with communication_copy_artifact_to_workspace."
        ),
    )
    def bokio_stage_evidence_for_workspace(
        evidence_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, ctx, STAGE_EVIDENCE_TOOL)
        evidence = _evidence_store(context).get_file(evidence_id)
        artifact = CommunicationArtifactStore(context.runtime_dir).stage(
            provider="bokio_evidence",
            session_id=resolved_session_id,
            content=evidence.blob_path.read_bytes(),
            original_filename=str(evidence.record["filename"]),
            mime_type=str(evidence.record["mime_type"]),
            metadata={"bokio_evidence_id": evidence.record["evidence_id"]},
        )
        return {
            "evidence": evidence.record,
            "artifact": artifact.model_dump(mode="json"),
        }

    @server.tool(
        name=IMPORT_EVIDENCE_ARTIFACT_TOOL,
        description=(
            "Import a PDF, JPEG, or PNG communication or connector artifact owned by this "
            "session into durable Bokio evidence storage."
        ),
    )
    def bokio_import_evidence_artifact(
        artifact_id: str,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, ctx, IMPORT_EVIDENCE_ARTIFACT_TOOL)
        artifact = CommunicationArtifactStore(context.runtime_dir).get(artifact_id)
        if artifact.metadata.session_id != resolved_session_id:
            raise ToolError("Bokio evidence imports require an artifact owned by this session")
        record, created = _evidence_store(context).ingest_bytes(
            content=artifact.blob_path.read_bytes(),
            filename=artifact.metadata.filename,
            source_type="communication_artifact",
            source_key=artifact.metadata.artifact_id,
            source_metadata={
                "artifact_id": artifact.metadata.artifact_id,
                "provider": artifact.metadata.provider,
                "source_message_id": artifact.metadata.source_message_id,
                "source_attachment_id": artifact.metadata.source_attachment_id,
                "source_channel": artifact.metadata.source_channel,
                "source_mailbox": artifact.metadata.source_mailbox,
                "artifact_metadata": artifact.metadata.metadata,
            },
            mime_type=artifact.metadata.mime_type,
            created_at=artifact.metadata.created_at,
        )
        return {"evidence": record, "created": created}

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
        name=ATTACH_EVIDENCE_TOOL,
        description=(
            "Upload one managed receipt or invoice to Bokio and attach it to an existing "
            "Bokio journal entry. This outward operation follows the normal MCP tool approval policy."
        ),
    )
    def bokio_attach_evidence(
        evidence_id: str,
        journal_entry_id: str,
        description: str | None = None,
        retry_if_failed: bool = False,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, ATTACH_EVIDENCE_TOOL)
        return {
            "association": _attach_evidence(
                context,
                evidence_id=evidence_id,
                journal_entry_id=journal_entry_id,
                description=description,
                retry_if_failed=retry_if_failed,
            )
        }

    @server.tool(
        name=CREATE_JOURNAL_ENTRY_WITH_EVIDENCE_TOOL,
        description=(
            "Create a Bokio journal entry and attach managed evidence. This direct write is "
            "autonomous or per-invocation approved according to the normal MCP policy. Definite "
            "failures require retry_if_failed; uncertain creates require checking Bokio and "
            "confirm_not_created before retrying."
        ),
    )
    def bokio_create_journal_entry_with_evidence(
        payload: dict[str, Any],
        evidence_ids: list[str],
        operation_key: str | None = None,
        retry_if_failed: bool = False,
        confirm_not_created: bool = False,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        _require_tool(session_id, ctx, CREATE_JOURNAL_ENTRY_WITH_EVIDENCE_TOOL)
        return {
            "operation": _create_journal_entry_with_evidence(
                context,
                payload=payload,
                evidence_ids=evidence_ids,
                operation_key=operation_key,
                retry_if_failed=retry_if_failed,
                confirm_not_created=confirm_not_created,
            )
        }

    @server.tool(
        name=PREPARE_JOURNAL_ENTRY_TOOL,
        description=(
            "Create a local prepared Bokio journal entry action, including any managed "
            "evidence IDs, for human approval."
        ),
    )
    def bokio_prepare_journal_entry(
        payload: dict[str, Any],
        evidence_ids: list[str] | None = None,
        note: str | None = None,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, ctx, PREPARE_JOURNAL_ENTRY_TOOL)
        return _prepare_action(
            context,
            kind="journal_entry",
            payload=payload,
            evidence_ids=evidence_ids,
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
        name=CREATE_SUPPLIER_INVOICE_TOOL,
        description=(
            "Create a Bokio supplier invoice using a caller-supplied durable operation key. "
            "Exact repeats return the stored result; uncertain outcomes require checking Bokio "
            "and explicitly confirming that the invoice was not created before retrying."
        ),
    )
    def bokio_create_supplier_invoice(
        payload: dict[str, Any],
        operation_key: str,
        retry_if_failed: bool = False,
        confirm_not_created: bool = False,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _require_tool(session_id, ctx, CREATE_SUPPLIER_INVOICE_TOOL)
        store = BokioOperationStore(context.runtime_dir)
        operation = store.begin(
            session_id=resolved_session_id,
            operation_kind="supplier_invoice",
            operation_key=operation_key,
            payload=payload,
        )
        if operation["status"] == "succeeded":
            return {"operation": {**operation, "idempotent": True}}
        operation, claimed = store.claim(
            session_id=resolved_session_id,
            operation_kind="supplier_invoice",
            operation_key=operation_key,
            retry_if_failed=retry_if_failed,
            confirm_not_created=confirm_not_created,
        )
        if not claimed:
            return {"operation": {**operation, "idempotent": True}}
        try:
            result = _client(context).create_supplier_invoice(payload)
        except Exception as exc:
            store.mark_failed(
                session_id=resolved_session_id,
                operation_kind="supplier_invoice",
                operation_key=operation_key,
                error=bokio_error_payload(exc),
                uncertain=bokio_write_failure_is_uncertain(exc),
            )
            raise
        operation = store.mark_succeeded(
            session_id=resolved_session_id,
            operation_kind="supplier_invoice",
            operation_key=operation_key,
            result=result,
        )
        return {"operation": operation}

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


def _evidence_store(context: AddonContext) -> BokioEvidenceStore:
    return BokioEvidenceStore(context.runtime_dir)


def _prepare_action(
    context: AddonContext,
    *,
    kind: str,
    payload: dict[str, Any],
    evidence_ids: list[str] | None = None,
    source: str,
    note: str | None,
) -> dict[str, Any]:
    normalized_evidence_ids = normalize_stored_evidence_ids(
        context.runtime_dir,
        evidence_ids,
    )
    action = PreparedActionStore(context.runtime_dir).create_action(
        kind=kind,
        payload=payload,
        evidence_ids=normalized_evidence_ids,
        source=source,
        note=note,
    )
    return {
        "action": action,
        "message": "Prepared Bokio action created. Approve it in Admin Web before execution.",
    }
