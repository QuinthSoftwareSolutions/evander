from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import httpx
from mcp.server.fastmcp.exceptions import ToolError
import pytest

from evander.addons import AddonContext
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.models import ApprovalRequest, SessionManifest
from evander.policy import PolicyEngine
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.storage import JsonStore
from evander_bokio_addon.client import (
    BokioApiError,
    BokioClient,
    BokioConfigError,
    settings_from_context,
)
from evander_bokio_addon.evidence import BokioEvidenceError, BokioEvidenceStore
from evander_bokio_addon.mcp import MCP_TOOL_NAMES, register_mcp_tools
from evander_bokio_addon.operations import BokioOperationError, BokioOperationStore
from evander_bokio_addon.providers import BokioCompanyQueryProvider
from evander_bokio_addon.state import PreparedActionStore, upload_staging_dir


def test_bokio_client_uses_private_integration_bearer_token(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"companyInformation": {"name": "Example AB"}},
        )

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")

    result = BokioClient(settings_from_context()).get_company()

    assert result == {"companyInformation": {"name": "Example AB"}}
    assert str(requests[0].url) == "https://api.bokio.se/v1/companies/company-1/company-information"
    assert requests[0].headers["authorization"] == "Bearer secret-token"


def test_bokio_client_normalizes_api_errors(monkeypatch) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"Bokio-Plan-Warning": "plan warning"},
            json={
                "error": "price_plan_feature_required",
                "message": "This feature requires Integrations (API) or a Plus plan",
            },
        )

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")

    with pytest.raises(BokioApiError) as exc_info:
        BokioClient(settings_from_context()).list_accounts()

    payload = exc_info.value.to_payload()
    assert payload["status_code"] == 403
    assert payload["plan_warning"] == "plan warning"
    assert payload["payload"]["error"] == "price_plan_feature_required"


def test_bokio_upload_document_requires_staged_pdf_jpeg_or_png(
    tmp_path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    upload_root = upload_staging_dir(tmp_path, session_id="session-1")
    upload_root.mkdir(parents=True)
    receipt = upload_root / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.7\nreceipt")
    outside = tmp_path / "secrets.env"
    outside.write_text("SECRET=value", encoding="utf-8")
    text_file = upload_root / "receipt.txt"
    text_file.write_text("not a receipt image", encoding="utf-8")

    result = BokioClient(settings_from_context()).upload_document(
        file_path="receipt.pdf",
        allowed_roots=[upload_root],
    )

    assert result == {"uploadId": "upload-1"}
    assert str(requests[0].url) == "https://api.bokio.se/v1/companies/company-1/uploads"
    assert requests[0].headers["authorization"] == "Bearer secret-token"
    with pytest.raises(PermissionError, match="allowed upload staging"):
        BokioClient(settings_from_context()).upload_document(
            file_path=outside,
            allowed_roots=[upload_root],
        )
    with pytest.raises(ValueError, match="PDF, JPEG, or PNG"):
        BokioClient(settings_from_context()).upload_document(
            file_path="receipt.txt",
            allowed_roots=[upload_root],
        )
    with pytest.raises(BokioConfigError, match="allowed upload directory"):
        BokioClient(settings_from_context()).upload_document(
            file_path="receipt.pdf",
            allowed_roots=None,
        )


def test_bokio_upload_staging_separates_session_and_reserved_scopes(tmp_path) -> None:
    admin_scope = upload_staging_dir(tmp_path, scope="admin")
    workflow_scope = upload_staging_dir(tmp_path, scope="workflows")
    admin_session = upload_staging_dir(tmp_path, session_id="admin")
    workflow_session = upload_staging_dir(tmp_path, session_id="workflows")

    assert admin_scope == tmp_path / "addons" / "bokio-addon" / "uploads" / "scopes" / "admin"
    assert workflow_scope == (
        tmp_path / "addons" / "bokio-addon" / "uploads" / "scopes" / "workflows"
    )
    assert admin_session == (tmp_path / "addons" / "bokio-addon" / "uploads" / "sessions" / "admin")
    assert workflow_session == (
        tmp_path / "addons" / "bokio-addon" / "uploads" / "sessions" / "workflows"
    )
    assert len({admin_scope, workflow_scope, admin_session, workflow_session}) == 4


def test_bokio_evidence_store_deduplicates_content_and_retains_provenance(tmp_path: Path) -> None:
    store = BokioEvidenceStore(tmp_path)
    first, first_created = store.ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="anthropic-receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_first",
        source_metadata={"provider": "gmail", "message_id": "message-1"},
        mime_type="application/pdf",
    )
    second, second_created = store.ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="duplicate.pdf",
        source_type="communication_artifact",
        source_key="comm_second",
        source_metadata={"provider": "connector:billing-dropbox"},
        mime_type="application/pdf",
    )

    assert first_created is True
    assert second_created is False
    assert second["evidence_id"] == first["evidence_id"]
    assert [source["source_key"] for source in second["sources"]] == [
        "comm_first",
        "comm_second",
    ]
    assert store.get_file(first["evidence_id"]).blob_path.read_bytes() == b"%PDF-1.7\nreceipt"
    with pytest.raises(BokioEvidenceError, match="does not match"):
        store.ingest_bytes(
            content=b"not a PDF",
            filename="malformed.pdf",
            source_type="communication_artifact",
            source_key="comm_bad",
        )


