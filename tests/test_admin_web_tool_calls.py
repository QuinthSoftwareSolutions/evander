from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from starlette.testclient import TestClient

from evander.admin import composition as admin_composition
from evander.admin.composition import build_admin_runtime
from evander.admin.web.app import create_app
from evander.audit import AuditLogger
from evander.models import AuditEvent
from evander.repositories.audit_index import ToolCallAuditIndex, audit_index_path_for_log_path


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
                "approval_diagnostic": {
                    "approval_request_id": "approval-1",
                    "rejection_kind": "payload_mismatch",
                    "approved_payload_digest": "digest-1",
                    "attempted_payload_digest": "attempted-digest",
                    "approved_payload": {
                        "token": "approved-secret",
                        "nested": {"password": "approved-nested-secret"},
                    },
                    "attempted_payload": {
                        "token": "attempted-secret",
                        "nested": {"password": "attempted-nested-secret"},
                    },
                            "differences": {
                                "changed": [
                                    {
                                        "path": "payload.token",
                                        "approved_value": "<redacted>",
                                        "attempted_value": "<redacted>",
                                    }
                                ]
                            },
                        },
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
        {
            "event_type": "approval_decision",
            "session_id": "session-1",
            "created_at": "2026-05-18T10:05:00+00:00",
        },
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
    service = build_admin_runtime(write_config(tmp_path))
    _write_audit_log(service.config.audit_log_path)

    default_page = service.approval_audit_admin.list_tool_call_audit_entries(limit=2)
    team_page = service.approval_audit_admin.list_tool_call_audit_entries(directory_path="root/team")
    team_direct_page = service.approval_audit_admin.list_tool_call_audit_entries(
        directory_path="root/team", direct_only=True
    )
    session_tool_status_page = service.approval_audit_admin.list_tool_call_audit_entries(
        session_id="session-1",
        tool_name="email_send_approved",
        status="success",
    )
    time_window_page = service.approval_audit_admin.list_tool_call_audit_entries(
        created_after=datetime(2026, 5, 18, 10, 1, tzinfo=UTC),
        created_before=datetime(2026, 5, 18, 10, 3, tzinfo=UTC),
    )
    correlation_page = service.approval_audit_admin.list_tool_call_audit_entries(
        q="correlation-2"
    )
    redaction_search_page = service.approval_audit_admin.list_tool_call_audit_entries(
        q="super-secret"
    )

    assert default_page.limit == 2
    assert default_page.counts.scanned == 3
    assert default_page.counts.skipped_malformed == 0
    assert default_page.counts.skipped_non_tool_call == 0
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
    assert team_page.items[1].approval_diagnostic is not None
    assert team_page.items[1].approval_diagnostic["approval_request_id"] == "approval-1"
    assert team_page.items[1].approval_diagnostic["attempted_payload"]["token"] == "<redacted>"
    assert team_page.items[1].approval_diagnostic["attempted_payload"]["nested"]["password"] == "<redacted>"
    assert team_page.items[1].approval_diagnostic["differences"]["changed"][0]["attempted_value"] == "<redacted>"
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
    assert (
        session_tool_status_page.items[0].related_links[0].href == "/api/admin/sessions/session-1"
    )
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


def test_admin_service_tool_call_recent_only_reads_from_log_tail(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    entries = [
        {
            "event_type": "tool_call",
            "session_id": f"old-session-{index}",
            "directory_path": "root/archive",
            "tool_name": "old_tool",
            "decision": "allowed",
            "created_at": f"2026-05-18T09:{index % 60:02d}:00+00:00",
        }
        for index in range(200)
    ]
    entries.extend(
        {
            "event_type": "tool_call",
            "session_id": f"session-{index}",
            "directory_path": "root/team",
            "tool_name": f"tool_{index}",
            "decision": "allowed" if index % 2 else "denied",
            "created_at": f"2026-05-18T10:{index:02d}:00+00:00",
        }
        for index in range(10)
    )
    service.config.audit_log_path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )

    page = service.approval_audit_admin.list_tool_call_audit_entries(
        directory_path="root/team",
        direct_only=True,
        limit=5,
        recent_only=True,
    )

    assert [item.tool_name for item in page.items] == [
        "tool_9",
        "tool_8",
        "tool_7",
        "tool_6",
        "tool_5",
    ]
    assert page.has_more is True
    assert page.counts.scanned == 10
    assert page.counts.matched == 10
    assert page.counts.returned == 5
    assert page.counts.status_counts == {"denied": 5, "success": 5}


