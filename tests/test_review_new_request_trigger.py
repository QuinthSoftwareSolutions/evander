from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.provider_models import ReviewNewRequestTriggerDefinition
from agent_sandbox.reviews.request_store import ReviewRequestRecord, ReviewRequestStore
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.review_new_request import ReviewNewRequestTriggerProvider


def _request(request_id: str, directory_path: str) -> ReviewRequestRecord:
    return ReviewRequestRecord.model_validate(
        {
            "request_id": request_id,
            "requested_at": datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
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


def test_review_new_request_trigger_claims_matching_requests(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ReviewRequestStore(runtime_dir / "review-request-store.json")
    store.add_request_if_new(_request("review-1", "root/team-a"))
    store.add_request_if_new(_request("review-2", "root/team-b"))

    trigger = ReviewNewRequestTriggerDefinition.model_validate(
        {
            "id": "review-trigger",
            "display_name": "Review requests",
            "type": "trigger:review:new-request",
        }
    )
    provider = ReviewNewRequestTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=runtime_dir),
            metadata={"directory_path": "root/team-a"},
        ),
    )

    events = provider.poll()

    assert [event.event_id for event in events] == ["review-trigger:review-1"]
    assert events[0].payload["review_request"]["request_id"] == "review-1"
    assert events[0].payload["review_request"]["status"] == "leased"

    stored = store.get_request("review-1")
    assert stored is not None
    assert stored.status == "leased"
    assert stored.dispatch_count == 1
    assert store.get_request("review-2").status == "pending"


def test_review_new_request_trigger_is_idempotent_while_leased(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    store = ReviewRequestStore(runtime_dir / "review-request-store.json")
    store.add_request_if_new(_request("review-1", "root/team-a"))

    trigger = ReviewNewRequestTriggerDefinition.model_validate(
        {
            "id": "review-trigger",
            "display_name": "Review requests",
            "type": "trigger:review:new-request",
        }
    )
    provider = ReviewNewRequestTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=runtime_dir),
            metadata={"directory_path": "root/team-a"},
        ),
    )

    first = provider.poll()
    second = provider.poll()

    assert [event.event_id for event in first] == ["review-trigger:review-1"]
    assert second == []
