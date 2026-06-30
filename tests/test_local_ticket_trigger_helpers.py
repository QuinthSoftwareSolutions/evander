from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from evander.models import LocalIssueRecord
from evander.storage import JsonStore
from evander.triggers.providers._local_tickets import (
    LOCAL_ISSUES_FILENAME,
    RECENT_ISSUE_IDS_FIELD,
    RECENT_ISSUE_LIMIT,
    TICKET_CHANGED_CURSOR_FIELD,
    TICKET_CHANGED_CURSOR_FILENAME,
    TICKET_CREATED_CURSOR_FIELD,
    TICKET_CREATED_CURSOR_FILENAME,
    format_ticket_timestamp,
    local_ticket_candidates,
    max_local_ticket_timestamp,
    parse_ticket_timestamp,
    read_local_ticket_issues,
    seed_new_ticket_created_trigger_cursors,
    ticket_cursor_path,
    ticket_cursor_store,
    within_ticket_directories,
)


def _issue(
    tmp_path: Path,
    *,
    issue_id: str,
    issue_number: str,
    directory_path: str,
    created_at: datetime,
    updated_at: datetime,
) -> LocalIssueRecord:
    return LocalIssueRecord(
        issue_id=issue_id,
        issue_number=issue_number,
        title=issue_id,
        body="Body",
        yaml_path=str(tmp_path / f"{issue_id}.yaml"),
        directory_path=directory_path,
        created_at=created_at,
        updated_at=updated_at,
    )


def test_local_ticket_constants_and_cursor_paths(tmp_path: Path) -> None:
    assert LOCAL_ISSUES_FILENAME == "local-issues.json"
    assert TICKET_CREATED_CURSOR_FILENAME == "trigger-cursors-ticket-created.json"
    assert TICKET_CHANGED_CURSOR_FILENAME == "trigger-cursors-ticket-changed.json"
    assert TICKET_CREATED_CURSOR_FIELD == "last_created_at"
    assert TICKET_CHANGED_CURSOR_FIELD == "last_updated_at"
    assert RECENT_ISSUE_IDS_FIELD == "recent_issue_ids"
    assert RECENT_ISSUE_LIMIT == 512
    assert ticket_cursor_path(tmp_path, "created") == tmp_path / TICKET_CREATED_CURSOR_FILENAME
    assert ticket_cursor_path(tmp_path, "changed") == tmp_path / TICKET_CHANGED_CURSOR_FILENAME
    assert ticket_cursor_store(tmp_path, "created").path == ticket_cursor_path(
        tmp_path, "created"
    )


def test_within_ticket_directories_scopes_exactly_and_ignores_blank_entries() -> None:
    assert within_ticket_directories("root/example-industry/app/subteam", ["root"]) is True
    assert (
        within_ticket_directories(
            "root/example-industry/app/subteam",
            ["root/example-industry/app"],
        )
        is True
    )
    assert (
        within_ticket_directories(
            "root/example-industry/other",
            ["root/example-industry/app"],
        )
        is False
    )
    assert within_ticket_directories("root/example-industry/other", ["", " / "]) is False
    assert within_ticket_directories("root/example-industry/other", []) is True


def test_ticket_timestamp_parse_and_format_normalizes_to_utc() -> None:
    assert parse_ticket_timestamp(None) is None
    assert parse_ticket_timestamp(123) is None
    assert parse_ticket_timestamp("   ") is None
    assert parse_ticket_timestamp("2026-05-02T11:30:00Z") == datetime(
        2026, 5, 2, 11, 30, tzinfo=UTC
    )
    assert parse_ticket_timestamp("2026-05-02T11:30:00") == datetime(
        2026, 5, 2, 11, 30, tzinfo=UTC
    )
    assert parse_ticket_timestamp("2026-05-02T13:30:00+02:00") == datetime(
        2026, 5, 2, 11, 30, tzinfo=UTC
    )
    assert format_ticket_timestamp(
        datetime(2026, 5, 2, 13, 30, tzinfo=timezone(timedelta(hours=2)))
    ) == "2026-05-02T11:30:00Z"