def test_bokio_evidence_store_serializes_concurrent_content_deduplication(
    tmp_path: Path,
) -> None:
    store = BokioEvidenceStore(tmp_path)
    worker_count = 8
    start = threading.Barrier(worker_count)

    def ingest(index: int) -> tuple[dict[str, object], bool]:
        start.wait(timeout=5)
        return store.ingest_bytes(
            content=b"%PDF-1.7\nshared receipt",
            filename=f"receipt-{index}.pdf",
            source_type="communication_artifact",
            source_key=f"comm_{index}",
            mime_type="application/pdf",
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(ingest, range(worker_count)))

    evidence_ids = {record["evidence_id"] for record, _created in results}
    assert len(evidence_ids) == 1
    assert sum(created for _record, created in results) == 1
    evidence = store.get(str(next(iter(evidence_ids))))
    assert {source["source_key"] for source in evidence["sources"]} == {
        f"comm_{index}" for index in range(worker_count)
    }


def test_bokio_journal_operation_rejects_idempotency_key_reuse_for_a_different_request(
    tmp_path: Path,
) -> None:
    store = BokioEvidenceStore(tmp_path)
    first_evidence, _created = store.ingest_bytes(
        content=b"%PDF-1.7\nfirst receipt",
        filename="first.pdf",
        source_type="communication_artifact",
        source_key="comm_first",
        mime_type="application/pdf",
    )
    second_evidence, _created = store.ingest_bytes(
        content=b"%PDF-1.7\nsecond receipt",
        filename="second.pdf",
        source_type="communication_artifact",
        source_key="comm_second",
        mime_type="application/pdf",
    )
    original = store.begin_journal_entry_operation(
        operation_key="receipt-operation",
        payload={"title": "First", "items": []},
        evidence_ids=[first_evidence["evidence_id"]],
    )

    repeated = store.begin_journal_entry_operation(
        operation_key="receipt-operation",
        payload={"items": [], "title": "First"},
        evidence_ids=[first_evidence["evidence_id"]],
    )

    assert repeated == original
    with pytest.raises(BokioEvidenceError, match="different journal-entry request"):
        store.begin_journal_entry_operation(
            operation_key="receipt-operation",
            payload={"title": "Changed", "items": []},
            evidence_ids=[first_evidence["evidence_id"]],
        )
    with pytest.raises(BokioEvidenceError, match="different journal-entry request"):
        store.begin_journal_entry_operation(
            operation_key="receipt-operation",
            payload={"title": "First", "items": []},
            evidence_ids=[second_evidence["evidence_id"]],
        )


def test_bokio_journal_creation_claim_survives_restart_without_being_reclaimed(
    tmp_path: Path,
) -> None:
    store = BokioEvidenceStore(tmp_path)
    evidence, _created = store.ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    store.begin_journal_entry_operation(
        operation_key="receipt-operation",
        payload={"title": "Receipt", "items": []},
        evidence_ids=[evidence["evidence_id"]],
    )

    claimed_operation, claimed = store.claim_journal_entry_creation(
        operation_key="receipt-operation"
    )
    reopened_store = BokioEvidenceStore(tmp_path)
    reopened_operation = reopened_store.begin_journal_entry_operation(
        operation_key="receipt-operation",
        payload={"title": "Receipt", "items": []},
        evidence_ids=[evidence["evidence_id"]],
    )
    retry_operation, reclaimed = reopened_store.claim_journal_entry_creation(
        operation_key="receipt-operation"
    )

    assert claimed is True
    assert claimed_operation["status"] == "creating"
    assert reopened_operation["status"] == "creating"
    assert reclaimed is False
    assert retry_operation["status"] == "creating"


def test_bokio_mcp_imports_only_the_callers_connector_or_email_artifact(tmp_path: Path) -> None:
    artifacts = CommunicationArtifactStore(tmp_path)
    imported = artifacts.stage(
        provider="connector:billing-dropbox",
        session_id="session-1",
        content=b"%PDF-1.7\nreceipt",
        original_filename="openai-receipt.pdf",
        mime_type="application/pdf",
        metadata={"connector_id": "billing-dropbox", "operation": "get_receipt"},
    )
    other_session = artifacts.stage(
        provider="gmail",
        session_id="session-other",
        content=b"%PDF-1.7\nother",
        original_filename="other-receipt.pdf",
        mime_type="application/pdf",
    )
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_import_evidence_artifact"],
    )

    result = server.tools["bokio_import_evidence_artifact"](
        artifact_id=imported.artifact_id,
        session_id="session-1",
    )

    assert result["created"] is True
    source = result["evidence"]["sources"][0]
    assert source["source_type"] == "communication_artifact"
    assert source["source_key"] == imported.artifact_id
    assert source["metadata"]["provider"] == "connector:billing-dropbox"
    with pytest.raises(ToolError, match="owned by this session"):
        server.tools["bokio_import_evidence_artifact"](
            artifact_id=other_session.artifact_id,
            session_id="session-1",
        )