def test_admin_service_tool_call_fallback_reads_jsonl_when_index_is_disabled(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.approval_audit_admin.tool_call_audit_index = None
    _write_audit_log(service.config.audit_log_path)

    page = service.approval_audit_admin.list_tool_call_audit_entries(limit=2)

    assert page.counts.scanned == 5
    assert page.counts.skipped_malformed == 1
    assert page.counts.skipped_non_tool_call == 1
    assert page.counts.matched == 3
    assert [item.tool_name for item in page.items] == [
        "prompt_agent",
        "make_phone_call",
    ]


def test_admin_service_directory_filter_falls_back_when_audit_index_is_incomplete(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    entries = [
        {
            "event_type": "tool_call",
            "session_id": "old-session",
            "directory_path": "root/team",
            "tool_name": "old_team_tool",
            "decision": "allowed",
            "created_at": "2026-05-18T09:00:00+00:00",
        },
        *[
            {
                "event_type": "tool_call",
                "session_id": f"recent-session-{index}",
                "directory_path": "root/other",
                "tool_name": f"recent_other_tool_{index}",
                "decision": "allowed",
                "created_at": f"2026-05-18T10:{index % 60:02d}:00+00:00",
            }
            for index in range(1_200)
        ],
    ]
    service.config.audit_log_path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )

    page = service.approval_audit_admin.list_tool_call_audit_entries(
        directory_path="root/team",
        direct_only=True,
        limit=10,
    )

    assert [item.tool_name for item in page.items] == ["old_team_tool"]
    assert page.counts.scanned == 1_201
    assert page.counts.matched == 1


def test_admin_service_default_read_falls_back_when_tail_index_is_sparse(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    entries = [
        {
            "event_type": "tool_call",
            "session_id": "old-session",
            "directory_path": "root/team",
            "tool_name": "old_tool_call",
            "decision": "allowed",
            "created_at": "2026-05-18T09:00:00+00:00",
        },
        *[
            {
                "event_type": "approval_decision",
                "session_id": f"recent-session-{index}",
                "created_at": f"2026-05-18T10:{index % 60:02d}:00+00:00",
            }
            for index in range(1_200)
        ],
    ]
    service.config.audit_log_path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )

    page = service.approval_audit_admin.list_tool_call_audit_entries(limit=10)

    assert [item.tool_name for item in page.items] == ["old_tool_call"]
    assert page.counts.scanned == 1_201
    assert page.counts.skipped_non_tool_call == 1_200
    assert page.counts.matched == 1

    recent_page = service.approval_audit_admin.list_tool_call_audit_entries(
        limit=10, recent_only=True
    )

    assert recent_page.items == []
    assert recent_page.counts.scanned == 0
    assert recent_page.counts.matched == 0
    assert recent_page.has_more is True


def test_audit_logger_writes_jsonl_and_indexes_tool_call_entries(tmp_path: Path) -> None:
    audit_log_path = tmp_path / "runtime" / "audit.jsonl"
    logger = AuditLogger(audit_log_path)

    logger.log(
        AuditEvent(
            event_type="tool_call",
            session_id="session-indexed",
            directory_path="root/team",
            tool_name="assistant_read_status",
            decision="allowed",
            created_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        )
    )

    assert "assistant_read_status" in audit_log_path.read_text(encoding="utf-8")
    index = ToolCallAuditIndex(audit_index_path_for_log_path(audit_log_path))
    page = index.query_tool_calls(
        session_id="session-indexed",
        tool_name=None,
        status=None,
        directory_path="root/team",
        direct_only=True,
        created_after=None,
        created_before=None,
        q=None,
        limit=10,
    )
    assert page.matched == 1
    assert page.payloads[0]["tool_name"] == "assistant_read_status"


def test_audit_logger_advances_complete_import_source_after_appends(tmp_path: Path) -> None:
    audit_log_path = tmp_path / "runtime" / "audit.jsonl"
    audit_log_path.parent.mkdir(parents=True)
    audit_log_path.write_text("", encoding="utf-8")
    index = ToolCallAuditIndex(audit_index_path_for_log_path(audit_log_path))
    initial_import = index.import_from_jsonl(audit_log_path, reset=True)
    logger = AuditLogger(audit_log_path)

    assert initial_import.complete is True
    assert index.import_is_complete(audit_log_path) is True

    logger.log(
        AuditEvent(
            event_type="approval_decision",
            session_id="session-non-tool",
            created_at=datetime(2026, 5, 18, 12, 1, tzinfo=UTC),
        )
    )
    logger.log(
        AuditEvent(
            event_type="tool_call",
            session_id="session-indexed",
            directory_path="root/team",
            tool_name="assistant_read_status",
            decision="allowed",
            created_at=datetime(2026, 5, 18, 12, 2, tzinfo=UTC),
        )
    )

    assert index.import_is_complete(audit_log_path) is True
    page = index.query_tool_calls(
        session_id="session-indexed",
        tool_name=None,
        status=None,
        directory_path="root/team",
        direct_only=True,
        created_after=None,
        created_before=None,
        q=None,
        limit=10,
    )
    assert page.matched == 1
    assert page.payloads[0]["tool_name"] == "assistant_read_status"


def test_audit_index_appended_payload_does_not_complete_past_concurrent_append(
    tmp_path: Path,
) -> None:
    audit_log_path = tmp_path / "runtime" / "audit.jsonl"
    audit_log_path.parent.mkdir(parents=True)
    audit_log_path.write_text("", encoding="utf-8")
    index = ToolCallAuditIndex(audit_index_path_for_log_path(audit_log_path))
    initial_import = index.import_from_jsonl(audit_log_path, reset=True)
    previous_stat = audit_log_path.stat()
    first_payload = {
        "event_type": "tool_call",
        "session_id": "session-first",
        "directory_path": "root/team",
        "tool_name": "first_tool",
        "decision": "allowed",
        "created_at": "2026-05-18T12:03:00+00:00",
    }
    second_payload = {
        "event_type": "tool_call",
        "session_id": "session-second",
        "directory_path": "root/team",
        "tool_name": "second_tool",
        "decision": "allowed",
        "created_at": "2026-05-18T12:04:00+00:00",
    }
    first_line = json.dumps(first_payload, sort_keys=True) + "\n"
    second_line = json.dumps(second_payload, sort_keys=True) + "\n"
    audit_log_path.write_text(first_line + second_line, encoding="utf-8")

    assert initial_import.complete is True
    indexed = index.index_appended_payload(
        first_payload,
        audit_log_path,
        previous_size_bytes=previous_stat.st_size,
        previous_mtime_ns=previous_stat.st_mtime_ns,
        appended_offset_bytes=len(first_line.encode("utf-8")),
    )

    assert indexed is True
    assert index.import_is_complete(audit_log_path) is False
    first_page = index.query_tool_calls(
        session_id=None,
        tool_name=None,
        status=None,
        directory_path="root/team",
        direct_only=True,
        created_after=None,
        created_before=None,
        q=None,
        limit=10,
    )
    assert [payload["tool_name"] for payload in first_page.payloads] == ["first_tool"]

    catch_up = index.import_from_jsonl(audit_log_path, reset=False)

    assert catch_up.imported == 1
    assert catch_up.complete is True
    assert index.import_is_complete(audit_log_path) is True
    caught_up_page = index.query_tool_calls(
        session_id=None,
        tool_name=None,
        status=None,
        directory_path="root/team",
        direct_only=True,
        created_after=None,
        created_before=None,
        q=None,
        limit=10,
    )
    assert [payload["tool_name"] for payload in caught_up_page.payloads] == [
        "second_tool",
        "first_tool",
    ]


def test_audit_index_backfill_is_idempotent_and_skips_bad_lines(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    _write_audit_log(service.config.audit_log_path)
    index = ToolCallAuditIndex(audit_index_path_for_log_path(service.config.audit_log_path))

    first = index.import_from_jsonl(service.config.audit_log_path, reset=True)
    second = index.import_from_jsonl(service.config.audit_log_path, reset=False)

    assert first.lines_scanned == 5
    assert first.imported == 3
    assert first.skipped_malformed == 1
    assert first.skipped_non_tool_call == 1
    assert first.complete is True
    assert second.imported == 0
    assert second.complete is True
    page = index.query_tool_calls(
        session_id=None,
        tool_name=None,
        status=None,
        directory_path=None,
        direct_only=False,
        created_after=datetime(2026, 5, 18, 10, 1, tzinfo=UTC),
        created_before=datetime(2026, 5, 18, 10, 3, tzinfo=UTC),
        q=None,
        limit=10,
    )
    assert page.matched == 1
    assert page.payloads[0]["tool_name"] == "make_phone_call"


def test_audit_index_tail_import_batches_records_in_one_connection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audit_log_path = tmp_path / "runtime" / "audit.jsonl"
    audit_log_path.parent.mkdir(parents=True)
    entries = [
        {
            "event_type": "tool_call",
            "session_id": f"session-{index}",
            "directory_path": "root/team",
            "tool_name": f"tool_{index}",
            "decision": "allowed",
            "created_at": f"2026-05-18T10:{index:02d}:00+00:00",
        }
        for index in range(25)
    ]
    audit_log_path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )
    from evander.repositories import audit_index as audit_index_module

    connect_calls = 0
    original_connect = audit_index_module.connect_audit_index_db

    def counting_connect(path):
        nonlocal connect_calls
        connect_calls += 1
        return original_connect(path)

    monkeypatch.setattr(audit_index_module, "connect_audit_index_db", counting_connect)
    index = ToolCallAuditIndex(audit_index_path_for_log_path(audit_log_path))

    result = index.import_from_jsonl_tail(audit_log_path, max_lines=100)

    assert result.imported == 25
    assert result.complete is True
    assert connect_calls == 2


def test_audit_index_directory_prefix_escapes_like_wildcards(tmp_path: Path) -> None:
    audit_log_path = tmp_path / "runtime" / "audit.jsonl"
    audit_log_path.parent.mkdir(parents=True)
    entries = [
        {
            "event_type": "tool_call",
            "directory_path": "root/team_a",
            "tool_name": "underscore_direct",
            "decision": "allowed",
            "created_at": "2026-05-18T10:00:00+00:00",
        },
        {
            "event_type": "tool_call",
            "directory_path": "root/team_a/sub",
            "tool_name": "underscore_child",
            "decision": "allowed",
            "created_at": "2026-05-18T10:01:00+00:00",
        },
        {
            "event_type": "tool_call",
            "directory_path": "root/teamXa/sub",
            "tool_name": "underscore_intruder",
            "decision": "allowed",
            "created_at": "2026-05-18T10:02:00+00:00",
        },
        {
            "event_type": "tool_call",
            "directory_path": "root/team%a",
            "tool_name": "percent_direct",
            "decision": "allowed",
            "created_at": "2026-05-18T10:03:00+00:00",
        },
        {
            "event_type": "tool_call",
            "directory_path": "root/team%a/sub",
            "tool_name": "percent_child",
            "decision": "allowed",
            "created_at": "2026-05-18T10:04:00+00:00",
        },
        {
            "event_type": "tool_call",
            "directory_path": "root/teamZZa/sub",
            "tool_name": "percent_intruder",
            "decision": "allowed",
            "created_at": "2026-05-18T10:05:00+00:00",
        },
    ]
    audit_log_path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )
    index = ToolCallAuditIndex(audit_index_path_for_log_path(audit_log_path))
    index.import_from_jsonl(audit_log_path, reset=True)

    underscore_page = index.query_tool_calls(
        session_id=None,
        tool_name=None,
        status=None,
        directory_path="root/team_a",
        direct_only=False,
        created_after=None,
        created_before=None,
        q=None,
        limit=10,
    )
    percent_page = index.query_tool_calls(
        session_id=None,
        tool_name=None,
        status=None,
        directory_path="root/team%a",
        direct_only=False,
        created_after=None,
        created_before=None,
        q=None,
        limit=10,
    )

    assert [payload["tool_name"] for payload in underscore_page.payloads] == [
        "underscore_child",
        "underscore_direct",
    ]
    assert [payload["tool_name"] for payload in percent_page.payloads] == [
        "percent_child",
        "percent_direct",
    ]


