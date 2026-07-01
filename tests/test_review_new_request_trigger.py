from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from evander.config import GatewayConfig
from evander.events.provider_models import ReviewNewRequestTriggerDefinition
from evander.reviews.request_store import ReviewRequestRecord, ReviewRequestStore
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.review_new_request import ReviewNewRequestTriggerProvider


def _request(
    request_id: str,
    directory_path: str,
    *,
    requested_at: datetime | None = None,
) -> ReviewRequestRecord:
    return ReviewRequestRecord.model_validate(
        {
            "request_id": request_id,
            "requested_at": requested_at or datetime.now(UTC),
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


def _provider(
    runtime_dir: Path,
    *,
    trigger_id: str = "review-trigger",
    directory_path: str = "root/team-a",
) -> ReviewNewRequestTriggerProvider:
    trigger = ReviewNewRequestTriggerDefinition.model_validate(
        {
            "id": trigger_id,
            "display_name": "Review requests",
            "type": "trigger:review:new-request",
        }
    )
    provider = ReviewNewRequestTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=runtime_dir),
            metadata={"directory_path": directory_path},
        ),
    )
    return provider


def _poll_and_ack(provider: ReviewNewRequestTriggerProvider):
    events = provider.poll()
    for event in events:
        provider.acknowledge_published_event(event)
    return events


def test_review_new_request_trigger_seeds_new_subscription_without_history(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ReviewRequestStore(runtime_dir / "review-request-store.json")
    store.add_request_if_new(
        _request(
            "review-old",
            "root/team-a",
            requested_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        )
    )

    provider = _provider(runtime_dir)

    assert _poll_and_ack(provider) == []

    stored = store.get_request("review-old")
    assert stored is not None
    assert stored.status == "pending"
    assert stored.dispatch_count == 0


def test_review_new_request_trigger_emits_new_matching_requests_without_claiming(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ReviewRequestStore(runtime_dir / "review-request-store.json")
    provider = _provider(runtime_dir)
    _poll_and_ack(provider)

    store.add_request_if_new(_request("review-1", "root/team-a"))
    store.add_request_if_new(_request("review-2", "root/team-b"))

    events = _poll_and_ack(provider)

    assert [event.event_id for event in events] == ["review-trigger:review-1"]
    assert events[0].payload["review_request"]["request_id"] == "review-1"
    assert events[0].payload["review_request"]["status"] == "pending"

    stored = store.get_request("review-1")
    assert stored is not None
    assert stored.status == "pending"
    assert stored.dispatch_count == 0
    assert store.get_request("review-2").status == "pending"


def test_review_new_request_trigger_broadcasts_per_trigger_subscription(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ReviewRequestStore(runtime_dir / "review-request-store.json")
    provider_1 = _provider(runtime_dir, trigger_id="review-trigger-1")
    provider_2 = _provider(runtime_dir, trigger_id="review-trigger-2")
    _poll_and_ack(provider_1)
    _poll_and_ack(provider_2)

    store.add_request_if_new(_request("review-1", "root/team-a"))

    first = _poll_and_ack(provider_1)
    second = _poll_and_ack(provider_2)

    assert [event.event_id for event in first] == ["review-trigger-1:review-1"]
    assert [event.event_id for event in second] == ["review-trigger-2:review-1"]
    assert first[0].payload["review_request"]["request_id"] == "review-1"
    assert second[0].payload["review_request"]["request_id"] == "review-1"

    stored = store.get_request("review-1")
    assert stored is not None
    assert stored.status == "pending"
    assert stored.dispatch_count == 0


def test_review_new_request_trigger_is_idempotent_after_publishing(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ReviewRequestStore(runtime_dir / "review-request-store.json")
    provider = _provider(runtime_dir)
    _poll_and_ack(provider)
    store.add_request_if_new(_request("review-1", "root/team-a"))

    first = _poll_and_ack(provider)
    second = _poll_and_ack(provider)

    assert [event.event_id for event in first] == ["review-trigger:review-1"]
    assert second == []


def test_review_new_request_trigger_respects_directory_scope(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ReviewRequestStore(runtime_dir / "review-request-store.json")
    provider = _provider(runtime_dir, directory_path="root/team-a")
    _poll_and_ack(provider)
    store.add_request_if_new(_request("review-1", "root/team-a"))
    store.add_request_if_new(_request("review-2", "root/team-b"))
    store.add_request_if_new(_request("review-3", "root/team-a/project"))

    events = _poll_and_ack(provider)

    assert [event.event_id for event in events] == [
        "review-trigger:review-1",
        "review-trigger:review-3",
    ]


def test_review_new_request_trigger_new_subscription_emits_later_requests(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ReviewRequestStore(runtime_dir / "review-request-store.json")
    store.add_request_if_new(
        _request(
            "review-old",
            "root/team-a",
            requested_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        )
    )

    provider = _provider(runtime_dir)
    events = _poll_and_ack(provider)

    assert events == []

    store.add_request_if_new(_request("review-new", "root/team-a"))

    later = _poll_and_ack(provider)

    assert [event.event_id for event in later] == ["review-trigger:review-new"]