def test_bokio_mcp_attaches_managed_evidence_once(tmp_path: Path, monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    evidence, _created = BokioEvidenceStore(tmp_path).ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    server = _register_bokio_mcp(tmp_path, allowed_tools=["bokio_attach_evidence"])

    first = server.tools["bokio_attach_evidence"](
        evidence_id=evidence["evidence_id"],
        journal_entry_id="journal-entry-1",
        session_id="session-1",
    )
    second = server.tools["bokio_attach_evidence"](
        evidence_id=evidence["evidence_id"],
        journal_entry_id="journal-entry-1",
        session_id="session-1",
    )

    assert first["association"]["status"] == "attached"
    assert second["association"]["idempotent"] is True
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.bokio.se/v1/companies/company-1/uploads"
    assert b'journalEntryId"\r\n\r\njournal-entry-1' in requests[0].content


def test_bokio_mcp_retries_a_failed_attachment_only_when_requested(
    tmp_path: Path, monkeypatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, json={"message": "temporarily unavailable"})
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    evidence, _created = BokioEvidenceStore(tmp_path).ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    server = _register_bokio_mcp(tmp_path, allowed_tools=["bokio_attach_evidence"])

    with pytest.raises(BokioApiError, match="temporarily unavailable"):
        server.tools["bokio_attach_evidence"](
            evidence_id=evidence["evidence_id"],
            journal_entry_id="journal-entry-1",
            session_id="session-1",
        )
    with pytest.raises(BokioEvidenceError, match="retry_if_failed"):
        server.tools["bokio_attach_evidence"](
            evidence_id=evidence["evidence_id"],
            journal_entry_id="journal-entry-1",
            session_id="session-1",
        )

    result = server.tools["bokio_attach_evidence"](
        evidence_id=evidence["evidence_id"],
        journal_entry_id="journal-entry-1",
        retry_if_failed=True,
        session_id="session-1",
    )

    assert result["association"]["status"] == "attached"
    assert len(requests) == 2


def test_bokio_mcp_marks_missing_evidence_blob_failed_so_attachment_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    store = BokioEvidenceStore(tmp_path)
    evidence, _created = store.ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    evidence_id = evidence["evidence_id"]
    blob_path = store.get_file(evidence_id).blob_path
    blob_path.unlink()
    server = _register_bokio_mcp(tmp_path, allowed_tools=["bokio_attach_evidence"])

    with pytest.raises(FileNotFoundError, match="blob is missing"):
        server.tools["bokio_attach_evidence"](
            evidence_id=evidence_id,
            journal_entry_id="journal-entry-1",
            session_id="session-1",
        )

    failed = store.get(evidence_id)["associations"][0]
    assert failed["status"] == "failed"
    assert "blob is missing" in failed["error"]
    blob_path.write_bytes(b"%PDF-1.7\nreceipt")

    result = server.tools["bokio_attach_evidence"](
        evidence_id=evidence_id,
        journal_entry_id="journal-entry-1",
        retry_if_failed=True,
        session_id="session-1",
    )

    assert result["association"]["status"] == "attached"
    assert len(requests) == 1


def test_bokio_evidence_store_atomically_claims_one_failed_association_retry(
    tmp_path: Path,
) -> None:
    store = BokioEvidenceStore(tmp_path)
    evidence, _created = store.ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    evidence_id = evidence["evidence_id"]
    store.begin_association(
        evidence_id=evidence_id,
        journal_entry_id="journal-entry-1",
    )
    store.mark_association_failed(
        evidence_id=evidence_id,
        journal_entry_id="journal-entry-1",
        error="uncertain upload outcome",
    )
    worker_count = 8
    start = threading.Barrier(worker_count)

    def claim_retry(_index: int) -> str:
        start.wait(timeout=5)
        try:
            association = store.begin_association(
                evidence_id=evidence_id,
                journal_entry_id="journal-entry-1",
                retry_if_failed=True,
            )
        except BokioEvidenceError as exc:
            return str(exc)
        return str(association["status"])

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        outcomes = list(executor.map(claim_retry, range(worker_count)))

    assert outcomes.count("pending") == 1
    assert all(outcome == "pending" or "already pending" in outcome for outcome in outcomes)


def test_bokio_evidence_status_keeps_any_failed_association_visible(
    tmp_path: Path,
) -> None:
    store = BokioEvidenceStore(tmp_path)
    evidence, _created = store.ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    evidence_id = evidence["evidence_id"]
    store.begin_association(
        evidence_id=evidence_id,
        journal_entry_id="journal-entry-failed",
    )
    store.mark_association_failed(
        evidence_id=evidence_id,
        journal_entry_id="journal-entry-failed",
        error="uncertain upload outcome",
    )
    store.begin_association(
        evidence_id=evidence_id,
        journal_entry_id="journal-entry-attached",
    )
    store.mark_association_attached(
        evidence_id=evidence_id,
        journal_entry_id="journal-entry-attached",
        bokio_result={"uploadId": "upload-2"},
    )

    unresolved = store.get(evidence_id)
    assert unresolved["status"] == "needs_attention"
    assert [record["evidence_id"] for record in store.list(status="needs_attention")] == [
        evidence_id
    ]

    store.begin_association(
        evidence_id=evidence_id,
        journal_entry_id="journal-entry-failed",
        retry_if_failed=True,
    )
    store.mark_association_attached(
        evidence_id=evidence_id,
        journal_entry_id="journal-entry-failed",
        bokio_result={"uploadId": "upload-1"},
    )

    assert store.get(evidence_id)["status"] == "attached"
    assert store.list(status="needs_attention") == []


def test_bokio_mcp_creates_entry_and_attaches_evidence_idempotently(
    tmp_path: Path, monkeypatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/journal-entries"):
            return httpx.Response(200, json={"id": "journal-entry-1"})
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    evidence, _created = BokioEvidenceStore(tmp_path).ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_journal_entry_with_evidence"],
    )

    first = server.tools["bokio_create_journal_entry_with_evidence"](
        payload={"title": "OpenAI receipt", "items": []},
        evidence_ids=[evidence["evidence_id"]],
        operation_key="openai-july-receipt",
        session_id="session-1",
    )
    second = server.tools["bokio_create_journal_entry_with_evidence"](
        payload={"title": "OpenAI receipt", "items": []},
        evidence_ids=[evidence["evidence_id"]],
        operation_key="openai-july-receipt",
        session_id="session-1",
    )

    assert first["operation"]["status"] == "completed"
    assert first["operation"]["journal_entry_id"] == "journal-entry-1"
    assert first["operation"]["associations"][0]["status"] == "attached"
    assert second["operation"]["idempotent"] is True
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "journal-entries",
        "uploads",
    ]