def test_admin_service_date_window_uses_index_without_full_jsonl_scan(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    entries = [
        {
            "event_type": "tool_call",
            "session_id": f"old-session-{index}",
            "directory_path": "root/archive",
            "tool_name": "old_tool",
            "decision": "allowed",
            "created_at": f"2026-05-17T09:{index % 60:02d}:00+00:00",
        }
        for index in range(2_000)
    ]
    entries.extend(
        {
            "event_type": "tool_call",
            "session_id": f"session-{index}",
            "directory_path": "root/team",
            "tool_name": f"tool_{index}",
            "decision": "allowed",
            "created_at": f"2026-05-18T10:{index:02d}:00+00:00",
        }
        for index in range(10)
    )
    service.config.audit_log_path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )

    page = service.approval_audit_admin.list_tool_call_audit_entries(
        directory_path="root/team",
        direct_only=True,
        created_after=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
        limit=5,
    )

    assert [item.tool_name for item in page.items] == [
        "tool_9",
        "tool_8",
        "tool_7",
        "tool_6",
        "tool_5",
    ]
    assert page.counts.scanned == 10
    assert page.counts.matched == 10
    assert page.has_more is True


def test_admin_web_tool_calls_route_returns_empty_payload_when_audit_log_is_missing(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

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
    assert (
        invalid_status_response.json()["error"]
        == "status must be one of: success, pending_approval, denied, or error"
    )


def test_build_admin_runtime_starts_background_import_without_tail_import(
    tmp_path: Path,
    monkeypatch,
) -> None:
    started: list[tuple[ToolCallAuditIndex, Path]] = []

    def fail_tail_import(self, *args, **kwargs):
        raise AssertionError("startup must not synchronously tail-import audit JSONL")

    def fake_background_import(index: ToolCallAuditIndex, audit_log_path: Path) -> None:
        started.append((index, audit_log_path))

    monkeypatch.setattr(ToolCallAuditIndex, "import_from_jsonl_tail", fail_tail_import)
    monkeypatch.setattr(
        admin_composition,
        "_start_tool_call_audit_index_import",
        fake_background_import,
    )

    service = build_admin_runtime(write_config(tmp_path))

    assert service.tool_call_audit_index is not None
    assert started == [(service.tool_call_audit_index, service.config.audit_log_path)]


def test_admin_web_dashboard_tool_calls_reads_index_only_when_jsonl_has_unindexed_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        admin_composition,
        "_start_tool_call_audit_index_import",
        lambda index, audit_log_path: None,
    )
    service = build_admin_runtime(write_config(tmp_path))
    entries = [
        {
            "event_type": "tool_call",
            "session_id": f"session-{index}",
            "directory_path": "root/team",
            "tool_name": f"tool_{index}",
            "decision": "allowed",
            "created_at": f"2026-05-18T10:{index:02d}:00+00:00",
        }
        for index in range(10)
    ]
    service.config.audit_log_path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(service))

    dashboard_response = client.get("/api/admin/tool-calls?_dashboard=1&limit=5")
    exact_response = client.get("/api/admin/tool-calls?limit=5")

    assert dashboard_response.status_code == 200
    dashboard_payload = dashboard_response.json()
    assert dashboard_payload["items"] == []
    assert dashboard_payload["counts"]["scanned"] == 0
    assert dashboard_payload["counts"]["matched"] == 0
    assert dashboard_payload["counts"]["returned"] == 0
    assert dashboard_payload["has_more"] is False

    assert exact_response.status_code == 200
    exact_payload = exact_response.json()
    assert [item["tool_name"] for item in exact_payload["items"]] == [
        "tool_9",
        "tool_8",
        "tool_7",
        "tool_6",
        "tool_5",
    ]
    assert exact_payload["counts"]["scanned"] == 10
    assert exact_payload["counts"]["matched"] == 10


