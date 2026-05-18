from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import create_app


def write_config(tmp_path: Path) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(
        "\n".join(
            [
                f'runtime_dir: "{runtime_dir}"',
                f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
                f'session_store_path: "{runtime_dir / "sessions.json"}"',
                f'approval_store_path: "{runtime_dir / "approvals.json"}"',
                f'draft_store_path: "{runtime_dir / "drafts.json"}"',
                f'email_store_path: "{runtime_dir / "emails.json"}"',
                f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _write_audit_log(path: Path) -> None:
    audit_lines = [
        {
            "event_type": "tool_call",
            "session_id": "session-1",
            "vm_id": "vm-1",
            "directory_path": "root/team",
            "agent_role": "automation",
            "actor_user_id": "user-1",
            "tool_name": "email_send_approved",
            "request_id": "request-1",
            "correlation_id": "correlation-1",
            "approval_request_id": "approval-1",
            "workflow_run_id": "run-1",
            "trace_id": "trace-1",
            "duration_ms": 42,
            "decision": "allowed",
            "args": {
                "session_id": "session-1",
                "request_id": "request-1",
                "token": "super-secret",
                "nested": {"password": "nested-secret"},
            },
            "result": {
                "request_id": "request-1",
                "secret": "result-secret",
                "status": "success",
            },
            "created_at": "2026-05-18T10:00:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": "session-1",
            "vm_id": "vm-1",
            "directory_path": "root/team/sub",
            "tool_name": "make_phone_call",
            "request_id": "request-2",
            "decision": "denied",
            "reason": "blocked",
            "args": {
                "session_id": "session-1",
                "request_id": "request-2",
                "secret": "blocked-secret",
            },
            "result": {
                "request_id": "request-2",
                "status": "denied",
                "error": "blocked",
            },
            "created_at": "2026-05-18T10:02:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": "session-2",
            "vm_id": "vm-2",
            "directory_path": "root/other",
            "tool_name": "prompt_agent",
            "correlation_id": "correlation-2",
            "decision": "error",
            "reason": "boom",
            "args": {
                "session_id": "session-2",
                "correlation_id": "correlation-2",
                "password": "error-secret",
            },
            "result": {
                "status": "error",
                "error": "boom",
                "error_class": "tool_dispatch_error",
            },
            "created_at": "2026-05-18T10:04:00+00:00",
        },
        {"event_type": "approval_decision", "session_id": "session-1", "created_at": "2026-05-18T10:05:00+00:00"},
    ]
    path.write_text(
        "\n".join(
            [
                "{not-json}",
                *[json.dumps(entry, sort_keys=True) for entry in audit_lines],
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_admin_service_lists_tool_call_audit_entries_with_filters_and_redaction(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    _write_audit_log(service.config.audit_log_path)

    default_page = service.list_tool_call_audit_entries(limit=2)
    team_page = service.list_tool_call_audit_entries(directory_path="root/team")
    team_direct_page = service.list_tool_call_audit_entries(
        directory_path="root/team", direct_only=True
    )
    session_tool_status_page = service.list_tool_call_audit_entries(
        session_id="session-1",
        tool_name="email_send_approved",
        status="success",
    )
    time_window_page = service.list_tool_call_audit_entries(
        created_after=datetime(2026, 5, 18, 10, 1, tzinfo=UTC),
        created_before=datetime(2026, 5, 18, 10, 3, tzinfo=UTC),
    )
    correlation_page = service.list_tool_call_audit_entries(q="correlation-2")
    redaction_search_page = service.list_tool_call_audit_entries(q="super-secret")

    assert default_page.limit == 2
    assert default_page.counts.scanned == 5
    assert default_page.counts.skipped_malformed == 1
    assert default_page.counts.skipped_non_tool_call == 1
    assert default_page.counts.matched == 3
    assert default_page.counts.returned == 2
    assert default_page.counts.status_counts == {"denied": 1, "error": 1, "success": 1}
    assert [item.tool_name for item in default_page.items] == [
        "prompt_agent",
        "make_phone_call",
    ]
    assert default_page.items[0].status == "error"
    assert default_page.items[0].error_summary == "boom"
    assert default_page.items[0].related_links[0].kind == "session"
    assert {"session", "vm"}.issubset({link.kind for link in default_page.items[0].related_links})
    assert default_page.items[1].status == "denied"
    assert default_page.items[1].error_summary == "blocked"
    assert default_page.items[1].args["secret"] == "<redacted>"
    assert default_page.items[1].result["error"] == "blocked"

    assert [item.tool_name for item in team_page.items] == [
        "make_phone_call",
        "email_send_approved",
    ]
    assert [item.tool_name for item in team_direct_page.items] == ["email_send_approved"]
    assert team_direct_page.items[0].directory_path == "root/team"
    assert team_direct_page.items[0].args["token"] == "<redacted>"
    assert team_direct_page.items[0].result["secret"] == "<redacted>"
    assert {
        "approval_request",
        "session",
        "vm",
        "workflow_run",
        "workflow_trace",
    }.issubset({link.kind for link in team_direct_page.items[0].related_links})
    assert session_tool_status_page.counts.matched == 1
    assert session_tool_status_page.items[0].duration_ms == 42
    assert session_tool_status_page.items[0].decision == "allowed"
    assert session_tool_status_page.items[0].request_id == "request-1"
    assert session_tool_status_page.items[0].correlation_id == "correlation-1"
    assert session_tool_status_page.items[0].approval_request_id == "approval-1"
    assert session_tool_status_page.items[0].related_links[0].href == "/api/admin/sessions/session-1"
    assert [item.tool_name for item in time_window_page.items] == ["make_phone_call"]
    assert time_window_page.items[0].directory_path == "root/team/sub"
    assert [item.tool_name for item in correlation_page.items] == ["prompt_agent"]
    assert correlation_page.items[0].status == "error"
    assert correlation_page.items[0].error_summary == "boom"
    assert correlation_page.items[0].related_links[0].kind == "session"
    assert correlation_page.items[0].related_links[1].kind == "vm"
    assert redaction_search_page.items == []
    assert "super-secret" not in json.dumps(default_page.model_dump(mode="json"))
    assert "result-secret" not in json.dumps(default_page.model_dump(mode="json"))


def test_admin_web_tool_calls_route_returns_empty_payload_when_audit_log_is_missing(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    response = client.get("/api/admin/tool-calls")
    invalid_status_response = client.get("/api/admin/tool-calls?status=maybe")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["counts"]["scanned"] == 0
    assert payload["counts"]["matched"] == 0
    assert payload["counts"]["returned"] == 0
    assert payload["limit"] == 100
    assert payload["has_more"] is False
    assert invalid_status_response.status_code == 400
    assert invalid_status_response.json()["error"] == "status must be one of: success, denied, or error"