def test_bokio_mcp_claims_journal_entry_creation_before_calling_bokio(
    tmp_path: Path, monkeypatch
) -> None:
    create_started = threading.Event()
    release_create = threading.Event()
    request_lock = threading.Lock()
    create_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_requests
        if request.url.path.endswith("/journal-entries"):
            with request_lock:
                create_requests += 1
            create_started.set()
            if not release_create.wait(timeout=5):
                raise AssertionError("timed out waiting to release Bokio create request")
            return httpx.Response(200, json={"id": "journal-entry-1"})
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    evidence, _created = BokioEvidenceStore(tmp_path).ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_journal_entry_with_evidence"],
    )
    arguments = {
        "payload": {"title": "OpenAI receipt", "items": []},
        "evidence_ids": [evidence["evidence_id"]],
        "operation_key": "openai-july-receipt",
        "session_id": "session-1",
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            server.tools["bokio_create_journal_entry_with_evidence"], **arguments
        )
        assert create_started.wait(timeout=5)
        second = executor.submit(
            server.tools["bokio_create_journal_entry_with_evidence"], **arguments
        )
        try:
            with pytest.raises(BokioEvidenceError, match="in flight or its outcome is unknown"):
                second.result(timeout=5)
        finally:
            release_create.set()
        first_result = first.result(timeout=5)

    assert first_result["operation"]["status"] == "completed"
    assert create_requests == 1


def test_bokio_mcp_keeps_pre_request_creation_failures_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BOKIO_COMPANY_ID", raising=False)
    monkeypatch.delenv("BOKIO_INTEGRATION_TOKEN", raising=False)
    evidence, _created = BokioEvidenceStore(tmp_path).ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    evidence_id = evidence["evidence_id"]
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_journal_entry_with_evidence"],
    )
    arguments = {
        "payload": {"title": "OpenAI receipt", "items": []},
        "evidence_ids": [evidence_id],
        "operation_key": "retryable-pre-request-failure",
        "session_id": "session-1",
    }

    with pytest.raises(BokioConfigError, match="company id is required"):
        server.tools["bokio_create_journal_entry_with_evidence"](**arguments)

    operation = BokioEvidenceStore(tmp_path).begin_journal_entry_operation(
        operation_key="retryable-pre-request-failure",
        payload=arguments["payload"],
        evidence_ids=[evidence_id],
    )
    assert operation["status"] == "pending_create"

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/journal-entries"):
            return httpx.Response(200, json={"id": "journal-entry-1"})
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")

    result = server.tools["bokio_create_journal_entry_with_evidence"](**arguments)

    assert result["operation"]["status"] == "completed"
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "journal-entries",
        "uploads",
    ]