def test_local_ticket_candidates_sort_and_scope_by_directory(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    issues_store = JsonStore(runtime_dir / LOCAL_ISSUES_FILENAME, LocalIssueRecord)
    issue_a = _issue(
        tmp_path,
        issue_id="issue-a",
        issue_number="1",
        directory_path="root/example-industry/app/subteam",
        created_at=datetime(2026, 5, 2, 11, 30, tzinfo=UTC),
        updated_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
    )
    issue_b = _issue(
        tmp_path,
        issue_id="issue-b",
        issue_number="2",
        directory_path="root/example-industry/app",
        created_at=datetime(2026, 5, 2, 11, 30, tzinfo=UTC),
        updated_at=datetime(2026, 5, 2, 11, 45, tzinfo=UTC),
    )
    issue_c = _issue(
        tmp_path,
        issue_id="issue-c",
        issue_number="3",
        directory_path="root/example-industry/other",
        created_at=datetime(2026, 5, 2, 12, 15, tzinfo=UTC),
        updated_at=datetime(2026, 5, 2, 12, 20, tzinfo=UTC),
    )
    issues_store.put(issue_a.issue_id, issue_a)
    issues_store.put(issue_b.issue_id, issue_b)
    issues_store.put(issue_c.issue_id, issue_c)

    read_back = read_local_ticket_issues(runtime_dir)
    assert set(read_back) == {"issue-a", "issue-b", "issue-c"}

    candidates = local_ticket_candidates(
        runtime_dir,
        ["root/example-industry/app"],
        "created_at",
    )

    assert [issue.issue_id for issue in candidates] == ["issue-a", "issue-b"]
    assert (
        max_local_ticket_timestamp(candidates, ["root/example-industry/app"], "created_at")
        == datetime(2026, 5, 2, 11, 30, tzinfo=UTC)
    )


def test_seed_new_ticket_created_trigger_cursors_preserves_existing_entries(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    issues_store = JsonStore(runtime_dir / LOCAL_ISSUES_FILENAME, LocalIssueRecord)
    issues_store.put(
        "issue-a",
        _issue(
            tmp_path,
            issue_id="issue-a",
            issue_number="1",
            directory_path="root/example-industry/app/subteam",
            created_at=datetime(2026, 5, 2, 8, 0, tzinfo=UTC),
            updated_at=datetime(2026, 5, 2, 8, 15, tzinfo=UTC),
        ),
    )
    issues_store.put(
        "issue-b",
        _issue(
            tmp_path,
            issue_id="issue-b",
            issue_number="2",
            directory_path="root/example-industry/app",
            created_at=datetime(2026, 5, 2, 11, 30, tzinfo=UTC),
            updated_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        ),
    )
    issues_store.put(
        "issue-c",
        _issue(
            tmp_path,
            issue_id="issue-c",
            issue_number="3",
            directory_path="root/example-industry/other",
            created_at=datetime(2026, 5, 2, 12, 45, tzinfo=UTC),
            updated_at=datetime(2026, 5, 2, 12, 45, tzinfo=UTC),
        ),
    )

    cursor_path = ticket_cursor_path(runtime_dir, "created")
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(
        json.dumps(
            {
                "preserve-me": {"last_created_at": "2026-05-01T00:00:00Z"},
                "ticket-copy": {"last_created_at": "2026-05-01T01:00:00Z"},
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    seed_new_ticket_created_trigger_cursors(
        runtime_dir=runtime_dir,
        previous_config={
            "triggers": [
                {
                    "id": "ticket-existing",
                    "display_name": "Existing",
                    "type": "trigger:ticket:created",
                    "directories": ["root/team-a"],
                }
            ]
        },
        next_config={
            "triggers": [
                {
                    "id": "ticket-existing",
                    "display_name": "Existing",
                    "type": "trigger:ticket:created",
                    "directories": ["root/team-a"],
                },
                {
                    "id": "ticket-copy",
                    "display_name": "Copied",
                    "type": "trigger:ticket:created",
                    "directories": ["root/example-industry/app"],
                },
                {
                    "id": "ticket-copy-2",
                    "display_name": "Copied 2",
                    "type": "trigger:ticket:created",
                    "directories": ["root/example-industry/app"],
                },
            ]
        },
    )

    payload = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert payload["preserve-me"] == {"last_created_at": "2026-05-01T00:00:00Z"}
    assert payload["ticket-copy"] == {"last_created_at": "2026-05-01T01:00:00Z"}
    assert payload["ticket-copy-2"]["last_created_at"] == "2026-05-02T11:30:00Z"
