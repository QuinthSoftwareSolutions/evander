from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from evander.models import GetPullRequestResponse
from evander.pr_tracking import (
    PrMapStore,
    completion_event_id,
    completion_event_payload,
    is_pr_completed,
    list_pr_map_entries,
    status_snapshot_from_pull_request,
)


def test_pr_map_store_updates_status_and_tracks_completion_publication(
    tmp_path: Path,
) -> None:
    pr_map_path = tmp_path / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "azure_devops:org/repo:42": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "42",
                        "session_id": "session-42",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    response = GetPullRequestResponse(
        provider="azure_devops",
        repo="org/repo",
        pr_id="42",
        title="Implement feature",
        status="completed",
        url="https://dev.azure.com/example/_git/repo/pullrequest/42",
        source_branch="refs/heads/feature",
        target_branch="refs/heads/main",
        source_commit="abc123",
        target_commit="def456",
        raw={"status": "completed", "closedDate": "2026-05-15T10:00:00Z"},
    )

    store = PrMapStore(pr_map_path)
    update = store.update_status(
        status_snapshot_from_pull_request(response, repo="org/repo", pr_id="42"),
        trigger_id="trigger-pr-completed",
        checked_at=datetime(2026, 5, 15, 10, 1, tzinfo=UTC),
    )

    assert update.completed_ready is True
    assert update.entry["status"] == "completed"
    assert update.entry["merged"] is True
    assert update.entry["completed_at"] == "2026-05-15T10:00:00Z"
    assert completion_event_payload(update.entry)["session_id"] == "session-42"

    event_id = completion_event_id("trigger-pr-completed", update.entry)
    store.mark_completion_event_published(
        provider="azure_devops",
        repo="org/repo",
        pr_id="42",
        trigger_id="trigger-pr-completed",
        event_id=event_id,
        published_at=datetime(2026, 5, 15, 10, 2, tzinfo=UTC),
    )
    second = store.update_status(
        status_snapshot_from_pull_request(response, repo="org/repo", pr_id="42"),
        trigger_id="trigger-pr-completed",
        checked_at=datetime(2026, 5, 15, 10, 3, tzinfo=UTC),
    )

    assert second.completed_ready is False
    payload = json.loads(pr_map_path.read_text(encoding="utf-8"))
    event_record = payload["entries"]["azure_devops:org/repo:42"]["completion_events"][
        "trigger-pr-completed"
    ]
    assert event_record["event_id"] == event_id


def test_list_pr_map_entries_prefers_session_directory_over_stale_mapping(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-42": {
                    "session_id": "session-42",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-08-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "azure_devops:org/repo:42": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "42",
                        "session_id": "session-42",
                        "directory_path": "root",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    root_entries = list_pr_map_entries(
        pr_map_path,
        directory_path="root",
        runtime_dir=runtime_dir,
    )
    session_entries = list_pr_map_entries(
        pr_map_path,
        directory_path="root/team-a",
        runtime_dir=runtime_dir,
    )

    assert [entry.pr_id for entry in root_entries] == ["42"]
    assert [entry.directory_path for entry in root_entries] == ["root/team-a"]
    assert [entry.pr_id for entry in session_entries] == ["42"]


def test_pr_terminal_state_detection_includes_closed_and_abandoned() -> None:
    assert is_pr_completed({"provider": "azure_devops", "status": "abandoned", "merged": False})
    assert is_pr_completed({"provider": "github", "status": "closed", "merged": False})
    assert is_pr_completed({"provider": "local", "status": "closed", "merged": False})
    assert not is_pr_completed({"provider": "azure_devops", "status": "active", "merged": False})


def test_azure_abandoned_pr_snapshot_is_terminal_without_merge() -> None:
    response = GetPullRequestResponse(
        provider="azure_devops",
        repo="org/repo",
        pr_id="43",
        title="Abandoned feature",
        status="abandoned",
        url="https://dev.azure.com/example/_git/repo/pullrequest/43",
        source_branch="refs/heads/feature",
        target_branch="refs/heads/main",
        source_commit="abc123",
        target_commit="def456",
        raw={"status": "abandoned", "closedDate": "2026-05-15T11:00:00Z"},
    )

    snapshot = status_snapshot_from_pull_request(response, repo="org/repo", pr_id="43")

    assert snapshot["status"] == "abandoned"
    assert snapshot["merged"] is False
    assert snapshot["completed_at"] == "2026-05-15T11:00:00Z"