def test_bokio_mcp_late_duplicate_failure_cannot_regress_completed_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_started = threading.Event()
    release_upload = threading.Event()
    delayed_mark_started = threading.Event()
    release_delayed_mark = threading.Event()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/journal-entries"):
            return httpx.Response(200, json={"id": "journal-entry-1"})
        upload_started.set()
        if not release_upload.wait(timeout=5):
            raise AssertionError("timed out waiting to release Bokio evidence upload")
        return httpx.Response(200, json={"uploadId": "upload-1"})

    original_mark_needs_attention = BokioEvidenceStore.mark_journal_entry_operation_needs_attention

    def delayed_mark_needs_attention(
        store: BokioEvidenceStore, **kwargs: object
    ) -> dict[str, object]:
        delayed_mark_started.set()
        if not release_delayed_mark.wait(timeout=5):
            raise AssertionError("timed out waiting to release delayed operation update")
        return original_mark_needs_attention(store, **kwargs)

    monkeypatch.setattr(
        BokioEvidenceStore,
        "mark_journal_entry_operation_needs_attention",
        delayed_mark_needs_attention,
    )
    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    evidence, _created = BokioEvidenceStore(tmp_path).ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_journal_entry_with_evidence"],
    )
    arguments = {
        "payload": {"title": "OpenAI receipt", "items": []},
        "evidence_ids": [evidence["evidence_id"]],
        "operation_key": "concurrent-evidence-operation",
        "session_id": "session-1",
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(
            server.tools["bokio_create_journal_entry_with_evidence"], **arguments
        )
        assert upload_started.wait(timeout=5)
        duplicate = executor.submit(
            server.tools["bokio_create_journal_entry_with_evidence"], **arguments
        )
        assert delayed_mark_started.wait(timeout=5)
        try:
            release_upload.set()
            owner_result = owner.result(timeout=5)
        finally:
            release_delayed_mark.set()
            release_upload.set()
        with pytest.raises(BokioEvidenceError, match="already pending"):
            duplicate.result(timeout=5)

    repeated = server.tools["bokio_create_journal_entry_with_evidence"](**arguments)

    assert owner_result["operation"]["status"] == "completed"
    assert repeated["operation"]["status"] == "completed"
    assert repeated["operation"]["idempotent"] is True
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "journal-entries",
        "uploads",
    ]


def test_bokio_mcp_resumes_evidence_after_entry_create_without_recreating_entry(
    tmp_path: Path, monkeypatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/journal-entries"):
            return httpx.Response(200, json={"id": "journal-entry-1"})
        if sum(item.url.path.endswith("/uploads") for item in requests) == 1:
            return httpx.Response(503, json={"message": "temporarily unavailable"})
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    evidence, _created = BokioEvidenceStore(tmp_path).ingest_bytes(
        content=b"%PDF-1.7\nreceipt",
        filename="receipt.pdf",
        source_type="communication_artifact",
        source_key="comm_receipt",
        mime_type="application/pdf",
    )
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_journal_entry_with_evidence"],
    )
    arguments = {
        "payload": {"title": "OpenAI receipt", "items": []},
        "evidence_ids": [evidence["evidence_id"]],
        "operation_key": "openai-july-receipt",
        "session_id": "session-1",
    }

    with pytest.raises(BokioApiError, match="temporarily unavailable"):
        server.tools["bokio_create_journal_entry_with_evidence"](**arguments)
    with pytest.raises(BokioEvidenceError, match="retry_if_failed"):
        server.tools["bokio_create_journal_entry_with_evidence"](**arguments)

    result = server.tools["bokio_create_journal_entry_with_evidence"](
        **arguments,
        retry_if_failed=True,
    )

    assert result["operation"]["status"] == "completed"
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "journal-entries",
        "uploads",
        "uploads",
    ]


def test_bokio_mcp_read_tool_requires_session_tool_permission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"companyInformation": {"name": "Example AB"}})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")

    denied_server = _register_bokio_mcp(tmp_path / "denied", allowed_tools=[])
    with pytest.raises(ToolError, match="bokio_get_company"):
        denied_server.tools["bokio_get_company"](session_id="session-1")

    assert requests == []

    allowed_server = _register_bokio_mcp(
        tmp_path / "allowed",
        allowed_tools=["bokio_get_company"],
    )
    result = allowed_server.tools["bokio_get_company"](session_id="session-1")

    assert result == {"result": {"companyInformation": {"name": "Example AB"}}}
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer secret-token"


def test_bokio_mcp_prepare_tool_requires_session_tool_permission(tmp_path: Path) -> None:
    denied_root = tmp_path / "denied"
    denied_server = _register_bokio_mcp(denied_root, allowed_tools=[])

    with pytest.raises(ToolError, match="bokio_prepare_journal_entry"):
        denied_server.tools["bokio_prepare_journal_entry"](
            payload={"title": "Receipt", "items": []},
            session_id="session-1",
        )

    assert PreparedActionStore(denied_root).list_actions() == []

    allowed_root = tmp_path / "allowed"
    allowed_server = _register_bokio_mcp(
        allowed_root,
        allowed_tools=["bokio_prepare_journal_entry"],
    )
    result = allowed_server.tools["bokio_prepare_journal_entry"](
        payload={"title": "Receipt", "items": []},
        session_id="session-1",
    )

    assert result["action"]["kind"] == "journal_entry"
    assert result["action"]["source"] == "session-1"
    assert PreparedActionStore(allowed_root).list_actions()[0]["source"] == "session-1"


