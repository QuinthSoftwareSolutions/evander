from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from evander.effects.providers.base import EffectRuntimeContext
from evander.admin.user_notifications import AdminUserNotificationStore
from evander.admin.web.rbac import TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION
from evander.admin.web.user_store import AdminWebUserStore
from evander.events.internal import InternalEventBus, internal_event_store_path
from evander_team_addon.models import (
    HumanReviewAssignment,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    PersistentAgent,
    Project,
    ReleasePackage,
    Team,
    TeamOutboxRecord,
    TeamOutboxStatus,
    TeamRole,
)
from evander_team_addon.provider_schemas import TeamPublishOutboxEventsEffectDefinition
from evander_team_addon.providers import TeamPublishOutboxEventsEffectProvider
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.human_review_assignment_service import (
    reconcile_current_human_review_assignments,
)


def _seed_human_review_notification_state(
    repo: TeamStateRepository,
    *,
    reviewer_user_ids: list[str],
) -> None:
    def seed_human_review(state):
        state.teams["team"] = Team(
            id="team",
            name="Team",
            manager_agent_id="lead",
            project_ids=["project"],
        )
        state.roles["lead-role"] = TeamRole(
            id="lead-role",
            team_id="team",
            name="Lead",
        )
        state.agents["lead"] = PersistentAgent(
            id="lead",
            team_id="team",
            role_id="lead-role",
            session_id="lead-session",
        )
        state.projects["project"] = Project(
            id="project",
            team_id="team",
            name="Project",
            lead_agent_id="lead",
        )
        state.release_packages["release-1"] = ReleasePackage(
            id="release-1",
            project_id="project",
            candidate_commit_sha="a" * 40,
            candidate_tree_hash="b" * 40,
        )
        state.human_review_requests["human-review-1"] = HumanReviewRequest(
            id="human-review-1",
            release_package_id="release-1",
            candidate_commit_sha="a" * 40,
            candidate_tree_hash="b" * 40,
            requirement_snapshot=HumanReviewRequirementSnapshot(
                directory_path="root",
                configured_group_ids=["release-reviewers"],
                initially_eligible_user_ids=reviewer_user_ids,
            ),
            current_assignment=HumanReviewAssignment(
                eligible_user_ids=reviewer_user_ids,
            ),
            ready_at=datetime.now(UTC),
        )
        return state

    repo.update(seed_human_review)


def _seed_reviewer_directory(
    tmp_path: Path,
    *,
    reviewer_user_ids: list[str],
) -> AdminWebUserStore:
    user_store = AdminWebUserStore(tmp_path / "admin-web.db")
    user_store.bootstrap_admins({"owner@example.com"})
    user_store.upsert_group(
        group_id="release-reviewers",
        display_name="Release reviewers",
    )
    user_store.set_group_permissions(
        "release-reviewers",
        [TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION],
    )
    for user_id in reviewer_user_ids:
        user_store.upsert_user(login=user_id, role="regular")
        user_store.assign_user_group(
            login=user_id,
            group_id="release-reviewers",
            directory_path="root",
        )
    return user_store


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


