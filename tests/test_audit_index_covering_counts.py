from datetime import UTC, datetime
from pathlib import Path
import sqlite3

import pytest

from evander.repositories.audit_index import ToolCallAuditIndex
from evander.repositories import audit_index as audit_index_module


@pytest.mark.parametrize("direct_only", [False, True])
def test_directory_status_counts_use_covering_index(tmp_path: Path, direct_only: bool, monkeypatch):
    index = ToolCallAuditIndex(tmp_path / "audit.db")
    for position, (directory, status) in enumerate(
        [
            ("root", "success"),
            ("root", "denied"),
            ("root", "denied"),
            ("root/child", "error"),
            ("root/other", "success"),
        ]
    ):
        assert index.index_payload(
            {
                "event_type": "tool_call",
                "directory_path": directory,
                "tool_name": "sample_tool",
                "status": status,
                "request_id": str(position),
                "created_at": f"2026-09-{21 if position == 4 else 22}T10:00:00+00:00",
                "result": {"large_payload": "x" * 20000},
            }
        )
    # Exercise upgrade of an existing populated index, not just a fresh database.
    with sqlite3.connect(index.path) as connection:
        connection.execute("DROP INDEX idx_tool_call_audit_directory_status")
        connection.execute(
            "UPDATE audit_index_metadata SET value_json = '{\"version\":2}' "
            "WHERE key = 'schema_version'"
        )
    index = ToolCallAuditIndex(index.path)
    statements = []
    original_connect = audit_index_module.connect_audit_index_db

    def connect(path):
        connection = original_connect(path)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(audit_index_module, "connect_audit_index_db", connect)
    page = index.query_tool_calls(
        session_id=None,
        tool_name=None,
        status=None,
        directory_path="root",
        direct_only=direct_only,
        created_after=datetime(2026, 9, 22, tzinfo=UTC),
        created_before=None,
        q=None,
        limit=1,
    )
    expected = {"success": 1, "denied": 2}
    if not direct_only:
        expected["error"] = 1
    assert page.status_counts == expected
    assert page.matched == sum(expected.values())
    assert page.returned == 1
    assert page.has_more
    with sqlite3.connect(index.path) as connection:
        count_sql = next(sql for sql in statements if "SELECT status, COUNT(*)" in sql)
        plan = connection.execute(
            f"EXPLAIN QUERY PLAN {count_sql}",
        ).fetchall()
    assert any("COVERING INDEX idx_tool_call_audit_directory_status" in row[3] for row in plan)
