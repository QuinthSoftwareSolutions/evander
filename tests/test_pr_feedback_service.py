from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_sandbox.pr_feedback_worker import FeedbackEnvelope, PrFeedbackStore
from agent_sandbox.services import PrFeedbackService


class _InMemoryFeedbackRepo:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.advanced: list[tuple[str, str, str | None]] = []

    def get_cursor(self, stream_key: str) -> str | None:
        return None

    def set_cursor(self, stream_key: str, cursor: str | None) -> None:
        del stream_key, cursor

    def add_feedback_if_new(self, envelope: FeedbackEnvelope) -> bool:
        del envelope
        return False

    def get_session_cursor(self, session_id: str, stream_key: str) -> str | None:
        del session_id, stream_key
        return None

    def advance_session_cursor(self, session_id: str, stream_key: str, cursor: str | None) -> str | None:
        self.advanced.append((session_id, stream_key, cursor))
        return cursor

    def list_feedback_for_streams(
        self,
        *,
        streams: list[dict[str, str]],
        after: dict[tuple[str, str, str], str | None],
        exclude_feedback_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[FeedbackEnvelope]:
        allowed_streams = {
            (stream["provider"], stream["repo"], stream["pr_id"])
            for stream in streams
        }
        ignored = exclude_feedback_ids or set()
        items: list[FeedbackEnvelope] = []
        for raw_value in self.payload.get("feedback", {}).values():
            if not isinstance(raw_value, dict):
                continue
            candidate = FeedbackEnvelope.model_validate(raw_value)
            key = (candidate.provider, candidate.repo, candidate.pr_id)
            if key not in allowed_streams or candidate.feedback_id in ignored:
                continue
            cursor = after.get(key)
            if cursor and candidate.created_at <= datetime.fromisoformat(cursor.replace("Z", "+00:00")):
                continue
            items.append(candidate)
        items.sort(key=lambda item: item.created_at)
        return items[:limit] if limit is not None else items

    def mark_session_streams_read(
        self,
        *,
        session_id: str,
        streams: list[dict[str, str]],
        cursor: str | None,
    ) -> dict[str, str | None]:
        for stream in streams:
            stream_key = f"{stream['provider']}:{stream['repo']}:{stream['pr_id']}"
            self.advanced.append((session_id, stream_key, cursor))
        return {
            f"{stream['provider']}:{stream['repo']}:{stream['pr_id']}": cursor
            for stream in streams
        }

    def has_unread_feedback(self, *, session_id: str, streams: list[dict[str, str]]) -> bool:
        del session_id, streams
        return False

    def read_raw(self) -> dict[str, object]:
        return dict(self.payload)

    def prune_streams(self, streams: set[tuple[str, str, str]]) -> tuple[int, int]:
        del streams
        return (0, 0)


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


def test_has_pending_feedback_does_not_advance_session_cursor(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    _write_store(store_path)
    service = PrFeedbackService(store_path)
    stream = [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]

    response = service.has_pending_feedback(streams=stream, session_id="session-1")

    assert response.pending is True
    unread = service.list_unread_feedback(streams=stream, session_id="session-1", limit=1)
    assert [item.feedback_id for item in unread.feedback] == ["github:review:1"]


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


def test_list_unread_feedback_skips_ignored_feedback_ids(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    ignored_path = tmp_path / "ignored-feedback-ids.json"
    _write_store(store_path)
    ignored_path.write_text(
        json.dumps({"feedback_ids": ["github:review:2"]}),
        encoding="utf-8",
    )
    service = PrFeedbackService(store_path, ignored_feedback_store_path=ignored_path)

    response = service.list_unread_feedback(
        streams=[{"provider": "github", "repo": "org/repo", "pr_id": "17"}],
        cursor="2025-12-31T00:00:00Z",
    )

    assert [item.feedback_id for item in response.feedback] == ["github:review:1"]
    assert response.next_cursor == "2026-01-01T00:00:00Z"


def test_list_unread_feedback_prunes_expired_ignored_feedback_ids(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    ignored_path = tmp_path / "ignored-feedback-ids.json"
    _write_store(store_path)
    ignored_path.write_text(
        json.dumps(
            {
                "feedback_ids": [
                    {"feedback_id": "github:review:1", "ignored_at": "2025-01-01T00:00:00Z"},
                    {"feedback_id": "github:review:2", "ignored_at": "2099-01-01T00:00:00Z"},
                ]
            }
        ),
        encoding="utf-8",
    )
    service = PrFeedbackService(store_path, ignored_feedback_store_path=ignored_path)

    response = service.list_unread_feedback(
        streams=[{"provider": "github", "repo": "org/repo", "pr_id": "17"}],
        cursor="2025-12-31T00:00:00Z",
    )

    assert [item.feedback_id for item in response.feedback] == ["github:review:1"]
    payload = json.loads(ignored_path.read_text(encoding="utf-8"))
    assert payload["feedback_ids"] == [
        {"feedback_id": "github:review:2", "ignored_at": "2099-01-01T00:00:00Z"}
    ]


def test_list_all_feedback_includes_items_regardless_of_read_cursor(tmp_path: Path) -> None:
    store_path = tmp_path / "pr-feedback-store.json"
    _write_store(store_path)
    service = PrFeedbackService(store_path)

    response = service.list_all_feedback(
        streams=[{"provider": "github", "repo": "org/repo", "pr_id": "17"}],
        limit=10,
    )

    assert [item.feedback_id for item in response.feedback] == ["github:review:1", "github:review:2"]
    assert response.next_cursor == "2026-01-02T00:00:00Z"


def test_list_unread_feedback_can_use_repository_contract_without_file_reads(tmp_path: Path) -> None:
    store_path = tmp_path / "missing-pr-feedback-store.json"
    repository = _InMemoryFeedbackRepo(
        {
            "feedback": {
                "github:review:1": {
                    "feedback_id": "github:review:1",
                    "provider": "github",
                    "repo": "org/repo",
                    "pr_id": "17",
                    "author": "reviewer-1",
                    "body": "Looks good",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            }
        }
    )
    service = PrFeedbackService(store_path, repository=repository)

    response = service.list_unread_feedback(
        streams=[{"provider": "github", "repo": "org/repo", "pr_id": "17"}],
        session_id="session-1",
    )

    assert [item.feedback_id for item in response.feedback] == ["github:review:1"]
    assert repository.advanced == [("session-1", "github:org/repo:17", "2026-01-01T00:00:00Z")]
