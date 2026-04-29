from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_sandbox.models import SessionManifest
from agent_sandbox.reviews.request_service import ReviewRequestMetadata, ReviewRequestService
from agent_sandbox.reviews.request_store import ReviewRequestStore
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository


class _StubReviewRequestProvider:
    def fetch_metadata(self, *, repo: str, pr_id: str) -> ReviewRequestMetadata:
        return ReviewRequestMetadata(
            canonical_url=f"https://example.test/{repo}/pull/{pr_id}",
            title="Review title",
            body="Review body",
            base_branch="main",
            head_branch="feature/test",
            base_commit="abc123",
            head_commit="def456",
        )


def test_enqueue_request_uses_session_repository_directory_when_directory_not_provided(
    tmp_path: Path,
) -> None:
    sessions = JsonSessionManifestRepository(tmp_path / "sessions.json")
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    service = ReviewRequestService(
        providers={"github": _StubReviewRequestProvider()},
        store=ReviewRequestStore(tmp_path / "review-request-store.json"),
        session_store=sessions,
    )

    response = service.enqueue_request(
        session_id="session-1",
        provider="github",
        repo="QuinthSoftwareSolutions/agent-orchestrator",
        pr_id="42",
        requested_by="alice",
    )

    assert response.directory_path == "root/team-a"
    assert (
        response.canonical_url
        == "https://example.test/QuinthSoftwareSolutions/agent-orchestrator/pull/42"
    )
    stored = ReviewRequestStore(Path(tmp_path / "review-request-store.json")).get_request(
        response.request_id
    )
    assert stored is not None
    assert stored.directory_path == "root/team-a"