def test_bokio_mcp_upload_tool_requires_policy_before_client_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")

    denied_root = tmp_path / "denied"
    denied_upload_root = upload_staging_dir(denied_root, session_id="session-1")
    denied_upload_root.mkdir(parents=True)
    (denied_upload_root / "receipt.pdf").write_bytes(b"%PDF-1.7\nreceipt")
    denied_server = _register_bokio_mcp(denied_root, allowed_tools=[])

    with pytest.raises(ToolError, match="bokio_upload_document"):
        denied_server.tools["bokio_upload_document"](
            file_path="receipt.pdf",
            session_id="session-1",
        )

    assert requests == []

    allowed_root = tmp_path / "allowed"
    allowed_upload_root = upload_staging_dir(allowed_root, session_id="session-1")
    allowed_upload_root.mkdir(parents=True)
    (allowed_upload_root / "receipt.pdf").write_bytes(b"%PDF-1.7\nreceipt")
    allowed_server = _register_bokio_mcp(
        allowed_root,
        allowed_tools=["bokio_upload_document"],
    )
    result = allowed_server.tools["bokio_upload_document"](
        file_path="receipt.pdf",
        session_id="session-1",
    )

    assert result == {"result": {"uploadId": "upload-1"}}
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer secret-token"


def test_bokio_mcp_approved_write_tool_checks_policy_before_client_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "journal-entry-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    store = PreparedActionStore(tmp_path)
    action = store.create_action(
        kind="journal_entry",
        payload={"title": "Receipt", "items": []},
        source="session-1",
    )
    store.approve_action(action["action_id"])

    denied_server = _register_bokio_mcp(tmp_path, allowed_tools=[])
    with pytest.raises(ToolError, match="bokio_create_approved_journal_entry"):
        denied_server.tools["bokio_create_approved_journal_entry"](
            prepared_action_id=action["action_id"],
            session_id="session-1",
        )

    assert requests == []
    assert store.get_action(action["action_id"])["status"] == "approved"

    allowed_server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_approved_journal_entry"],
    )
    result = allowed_server.tools["bokio_create_approved_journal_entry"](
        prepared_action_id=action["action_id"],
        session_id="session-1",
    )

    assert result["result"] == {"id": "journal-entry-1"}
    assert store.get_action(action["action_id"])["status"] == "executed"
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer secret-token"


def test_bokio_mcp_creates_supplier_invoice_once_for_an_exact_operation_key_repeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "supplier-invoice-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_supplier_invoice"],
    )
    arguments = {
        "payload": {"supplierId": "supplier-1", "total": 1250},
        "operation_key": "supplier-1-invoice-2026-07",
        "session_id": "session-1",
    }

    first = server.tools["bokio_create_supplier_invoice"](**arguments)
    repeated = server.tools["bokio_create_supplier_invoice"](**arguments)

    assert first["operation"]["status"] == "succeeded"
    assert repeated["operation"]["idempotent"] is True
    assert repeated["operation"]["result"] == {"id": "supplier-invoice-1"}
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/supplier-invoices")
    operation = BokioOperationStore(tmp_path).list()[0]
    assert operation["attempt_count"] == 1
    assert operation["session_id"] == "session-1"


def test_bokio_mcp_rejects_supplier_operation_key_reuse_for_a_changed_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "supplier-invoice-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_supplier_invoice"],
    )
    server.tools["bokio_create_supplier_invoice"](
        payload={"supplierId": "supplier-1", "total": 1250},
        operation_key="supplier-invoice-key",
        session_id="session-1",
    )

    with pytest.raises(BokioOperationError, match="different Bokio request"):
        server.tools["bokio_create_supplier_invoice"](
            payload={"supplierId": "supplier-1", "total": 2500},
            operation_key="supplier-invoice-key",
            session_id="session-1",
        )

    assert len(requests) == 1


def test_bokio_mcp_claims_supplier_invoice_before_the_external_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_started = threading.Event()
    release_create = threading.Event()
    request_count = 0
    request_lock = threading.Lock()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        with request_lock:
            request_count += 1
        create_started.set()
        if not release_create.wait(timeout=5):
            raise AssertionError("timed out waiting to release supplier invoice creation")
        return httpx.Response(200, json={"id": "supplier-invoice-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_supplier_invoice"],
    )
    arguments = {
        "payload": {"supplierId": "supplier-1", "total": 1250},
        "operation_key": "concurrent-supplier-invoice",
        "session_id": "session-1",
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(server.tools["bokio_create_supplier_invoice"], **arguments)
        assert create_started.wait(timeout=5)
        duplicate = executor.submit(
            server.tools["bokio_create_supplier_invoice"], **arguments
        )
        try:
            with pytest.raises(BokioOperationError, match="already executing"):
                duplicate.result(timeout=5)
        finally:
            release_create.set()
        result = owner.result(timeout=5)

    assert result["operation"]["status"] == "succeeded"
    assert request_count == 1


def test_bokio_mcp_recovers_an_executing_supplier_operation_after_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "supplier-invoice-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_supplier_invoice"],
    )
    arguments = {
        "payload": {"supplierId": "supplier-1", "total": 1250},
        "operation_key": "interrupted-supplier-invoice",
        "session_id": "session-1",
    }
    store = BokioOperationStore(tmp_path)
    store.begin(
        session_id="session-1",
        operation_kind="supplier_invoice",
        operation_key=arguments["operation_key"],
        payload=arguments["payload"],
    )
    store.claim(
        session_id="session-1",
        operation_kind="supplier_invoice",
        operation_key=arguments["operation_key"],
    )

    with pytest.raises(BokioOperationError, match="confirm_not_created"):
        server.tools["bokio_create_supplier_invoice"](**arguments)
    assert requests == []

    recovered = server.tools["bokio_create_supplier_invoice"](
        **arguments,
        confirm_not_created=True,
    )

    assert recovered["operation"]["status"] == "succeeded"
    assert recovered["operation"]["attempt_count"] == 2
    assert len(requests) == 1


