from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.admin.services.approval_audit_service import (
    ApprovalAuditAdminDependencies,
    ApprovalAuditAdminService,
)
from evander.models import ApprovalRequest


def _service(path, monkeypatch):
    service = ApprovalAuditAdminService(
        ApprovalAuditAdminDependencies(
            runtime=None,
            config=SimpleNamespace(audit_log_path=path),
            tool_call_audit_index=None,
            directory_path=None,
        )
    )
    request = ApprovalRequest(
        request_id="approval",
        session_id="session",
        action_type="tool",
        payload_digest="digest",
        human_summary="Review",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    monkeypatch.setattr(service, "get_approval_request", lambda _: request)
    return service


def test_approval_review_reads_only_recent_blocks_and_preserves_context(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    unrelated = json.dumps({"session_id": "other", "padding": "x" * 500}) + "\n"
    recent = [
        {"approval_request_id": "approval", "sequence": number, "token": "secret"}
        for number in range(10)
    ]
    path.write_text(
        unrelated * 12000
        + "\n".join(json.dumps(entry) for entry in recent)
        + '\n{broken}\n[]\n{"session_id":"other"}\n',
        encoding="utf-8",
    )
    service = _service(path, monkeypatch)
    original_open = Path.open
    bytes_read = []

    class MeasuredReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def read(self, *args):
            value = self.handle.read(*args)
            bytes_read.append(len(value if isinstance(value, bytes) else value.encode("utf-8")))
            return value

    def measured_open(self, *args, **kwargs):
        handle = original_open(self, *args, **kwargs)
        return MeasuredReader(handle) if self == path else handle

    monkeypatch.setattr(Path, "open", measured_open)
    review = service.get_approval_review("approval")
    assert [entry["sequence"] for entry in review["audit_context"]] == list(range(2, 10))
    assert all(entry["token"] == "<redacted>" for entry in review["audit_context"])
    assert sum(bytes_read) <= 2 * 64 * 1024


@pytest.mark.parametrize("newline", ["", "\n"])
def test_approval_review_preserves_unicode_long_records_and_correlation(
    tmp_path, monkeypatch, newline
):
    path = tmp_path / "audit.jsonl"
    entries = [
        {"session_id": "session", "sequence": 0},
        {"args": {"request_id": "approval"}, "sequence": 1},
        {"result": {"payload_digest": "digest"}, "sequence": 2, "message": "å" * 70000},
    ]
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + newline)
    review = _service(path, monkeypatch).get_approval_review("approval")
    assert review["audit_context"] == entries


@pytest.mark.parametrize("failure", ["missing", "unicode", "read"])
def test_approval_review_returns_empty_context_on_unavailable_audit(tmp_path, monkeypatch, failure):
    path = tmp_path / "audit.jsonl"
    service = _service(path, monkeypatch)
    if failure == "unicode":
        path.write_bytes(b'\xff\n{"approval_request_id":"approval"}\n')
    elif failure == "read":
        path.write_text('{"approval_request_id":"approval"}\n')
        original_open = Path.open

        def failed_open(self, *args, **kwargs):
            if self == path:
                raise OSError("unavailable")
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", failed_open)
    assert service.get_approval_review("approval")["audit_context"] == []


def test_approval_review_does_not_decode_old_corruption_after_context_is_complete(
    tmp_path, monkeypatch
):
    path = tmp_path / "audit.jsonl"
    entries = [{"approval_request_id": "approval", "sequence": index} for index in range(8)]
    path.write_bytes(b"\xff\n" + ("\n".join(map(json.dumps, entries)) + "\n").encode("utf-8"))
    assert _service(path, monkeypatch).get_approval_review("approval")["audit_context"] == entries
