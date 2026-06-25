from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.internal import InternalEventBus, internal_event_store_path
from truman_team_addon.models import TeamOutboxRecord, TeamOutboxStatus
from truman_team_addon.provider_schemas import TeamPublishOutboxEventsEffectDefinition
from truman_team_addon.providers import TeamPublishOutboxEventsEffectProvider
from truman_team_addon.repository import TeamStateRepository


def test_publish_outbox_events_claims_publishes_and_completes_record(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-1",
            intent_type="team.ephemeral.requested",
            aggregate_type="ephemeral_assignment",
            aggregate_id="assignment-1",
            idempotency_key="team-addon:team.ephemeral.requested:assignment-1",
            payload={
                "event_type": "team.ephemeral.requested",
                "correlation_key": "assignment-1",
                "payload": {
                    "assignment_id": "assignment-1",
                    "session_id": "session-ephemeral",
                },
            },
            created_at=now,
            updated_at=now,
        )
    )
    provider = TeamPublishOutboxEventsEffectProvider()
    provider.initialize(
        TeamPublishOutboxEventsEffectDefinition(
            id="publish",
            display_name="Publish outbox",
            type="effect:team:publish-outbox-events",
            limit=5,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["claimed_count"] == 1
    assert result["published_count"] == 1
    assert result["completed_outbox_record_ids"] == ["outbox-1"]
    assert result["failed_outbox_record_ids"] == []
    assert len(result["published_event_ids"]) == 1
    completed = repo.list_outbox_records(status=TeamOutboxStatus.COMPLETED)
    assert [record.id for record in completed] == ["outbox-1"]
    events = InternalEventBus(internal_event_store_path(tmp_path)).list_events()
    assert [event.event_type for event in events] == ["team.ephemeral.requested"]
    assert events[0].payload["assignment_id"] == "assignment-1"


def test_publish_outbox_events_requeues_failed_publish(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-bad",
            intent_type="team.ephemeral.requested",
            payload={"payload": {"assignment_id": "assignment-1"}},
        )
    )
    provider = TeamPublishOutboxEventsEffectProvider()
    provider.initialize(
        TeamPublishOutboxEventsEffectDefinition(
            id="publish",
            display_name="Publish outbox",
            type="effect:team:publish-outbox-events",
            retry_after_seconds=10,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["failed_outbox_record_ids"] == ["outbox-bad"]
    pending = repo.list_outbox_records(status=TeamOutboxStatus.PENDING)
    assert [record.id for record in pending] == ["outbox-bad"]
    assert pending[0].last_error_code == "publish_failed"
    assert pending[0].attempt_count == 1