def test_bokio_mcp_requires_reconciliation_before_retrying_an_uncertain_supplier_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, json={"message": "upstream unavailable"})
        return httpx.Response(200, json={"id": "supplier-invoice-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_supplier_invoice"],
    )
    arguments = {
        "payload": {"supplierId": "supplier-1", "total": 1250},
        "operation_key": "uncertain-supplier-invoice",
        "session_id": "session-1",
    }

    with pytest.raises(BokioApiError, match="upstream unavailable"):
        server.tools["bokio_create_supplier_invoice"](**arguments)
    assert BokioOperationStore(tmp_path).list()[0]["status"] == "needs_attention"

    with pytest.raises(BokioOperationError, match="confirm_not_created"):
        server.tools["bokio_create_supplier_invoice"](**arguments, retry_if_failed=True)
    assert len(requests) == 1

    retried = server.tools["bokio_create_supplier_invoice"](
        **arguments,
        confirm_not_created=True,
    )

    assert retried["operation"]["status"] == "succeeded"
    assert retried["operation"]["attempt_count"] == 2
    assert len(requests) == 2


def test_bokio_mcp_treats_an_unparsable_successful_write_as_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, content=b"accepted but not json")
        return httpx.Response(200, json={"id": "supplier-invoice-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_supplier_invoice"],
    )
    arguments = {
        "payload": {"supplierId": "supplier-1", "total": 1250},
        "operation_key": "unparsable-success-supplier-invoice",
        "session_id": "session-1",
    }

    with pytest.raises(BokioApiError, match="non-JSON response"):
        server.tools["bokio_create_supplier_invoice"](**arguments)
    assert BokioOperationStore(tmp_path).list()[0]["status"] == "needs_attention"
    with pytest.raises(BokioOperationError, match="confirm_not_created"):
        server.tools["bokio_create_supplier_invoice"](**arguments, retry_if_failed=True)
    assert len(requests) == 1

    retried = server.tools["bokio_create_supplier_invoice"](
        **arguments,
        confirm_not_created=True,
    )

    assert retried["operation"]["status"] == "succeeded"
    assert len(requests) == 2


def test_bokio_mcp_requires_explicit_retry_after_a_definite_supplier_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(400, json={"message": "invalid supplier invoice"})
        return httpx.Response(200, json={"id": "supplier-invoice-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_supplier_invoice"],
    )
    arguments = {
        "payload": {"supplierId": "supplier-1", "total": 1250},
        "operation_key": "rejected-supplier-invoice",
        "session_id": "session-1",
    }

    with pytest.raises(BokioApiError, match="invalid supplier invoice"):
        server.tools["bokio_create_supplier_invoice"](**arguments)
    assert BokioOperationStore(tmp_path).list()[0]["status"] == "failed"
    with pytest.raises(BokioOperationError, match="retry_if_failed"):
        server.tools["bokio_create_supplier_invoice"](**arguments)

    retried = server.tools["bokio_create_supplier_invoice"](
        **arguments,
        retry_if_failed=True,
    )

    assert retried["operation"]["status"] == "succeeded"
    assert retried["operation"]["attempt_count"] == 2
    assert len(requests) == 2


@pytest.mark.parametrize("exception_type", [httpx.ConnectError, httpx.ConnectTimeout])
def test_bokio_mcp_keeps_connection_stage_supplier_failures_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[httpx.HTTPError],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise exception_type("connection failed", request=request)
        return httpx.Response(200, json={"id": "supplier-invoice-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_supplier_invoice"],
    )
    arguments = {
        "payload": {"supplierId": "supplier-1", "total": 1250},
        "operation_key": exception_type.__name__,
        "session_id": "session-1",
    }

    with pytest.raises(exception_type, match="connection failed"):
        server.tools["bokio_create_supplier_invoice"](**arguments)
    assert BokioOperationStore(tmp_path).list()[0]["status"] == "failed"
    with pytest.raises(BokioOperationError, match="retry_if_failed"):
        server.tools["bokio_create_supplier_invoice"](**arguments)

    retried = server.tools["bokio_create_supplier_invoice"](
        **arguments,
        retry_if_failed=True,
    )

    assert retried["operation"]["status"] == "succeeded"
    assert len(requests) == 2


def test_prepared_action_execution_claim_is_atomic_and_requires_reconciliation(
    tmp_path: Path,
) -> None:
    store = PreparedActionStore(tmp_path)
    action = store.create_action(
        kind="supplier_invoice",
        payload={"supplierId": "supplier-1"},
        source="session-1",
    )

    claimed, owns_execution = store.claim_execution(action["action_id"], approve=True)

    assert owns_execution is True
    assert claimed["status"] == "executing"
    with pytest.raises(ValueError, match="executing or has an unknown outcome"):
        store.claim_execution(action["action_id"], approve=True)
    attention = store.mark_needs_attention(
        action["action_id"], error={"error": "request timed out"}
    )
    assert attention["status"] == "needs_attention"
    with pytest.raises(ValueError, match="confirm it was not created"):
        store.claim_execution(action["action_id"], retry=True)
    retried, owns_retry = store.claim_execution(
        action["action_id"],
        retry=True,
        confirm_not_created=True,
    )
    assert owns_retry is True
    assert retried["status"] == "executing"
    assert [entry["status"] for entry in retried["history"]] == [
        "pending_approval",
        "executing",
        "needs_attention",
        "executing",
    ]


