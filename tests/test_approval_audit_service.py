import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.admin.services.approval_audit_service import (
    ApprovalAuditAdminDependencies,
    ApprovalAuditAdminService,
)


@pytest.mark.parametrize("recent_only", [False, True])
def test_audit_scan_classifies_invalid_lines_and_preserves_page_counts(
    tmp_path: Path, recent_only: bool
) -> None:
    audit_log_path = tmp_path / "audit.jsonl"
    audit_log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "tool_call",
                        "tool_name": "older_tool",
                        "decision": "denied",
                        "created_at": "2026-05-18T10:00:00Z",
                    }
                ),
                "  ",
                "{broken-json}",
                "[]",
                json.dumps({"event_type": "approval_decision"}),
                json.dumps({"event_type": "tool_call", "created_at": "invalid"}),
                json.dumps(
                    {
                        "event_type": " TOOL_CALL ",
                        "tool_name": "newer_tool",
                        "decision": "allowed",
                        "created_at": "2026-05-18T11:00:00Z",
                        "args": {"token": "must-be-redacted"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    service = ApprovalAuditAdminService(
        ApprovalAuditAdminDependencies(
            runtime=None,
            config=SimpleNamespace(audit_log_path=audit_log_path),
            tool_call_audit_index=None,
            directory_path=None,
        )
    )

    page = service.list_tool_call_audit_entries(limit=1, recent_only=recent_only)

    assert [entry.tool_name for entry in page.items] == ["newer_tool"]
    assert page.items[0].args == {"token": "<redacted>"}
    assert page.has_more is True
    assert page.counts.scanned == 6
    assert page.counts.skipped_malformed == 3
    assert page.counts.skipped_non_tool_call == 1
    assert page.counts.matched == 2
    assert page.counts.returned == 1
    assert page.counts.status_counts == (
        {"success": 1} if recent_only else {"denied": 1, "success": 1}
    )
