from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from evander.reviews.request_store import ReviewRequestRecord, ReviewRequestStore


def _request(
    request_id: str,
    *,
    directory_path: str = "root/team-a",
    requested_at: datetime | None = None,
) -> ReviewRequestRecord:
    return ReviewRequestRecord.model_validate(
        {
            "request_id": request_id,
            "requested_at": requested_at or datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
            "requested_by": "user-a",
            "request_channel": "teams:review-requests",
            "provider": "github",
            "repo": "org/repo",
            "pr_id": "17",
            "canonical_url": "https://github.test/org/repo/pull/17",
            "title": "Review this change",
            "body": "Please review.",
            "base_branch": "main",
            "head_branch": "review/session-1",
            "base_commit": "base-sha",
            "head_commit": "head-sha",
            "review_type": "full",
            "instructions": "Focus on security.",
            "directory_path": directory_path,
            "model": "gpt-5",
            "labels": {"priority": "high"},
            "metadata": {"source": "teams"},
        }
    )


def test_review_request_store_adds_claims_and_updates_status(tmp_path: Path) -> None:
    store = ReviewRequestStore(tmp_path / "review-request-store.json")
    request = _request("review-1")

    assert store.add_request_if_new(request) is True
    assert store.add_request_if_new(request) is False

    claimed = store.claim_ready_requests(
        lease_owner="trigger-1",
        lease_seconds=60,
        directory_path="root",
        now=datetime(2026, 4, 3, 12, 5, tzinfo=UTC),
    )

    assert [item.request_id for item in claimed] == ["review-1"]
    assert claimed[0].status == "leased"
    assert claimed[0].dispatch_count == 1
    assert claimed[0].lease_owner == "trigger-1"
    assert claimed[0].lease_expires_at == datetime(2026, 4, 3, 12, 6, tzinfo=UTC)

    stored = store.get_request("review-1")
    assert stored is not None
    assert stored.status == "leased"
    assert stored.lease_owner == "trigger-1"
    assert stored.dispatch_count == 1


def test_review_request_store_respects_directory_scope(tmp_path: Path) -> None:
    store = ReviewRequestStore(tmp_path / "review-request-store.json")
    store.add_request_if_new(_request("review-1", directory_path="root/team-a"))
    store.add_request_if_new(_request("review-2", directory_path="root/team-b"))

    claimed = store.claim_ready_requests(
        lease_owner="trigger-1",
        lease_seconds=60,
        directory_path="root/team-a",
        now=datetime(2026, 4, 3, 12, 5, tzinfo=UTC),
    )

    assert [item.request_id for item in claimed] == ["review-1"]
    assert store.get_request("review-2").status == "pending"
