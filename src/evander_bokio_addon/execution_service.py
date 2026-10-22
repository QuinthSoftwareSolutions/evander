from __future__ import annotations

from typing import Any

from evander.addons import AddonContext

from .client import BokioClient
from .evidence import (
    BokioEvidenceStore,
    create_journal_entry_with_evidence,
    normalize_stored_evidence_ids,
)
from .operations import (
    bokio_error_payload,
    bokio_operator_error_message,
    bokio_write_failure_is_uncertain,
)
from .state import PreparedActionStore


def execute_prepared_action(
    context: AddonContext,
    action_id: str,
    *,
    approve: bool = False,
    retry: bool = False,
    confirm_not_created: bool = False,
) -> dict[str, Any]:
    """Execute a claimed approved action, preserving durable retries and evidence recovery."""
    store = PreparedActionStore(context.runtime_dir)
    action, claimed = store.claim_execution(
        action_id,
        approve=approve,
        retry=retry,
        confirm_not_created=confirm_not_created,
    )
    if not claimed:
        return {"action": action, "result": action.get("result"), "idempotent": True}
    evidence_operation: dict[str, Any] | None = None
    evidence_operation_key: str | None = None
    try:
        kind = str(action.get("kind") or "").strip()
        payload = _object(action.get("payload"), "payload")
        client = BokioClient.from_context(context)
        if kind == "journal_entry":
            evidence_ids = normalize_stored_evidence_ids(
                context.runtime_dir,
                action.get("evidence_ids"),
            )
            if evidence_ids:
                evidence_operation_key = str(
                    action.get("evidence_operation_key") or f"prepared-action:{action_id}"
                )
                evidence_operation = create_journal_entry_with_evidence(
                    context,
                    payload=payload,
                    evidence_ids=evidence_ids,
                    operation_key=evidence_operation_key,
                    retry_if_failed=retry,
                    confirm_not_created=confirm_not_created,
                )
                result = _object(evidence_operation.get("result"), "evidence result")
            else:
                result = client.create_journal_entry(payload)
        elif kind == "supplier_invoice":
            result = client.create_supplier_invoice(payload)
        elif kind == "invoice":
            result = client.create_invoice(payload)
        elif kind == "bank_payment":
            result = client.create_bank_payment(payload)
        else:
            raise ValueError(f"Unsupported Bokio prepared action kind: {kind}")
    except Exception as exc:
        error = bokio_error_payload(exc)
        if evidence_operation_key is not None:
            try:
                current_operation = BokioEvidenceStore(
                    context.runtime_dir
                ).get_journal_entry_operation(evidence_operation_key)
            except Exception:
                current_operation = None
            if current_operation is not None:
                error["evidence_operation"] = current_operation
                if str(current_operation.get("journal_entry_id") or "").strip():
                    error["journal_entry_created"] = True
                    error["operator_message"] = bokio_operator_error_message(error)
                    store.mark_failed(
                        action_id,
                        error=error,
                        detail=(
                            "Journal entry created, but evidence attachment failed; retry will "
                            "resume evidence without recreating the entry"
                        ),
                    )
                    raise
                if current_operation.get("status") in {"creating", "needs_attention"}:
                    store.mark_needs_attention(action_id, error=error)
                    raise
        if bokio_write_failure_is_uncertain(exc):
            store.mark_needs_attention(action_id, error=error)
        else:
            store.mark_failed(action_id, error=error)
        raise
    action = store.mark_executed(
        action_id,
        result=result,
        evidence_result=evidence_operation,
    )
    response = {"action": action, "result": result}
    if evidence_operation is not None:
        response["evidence_operation"] = evidence_operation
    return response


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)
