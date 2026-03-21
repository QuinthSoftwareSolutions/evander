from __future__ import annotations

import json
from pathlib import Path

from agent_sandbox.services import PrFeedbackService


def _write_store(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cursors": {},
                "feedback": {
                    "github:review:1": {
                        "feedback_id": "github:review:1",
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "17",
                        "author": "reviewer-1",
                        "body": "Looks good",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    "github:review:2": {
                        "feedback_id": "github:review:2",
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "17",
                        "author": "reviewer-2",
                        "body": "Please add tests",
                        "created_at": "2026-01-02T00:00:00Z",
                    },
                    "github:review:3": {
                        "feedback_id": "github:review:3",
                        "provider": "github",
                        "repo": "other/repo",
                        "pr_id": "18",
                        "author": "reviewer-3",
                        "body": "Different stream",
                        "created_at": "2026-01-03T00:00:00Z",
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_has_pending_feedback_returns_minimal_heartbeat_shape(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    _write_store(store_path)
    service = PrFeedbackService(store_path)

    response = service.has_pending_feedback(
        streams=[{"provider": "github", "repo": "org/repo", "pr_id": "17"}],
        cursor="2025-12-31T00:00:00Z",
    )

    assert response.pending is True
    assert response.next_cursor == "2026-01-02T00:00:00Z"


def test_list_unread_feedback_returns_full_records_for_allowed_streams(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    _write_store(store_path)
    service = PrFeedbackService(store_path)

    response = service.list_unread_feedback(
        streams=[{"provider": "github", "repo": "org/repo", "pr_id": "17"}],
        cursor="2026-01-01T00:00:00Z",
    )

    assert [item.feedback_id for item in response.feedback] == ["github:review:2"]
    assert response.feedback[0].body == "Please add tests"
    assert response.next_cursor == "2026-01-02T00:00:00Z"