def test_publish_human_review_outbox_creates_deduplicated_admin_notification(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    _seed_human_review_notification_state(
        repo,
        reviewer_user_ids=["reviewer@example.com"],
    )
    _seed_reviewer_directory(
        tmp_path,
        reviewer_user_ids=["reviewer@example.com"],
    )
    repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-human-review-1",
            intent_type="team.release.human_review_requested",
            aggregate_type="human_review_request",
            aggregate_id="human-review-1",
            idempotency_key=(
                "team-addon:team.release.human_review_requested:"
                "human-review-1:reviewer@example.com:1"
            ),
            payload={
                "event_type": "team.release.human_review_requested",
                "correlation_key": "human-review-1",
                "payload": {
                    "human_review_request_id": "human-review-1",
                    "release_package_id": "release-1",
                    "candidate_commit_sha": "a" * 40,
                    "candidate_tree_hash": "b" * 40,
                    "recipient_user_id": "Reviewer@Example.com",
                    "assignment_generation": 1,
                },
            },
        )
    )
    provider = TeamPublishOutboxEventsEffectProvider()
    provider.initialize(
        TeamPublishOutboxEventsEffectDefinition(
            id="publish",
            display_name="Publish outbox",
            type="effect:team:publish-outbox-events",
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    first = provider.execute()

    assert first["success"] is True
    notifications, unread_count = AdminUserNotificationStore(tmp_path).list_unread(
        user_id="reviewer@example.com",
    )
    assert unread_count == 1
    assert notifications[0]["source"] == "team_human_review"
    assert notifications[0]["source_id"] == (
        "human-review-1:reviewer@example.com:1"
    )
    assert notifications[0]["target_url"] == (
        "#addon:team-addon?view=my-human-reviews"
        "&human_review_request_id=human-review-1"
    )
    assert notifications[0]["metadata"] == {
        "human_review_request_id": "human-review-1",
        "release_package_id": "release-1",
        "assignment_generation": 1,
    }

    # A publisher retry after the notification write remains harmless because
    # the Admin store uses request, recipient, and assignment generation as its
    # durable deduplication identity.
    duplicate = AdminUserNotificationStore(tmp_path).push(
        user_id="reviewer@example.com",
        title="Human release review assigned",
        body="retry",
        source="team_human_review",
        source_id="human-review-1:reviewer@example.com:1",
    )
    assert duplicate["id"] == notifications[0]["id"]
    assert AdminUserNotificationStore(tmp_path).list_unread(
        user_id="reviewer@example.com",
    )[1] == 1


def test_reconcile_human_review_assignment_adds_current_group_member_and_outbox(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    _seed_human_review_notification_state(
        repo,
        reviewer_user_ids=["reviewer@example.com"],
    )
    _seed_reviewer_directory(
        tmp_path,
        reviewer_user_ids=["reviewer@example.com", "second@example.com"],
    )

    result = reconcile_current_human_review_assignments(
        repository=repo,
        user_store=AdminWebUserStore(tmp_path / "admin-web.db"),
    )

    assert result["reconciled_human_review_request_ids"] == ["human-review-1"]
    request = repo.read().human_review_requests["human-review-1"]
    assert request.current_assignment.eligible_user_ids == [
        "reviewer@example.com",
        "second@example.com",
    ]
    pending = repo.list_outbox_records(
        status=TeamOutboxStatus.PENDING,
        intent_type="team.release.human_review_requested",
    )
    assert len(pending) == 1
    assert pending[0].payload["payload"]["recipient_user_id"] == "second@example.com"


def test_stale_human_review_notification_is_suppressed_and_request_is_blocked(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    _seed_human_review_notification_state(
        repo,
        reviewer_user_ids=["reviewer@example.com"],
    )
    _seed_reviewer_directory(tmp_path, reviewer_user_ids=[])
    repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-human-review-stale",
            intent_type="team.release.human_review_requested",
            aggregate_type="human_review_request",
            aggregate_id="human-review-1",
            idempotency_key=(
                "team-addon:team.release.human_review_requested:"
                "human-review-1:reviewer@example.com:1"
            ),
            payload={
                "event_type": "team.release.human_review_requested",
                "correlation_key": "human-review-1",
                "payload": {
                    "human_review_request_id": "human-review-1",
                    "release_package_id": "release-1",
                    "candidate_commit_sha": "a" * 40,
                    "candidate_tree_hash": "b" * 40,
                    "recipient_user_id": "reviewer@example.com",
                    "assignment_generation": 1,
                },
            },
        )
    )
    provider = TeamPublishOutboxEventsEffectProvider()
    provider.initialize(
        TeamPublishOutboxEventsEffectDefinition(
            id="publish",
            display_name="Publish outbox",
            type="effect:team:publish-outbox-events",
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["completed_outbox_record_ids"] == ["outbox-human-review-stale"]
    assert result["published_event_ids"] == []
    assert AdminUserNotificationStore(tmp_path).list_unread(
        user_id="reviewer@example.com",
    )[1] == 0
    state = repo.read()
    request = state.human_review_requests["human-review-1"]
    assert request.state == "blocked"
    assert request.current_assignment.eligible_user_ids == []
    assert {
        blocker.reason_code
        for blocker in state.blockers.values()
        if blocker.source_id == request.id
    } == {"human_review_assignment_unavailable"}