def test_admin_web_tool_calls_route_recent_only_uses_bounded_recent_read(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.approval_audit_admin.tool_call_audit_index = None
    entries = [
        {
            "event_type": "tool_call",
            "session_id": f"old-session-{index}",
            "directory_path": "root/archive",
            "tool_name": f"old_tool_{index}",
            "decision": "allowed",
            "created_at": f"2026-05-18T09:{index % 60:02d}:00+00:00",
        }
        for index in range(3_000)
    ]
    entries.extend(
        {
            "event_type": "tool_call",
            "session_id": f"recent-session-{index}",
            "directory_path": "root/team",
            "tool_name": f"recent_tool_{index}",
            "decision": "allowed",
            "created_at": f"2026-05-18T10:{index % 60:02d}:00+00:00",
        }
        for index in range(110)
    )
    service.config.audit_log_path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(service))

    recent_response = client.get(
        "/api/admin/tool-calls?directory_path=root&direct_only=false&limit=100&recent_only=true"
    )
    exact_response = client.get(
        "/api/admin/tool-calls?directory_path=root&direct_only=false&limit=100"
    )

    assert recent_response.status_code == 200
    recent_payload = recent_response.json()
    assert recent_payload["counts"]["scanned"] == 101
    assert recent_payload["counts"]["matched"] == 101
    assert recent_payload["counts"]["returned"] == 100
    assert recent_payload["items"][0]["tool_name"] == "recent_tool_109"
    assert recent_payload["items"][-1]["tool_name"] == "recent_tool_10"
    assert recent_payload["has_more"] is True

    assert exact_response.status_code == 200
    exact_payload = exact_response.json()
    assert exact_payload["counts"]["scanned"] == len(entries)
    assert exact_payload["counts"]["matched"] == len(entries)
    assert exact_payload["counts"]["returned"] == 100