def test_prepared_action_recovers_an_executing_claim_after_reconciliation(
    tmp_path: Path,
) -> None:
    store = PreparedActionStore(tmp_path)
    action = store.create_action(
        kind="supplier_invoice",
        payload={"supplierId": "supplier-1"},
        source="session-1",
    )
    store.claim_execution(action["action_id"], approve=True)

    with pytest.raises(ValueError, match="confirm it was not created"):
        store.claim_execution(action["action_id"], retry=True)

    recovered, owns_recovery = store.claim_execution(
        action["action_id"],
        retry=True,
        confirm_not_created=True,
    )

    assert owns_recovery is True
    assert recovered["status"] == "executing"
    assert [entry["status"] for entry in recovered["history"]] == [
        "pending_approval",
        "executing",
        "executing",
    ]
    assert recovered["history"][-1]["detail"].startswith("Execution recovered")


def test_bokio_ui_tracks_live_privilege_and_executing_recovery_state() -> None:
    ui_js = (
        Path(__file__).parents[1] / "src" / "evander_bokio_addon" / "ui_static" / "bokio-ui.js"
    ).read_text(encoding="utf-8")

    assert 'let privilegedUnlocked = pageParams.get("privileged_unlocked") === "true";' in ui_js
    assert 'data.type !== "admin-privileged-operations"' in ui_js
    assert "setPrivilegedUnlocked(Boolean(data.privileged_operations?.unlocked));" in ui_js
    assert 'action.status === "needs_attention" || action.status === "executing"' in ui_js
    assert 'runActionMutation(action, "retry", { confirm_not_created: true })' in ui_js


def test_bokio_workflow_provider_uses_full_gateway_provider_state(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BOKIO_CUSTOM_COMPANY", "company-from-custom-env")
    monkeypatch.setenv("BOKIO_CUSTOM_TOKEN", "token-from-custom-env")
    config = GatewayConfig(
        runtime_dir=tmp_path,
        provider_state={
            "bokio": {
                "company_id_env": "BOKIO_CUSTOM_COMPANY",
                "integration_token_env": "BOKIO_CUSTOM_TOKEN",
            }
        },
    )

    class Resolver:
        directory_path: str | None = None

        def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
            self.directory_path = directory_path
            return config

    resolver = Resolver()
    provider = BokioCompanyQueryProvider()
    provider.runtime_context = SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=tmp_path),
        provider_config_resolver=resolver,
        metadata={"directory_path": "root/books"},
    )

    settings = settings_from_context(provider._context())

    assert resolver.directory_path == "root/books"
    assert settings.company_id == "company-from-custom-env"
    assert settings.integration_token == "token-from-custom-env"
    assert settings.company_id_env == "BOKIO_CUSTOM_COMPANY"
    assert settings.token_env == "BOKIO_CUSTOM_TOKEN"


def test_prepared_action_store_transitions_without_secrets(tmp_path) -> None:
    store = PreparedActionStore(tmp_path)

    action = store.create_action(
        kind="journal_entry",
        payload={"title": "Receipt", "items": []},
        source="session-1",
        note="needs review",
    )
    approved = store.approve_action(action["action_id"])
    executed = store.mark_executed(action["action_id"], result={"id": "entry-1"})

    assert approved["status"] == "approved"
    assert executed["status"] == "executed"
    assert executed["result"] == {"id": "entry-1"}
    state_text = (tmp_path / "addons" / "bokio-addon" / "prepared-actions.json").read_text(
        encoding="utf-8"
    )
    assert "secret-token" not in state_text
    assert json.loads(state_text)["actions"][action["action_id"]]["payload"]["title"] == "Receipt"


def _install_transport(monkeypatch, handler) -> None:
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


class FakeServer:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(func):
            self.tools[kwargs.get("name") or func.__name__] = func
            return func

        if args and callable(args[0]):
            return decorator(args[0])
        return decorator


def _register_bokio_mcp(tmp_path: Path, *, allowed_tools: list[str]) -> FakeServer:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    sessions = JsonSessionManifestRepository(config.session_store_path)
    approvals = JsonStore(config.approval_store_path, ApprovalRequest)
    policy = PolicyEngine(
        config,
        sessions,
        approvals,
        extra_mcp_tool_names=list(MCP_TOOL_NAMES),
    )
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=list(allowed_tools),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )
    runtime = SimpleNamespace(policy=policy)
    server = FakeServer()
    register_mcp_tools(
        server,
        AddonContext(
            addon_id="bokio-addon",
            runtime_dir=tmp_path,
            addon_runtime_dir=tmp_path / "addons" / "bokio-addon",
            config=config,
            service=runtime,
        ),
    )
    return server
