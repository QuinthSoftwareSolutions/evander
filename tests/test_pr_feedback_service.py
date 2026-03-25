from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_sandbox.pr_feedback_worker import FeedbackEnvelope, PrFeedbackStore
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


def test_has_pending_feedback_uses_persisted_session_read_cursor(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    _write_store(store_path)
    service = PrFeedbackService(store_path)
    stream = [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]

    first = service.list_unread_feedback(streams=stream, session_id="session-1")
    assert [item.feedback_id for item in first.feedback] == ["github:review:1", "github:review:2"]

    second = service.has_pending_feedback(streams=stream, session_id="session-1")
    assert second.pending is False
    assert second.next_cursor == first.next_cursor


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


def test_list_unread_feedback_persists_session_cursor(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    _write_store(store_path)
    service = PrFeedbackService(store_path)
    stream = [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]

    first = service.list_unread_feedback(
        streams=stream,
        session_id="session-1",
        limit=1,
    )
    assert [item.feedback_id for item in first.feedback] == ["github:review:1"]

    second = service.list_unread_feedback(
        streams=stream,
        session_id="session-1",
        limit=1,
    )
    assert [item.feedback_id for item in second.feedback] == ["github:review:2"]


def test_new_comment_ingestion_is_visible_to_pending_feedback_check(tmp_path: Path) -> None:
    store_path = tmp_path / "runtime" / "pr-feedback-store.json"
    store = PrFeedbackStore(store_path)
    service = PrFeedbackService(store_path)
    stream = [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]

    before = service.has_pending_feedback(streams=stream, cursor="2026-01-10T00:00:00Z")
    assert before.pending is False

    inserted = store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="github:review_comment:42",
            provider="github",
            repo="org/repo",
            pr_id="17",
            thread_id="11",
            comment_id="42",
            author="reviewer",
            body="Please address this.",
            created_at=datetime(2026, 1, 11, 12, 0, 0, tzinfo=timezone.utc),
        )
    )
    assert inserted is True

    after = service.has_pending_feedback(streams=stream, cursor="2026-01-10T00:00:00Z")
    assert after.pending is True
    assert after.next_cursor == "2026-01-11T12:00:00Z"


def test_cursor_advancement_prevents_reprocessing_old_comments(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    _write_store(store_path)
    service = PrFeedbackService(store_path)
    stream = [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]

    first_batch = service.list_unread_feedback(streams=stream, cursor="2025-12-31T00:00:00Z")
    assert [item.feedback_id for item in first_batch.feedback] == ["github:review:1", "github:review:2"]
    assert first_batch.next_cursor == "2026-01-02T00:00:00Z"

    second_batch = service.list_unread_feedback(streams=stream, cursor=first_batch.next_cursor)
    assert second_batch.feedback == []
    assert second_batch.next_cursor == "2026-01-02T00:00:00Z"


def test_list_unread_feedback_limit_returns_single_oldest_item(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    _write_store(store_path)
    service = PrFeedbackService(store_path)

    response = service.list_unread_feedback(
        streams=[{"provider": "github", "repo": "org/repo", "pr_id": "17"}],
        cursor="2025-12-31T00:00:00Z",
        limit=1,
    )

    assert [item.feedback_id for item in response.feedback] == ["github:review:1"]
    assert response.next_cursor == "2026-01-01T00:00:00Z"
