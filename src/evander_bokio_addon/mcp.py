from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext
from evander.communication_artifacts import CommunicationArtifactStore
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.policy import PolicyError

from .api import _execute_prepared_action
from .client import BokioClient
from .evidence import BokioEvidenceError, BokioEvidenceStore, validate_evidence_id
from .state import PreparedActionStore, upload_staging_dir


GET_COMPANY_TOOL = "bokio_get_company"
LIST_ACCOUNTS_TOOL = "bokio_list_accounts"
LIST_JOURNAL_ENTRIES_TOOL = "bokio_list_journal_entries"
LIST_INVOICES_TOOL = "bokio_list_invoices"
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
PREPARE_BANK_PAYMENT_TOOL = "bokio_prepare_bank_payment"
CREATE_APPROVED_BANK_PAYMENT_TOOL = "bokio_create_approved_bank_payment"

READ_TOOLS = (
    GET_COMPANY_TOOL,
    LIST_ACCOUNTS_TOOL,
    LIST_JOURNAL_ENTRIES_TOOL,
    LIST_INVOICES_TOOL,
    LIST_EVIDENCE_TOOL,
    GET_EVIDENCE_TOOL,
)
WRITE_TOOLS = (
    STAGE_EVIDENCE_TOOL,
    IMPORT_EVIDENCE_ARTIFACT_TOOL,
    UPLOAD_DOCUMENT_TOOL,
    ATTACH_EVIDENCE_TOOL,
    CREATE_JOURNAL_ENTRY_WITH_EVIDENCE_TOOL,
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
            "autonomous or per-invocation approved according to the normal MCP policy."
        ),
    )
    def bokio_create_journal_entry_with_evidence(
        payload: dict[str, Any],
        evidence_ids: list[str],
        operation_key: str | None = None,
        retry_if_failed: bool = False,
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


def _evidence_store(context: AddonContext) -> BokioEvidenceStore:
    return BokioEvidenceStore(context.runtime_dir)


def _attach_evidence(
    context: AddonContext,
    *,
    evidence_id: str,
    journal_entry_id: str,
    description: str | None,
    retry_if_failed: bool = False,
) -> dict[str, Any]:
    store = _evidence_store(context)
    normalized_evidence_id = validate_evidence_id(evidence_id)
    association = store.begin_association(
        evidence_id=normalized_evidence_id,
        journal_entry_id=journal_entry_id,
        retry_if_failed=retry_if_failed,
    )
    if association["status"] == "attached":
        return {**association, "idempotent": True}
    evidence = store.get_file(normalized_evidence_id)
    try:
        result = _client(context).upload_document(
            file_path=evidence.blob_path,
            allowed_roots=[store.blob_root],
            description=description,
            journal_entry_id=journal_entry_id,
        )
    except Exception as exc:
        store.mark_association_failed(
            evidence_id=normalized_evidence_id,
            journal_entry_id=journal_entry_id,
            error=exc,
        )
        raise
    return store.mark_association_attached(
        evidence_id=normalized_evidence_id,
        journal_entry_id=journal_entry_id,
        bokio_result=result,
    )


def _create_journal_entry_with_evidence(
    context: AddonContext,
    *,
    payload: dict[str, Any],
    evidence_ids: list[str],
    operation_key: str | None,
    retry_if_failed: bool,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ToolError("payload must be a JSON object")
    normalized_evidence_ids = [validate_evidence_id(item) for item in evidence_ids]
    generated_key = sha256(
        json.dumps(
            {"payload": payload, "evidence_ids": normalized_evidence_ids}, sort_keys=True, default=str
        ).encode("utf-8")
    ).hexdigest()
    store = _evidence_store(context)
    operation = store.begin_journal_entry_operation(
        operation_key=str(operation_key or generated_key),
        payload=payload,
        evidence_ids=normalized_evidence_ids,
    )
    if operation["status"] == "completed":
        return {**operation, "idempotent": True}
    if operation["status"] == "needs_attention":
        operation = store.resume_journal_entry_operation(
            operation_key=operation["operation_key"],
            retry_if_failed=retry_if_failed,
        )
    if operation["status"] == "pending_create":
        operation, claimed = store.claim_journal_entry_creation(
            operation_key=operation["operation_key"]
        )
        if claimed:
            try:
                created = _client(context).create_journal_entry(payload)
                journal_entry_id = str(created.get("id") or "").strip()
                if not journal_entry_id:
                    raise BokioEvidenceError(
                        "Bokio journal-entry create response did not include an ID"
                    )
                operation = store.mark_journal_entry_created(
                    operation_key=operation["operation_key"],
                    journal_entry_id=journal_entry_id,
                    result=created,
                )
            except Exception as exc:
                store.mark_journal_entry_operation_needs_attention(
                    operation_key=operation["operation_key"], error=exc
                )
                raise
    if operation["status"] == "completed":
        return {**operation, "idempotent": True}
    if operation["status"] == "creating":
        raise BokioEvidenceError(
            "Bokio journal-entry creation is already in flight or its outcome is unknown; "
            "inspect Bokio before retrying"
        )
    if operation["status"] == "needs_attention":
        operation = store.resume_journal_entry_operation(
            operation_key=operation["operation_key"],
            retry_if_failed=retry_if_failed,
        )
    journal_entry_id = str(operation.get("journal_entry_id") or "").strip()
    if not journal_entry_id:
        raise BokioEvidenceError("Bokio journal-entry operation is missing its created entry ID")
    try:
        associations = [
            _attach_evidence(
                context,
                evidence_id=evidence_id,
                journal_entry_id=journal_entry_id,
                description=None,
                retry_if_failed=retry_if_failed,
            )
            for evidence_id in normalized_evidence_ids
        ]
    except Exception as exc:
        store.mark_journal_entry_operation_needs_attention(
            operation_key=operation["operation_key"], error=exc
        )
        raise
    operation = store.mark_journal_entry_operation_completed(
        operation_key=operation["operation_key"]
    )
    return {**operation, "associations": associations}


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
