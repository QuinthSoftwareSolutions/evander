from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from team_runtime_support import FakeFileCopier, FakeResumer, FakeSessionStore, _seed_team

from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.registry import EffectProviderFactory
from evander.events.internal import InternalEventBus
from evander.events.scheduled import ScheduledInternalEventStore
from evander.messaging.agent_prompt_lock import AgentPromptLockStore
from evander.orchestration.event_publisher import InternalEventPublisherRuntime
from evander_team_addon.models import TeamOutboxRecord
from evander_team_addon.provider_events import _publish_team_events, _publish_team_outbox_events
from evander_team_addon.provider_schemas import TeamWakeDueAgentsEffectDefinition
from evander_team_addon.provider_wake import TeamWakeDueAgentsEffectProvider
from evander_team_addon.repository import TeamStateConflictError, TeamStateRepository
from evander_team_addon.services.chat_service import TeamChatService


def _queued_chat(repo):
    chat = TeamChatService(repo)
    state, conversation, _ = chat.create_or_get_conversation(
        agent_id="backend", operator_user_id="operator@example.com"
    )
    _, message = chat.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Process this queued message",
        created_by="operator:operator@example.com",
        expected_revision=state.revision,
    )
    chat.queue_chat_message_dispatch(message_id=message.id, dispatch_id=f"team-chat:{message.id}")
    return chat, message


def test_team_reopen_and_snapshots_do_not_require_writer(tmp_path, monkeypatch):
    repo = _seed_team(tmp_path)
    before = repo.read()
    monkeypatch.setattr("evander_team_addon.sqlite_store.SQLITE_BUSY_TIMEOUT_MS", 100)
    with repo.store._connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE team_state_metadata SET value='99999' WHERE key='revision'")
        reopened = TeamStateRepository(tmp_path)
        assert reopened.read().revision == before.revision
        assert repo.read().revision == before.revision
        assert reopened.read_projection(collections=["agents"]).agents == before.agents
        writer.rollback()


def test_chat_planning_allows_writer_and_rechecks_external_activity_after_conflict(tmp_path):
    repo = _seed_team(tmp_path)
    chat, message = _queued_chat(repo)
    concurrent = TeamStateRepository(tmp_path)
    entered, release = Event(), Event()
    checks = []

    def busy(session_id):
        checks.append(session_id)
        if len(checks) == 1:
            entered.set()
            assert release.wait(5)
        return False

    def rename(state):
        state.projects["project"].name = "Changed during activity check"

    with ThreadPoolExecutor(max_workers=2) as pool:
        planned = pool.submit(
            chat.claim_pending_chat_message_dispatches,
            agent_id="backend",
            session_is_busy=busy,
            defer_activation=True,
        )
        assert entered.wait(5)
        try:
            pool.submit(concurrent.update, rename).result(timeout=2)
        finally:
            release.set()
        state, claims = planned.result(timeout=10)
    assert checks == ["session-backend", "session-backend"]
    assert len(claims) == 1
    assert state.projects["project"].name == "Changed during activity check"
    assert repo.read().conversation_messages[message.id].status == "dispatching"


def test_competing_chat_plans_claim_each_message_once(tmp_path):
    repo = _seed_team(tmp_path)
    chat, message = _queued_chat(repo)
    other = TeamChatService(TeamStateRepository(tmp_path))
    entered, release = Event(), Event()

    def busy(_session_id):
        entered.set()
        assert release.wait(5)
        return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            chat.claim_pending_chat_message_dispatches,
            agent_id="backend",
            session_is_busy=busy,
            defer_activation=True,
        )
        assert entered.wait(5)
        second = pool.submit(
            other.claim_pending_chat_message_dispatches, agent_id="backend", defer_activation=True
        )
        release.set()
        _, first_claims = first.result(timeout=10)
        _, second_claims = second.result(timeout=10)
    assert [claim["message_id"] for claim in first_claims + second_claims] == [message.id]
    assert (
        len([d for d in repo.read().dispatches.values() if message.id in d.chat_message_ids]) == 1
    )


def test_chat_conflict_exhaustion_preserves_queue_and_schedules_targeted_retry(
    tmp_path, monkeypatch
):
    repo = _seed_team(tmp_path)
    chat, message = _queued_chat(repo)
    calls = []

    def conflicting_commit(*args, **kwargs):
        calls.append(None)
        raise TeamStateConflictError(expected_revision=41, current_revision=42)

    monkeypatch.setattr(
        TeamStateRepository, "commit_prepared_state_with_command_transaction", conflicting_commit
    )
    resumer = FakeResumer()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="backend",
            limit=1,
            run_global_maintenance=False,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=resumer,
            session_lookup=FakeSessionStore(),
        ),
    )
    result = provider.execute()
    assert len(calls) == 3
    assert result["retry_scheduled_count"] == 1
    assert result["transient_conflicts"][0]["phase"] == "chat_claim_planning"
    assert repo.read().conversation_messages[message.id].status == "queued"
    assert resumer.calls == []
    [retry] = ScheduledInternalEventStore(tmp_path / "orchestration.db").list_all()
    assert retry.payload["agent_id"] == "backend"
    assert retry.payload["reason"] == "state_conflict_retry"


def test_empty_team_events_do_not_initialize_any_publisher():
    # No runtime directory is needed when there is nothing to publish.
    assert _publish_team_events(None, []) == []
    assert _publish_team_events(None, [None, "not an event"]) == []


def test_chat_activity_race_keeps_dispatch_queued_until_actual_prompt_delivery(tmp_path):
    repo = _seed_team(tmp_path)
    _, message = _queued_chat(repo)
    locks = AgentPromptLockStore(tmp_path / "agent-prompt-locks.json")
    checks = []
    enqueue_calls = []

    def claim(owner):
        return locks.claim(
            lock_key="session-backend",
            owner_id=owner,
            target="session-backend",
            vm_name="vm-backend",
            session_id="session-backend",
        )

    def stale_activity_check(**kwargs):
        checks.append(kwargs)
        # A competing prompt claims the session after this check's idle snapshot.
        assert claim("other-prompt").claimed
        return False

    def enqueue(**kwargs):
        enqueue_calls.append(kwargs)
        assert not claim("chat-prompt").claimed
        return {"success": True, "queued": True, "queue_key": "chat-retry"}

    sessions = FakeSessionStore()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake",
            display_name="Wake",
            type="effect:team:wake-due-agents",
            agent_id="backend",
            limit=1,
            run_global_maintenance=False,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            agent_resumer=SimpleNamespace(
                enqueue_agent_prompt=enqueue,
                resume_agent=lambda **kwargs: pytest.fail("must enqueue"),
            ),
            agent_prompt_activity=SimpleNamespace(has_active_enqueue_prompt=stale_activity_check),
            session_lookup=sessions,
            session_metadata_updater=sessions,
            vm_file_copier=FakeFileCopier(),
        ),
    )
    result = provider.execute()
    assert len(checks) == len(enqueue_calls) == 1
    assert result["chat_dispatches"][0]["queued"] is True
    state = repo.read()
    dispatch = state.dispatches[f"team-chat:{message.id}"]
    assert dispatch.status.value == "preparing"
    assert dispatch.queued_at is not None
    assert dispatch.lease_expires_at is None
    assert state.conversation_messages[message.id].status == "dispatching"


def test_event_publishers_are_lazy_reused_and_preserve_mixed_idempotent_events(
    tmp_path, monkeypatch
):
    import evander.orchestration.event_publisher as module

    original_bus = module.build_internal_event_bus_from_metadata
    original_scheduled = module.build_scheduled_internal_event_store_from_metadata
    built = []

    def bus(*args, **kwargs):
        built.append("immediate")
        return original_bus(*args, **kwargs)

    def scheduled(*args, **kwargs):
        built.append("scheduled")
        return original_scheduled(*args, **kwargs)

    monkeypatch.setattr(module, "build_internal_event_bus_from_metadata", bus)
    monkeypatch.setattr(module, "build_scheduled_internal_event_store_from_metadata", scheduled)
    publisher = InternalEventPublisherRuntime(runtime_dir=tmp_path)
    context = EffectRuntimeContext(
        resolved_config={"runtime_dir": str(tmp_path)}, internal_event_publisher=publisher
    )
    immediate = {"event_type": "team.test.immediate", "idempotency_key": "now"}
    delayed = {"event_type": "team.test.delayed", "idempotency_key": "later", "delay_seconds": 5}
    immediate_ids = _publish_team_events(context, [immediate])
    assert built == ["immediate"]
    mixed_ids = _publish_team_events(context, [immediate, delayed])
    assert mixed_ids == _publish_team_events(context, [immediate, delayed])
    assert mixed_ids[0] == immediate_ids[0]
    assert built == ["immediate", "scheduled"]
    assert len(InternalEventBus(tmp_path / "orchestration.db").list_events()) == 1
    assert len(ScheduledInternalEventStore(tmp_path / "orchestration.db").list_all()) == 1


def test_publisher_factory_changes_database_with_configuration(tmp_path):
    factory = EffectProviderFactory(None)
    first = factory.get_internal_event_publisher(runtime_dir=tmp_path)
    first.publish(event_type="team.test.first")
    assert factory.get_internal_event_publisher(runtime_dir=tmp_path) is first
    factory.metadata["orchestration_store_path"] = str(tmp_path / "changed.db")
    second = factory.get_internal_event_publisher(runtime_dir=tmp_path)
    second.publish(event_type="team.test.second")
    assert [
        e.event_type for e in InternalEventBus(tmp_path / "orchestration.db").list_events()
    ] == ["team.test.first"]
    assert [e.event_type for e in InternalEventBus(tmp_path / "changed.db").list_events()] == [
        "team.test.second"
    ]


def test_outbox_batch_reuses_publisher(tmp_path, monkeypatch):
    repo = TeamStateRepository(tmp_path)
    for name in ("first", "second"):
        repo.enqueue_outbox_record(
            TeamOutboxRecord(
                id=name,
                intent_type="team.test",
                aggregate_type="test",
                aggregate_id=name,
                idempotency_key=name,
                payload={"event_type": "team.test", "payload": {"name": name}},
            )
        )
    import evander_team_addon.provider_events as module

    publishers = []
    original = module._event_publisher

    def build(context):
        publisher = original(context)
        publishers.append(publisher)
        return publisher

    monkeypatch.setattr(module, "_event_publisher", build)
    context = EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)})
    result = _publish_team_outbox_events(
        context=context,
        repo=repo,
        worker_id="test",
        limit=25,
        claim_ttl_seconds=60,
        retry_after_seconds=1,
    )
    assert result["published_count"] == 2
    assert len(publishers) == 1
    assert len(InternalEventBus(tmp_path / "orchestration.db").list_events()) == 2


@pytest.mark.parametrize("conflict", ["revision", "activity"])
def test_prepared_maintenance_finalization_rejects_changed_evidence_then_recovers(
    tmp_path, monkeypatch, conflict
):
    from evander_team_addon.models import TeamDispatchActivity, TeamDispatchStatus
    from evander_team_addon.services.runtime_service import TeamRuntimeService
    from evander_team_addon.services.prepared_finalization import DispatchActivityConflictError

    repo = _seed_team(tmp_path)
    _, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        respect_wake_interval=False,
    )
    dispatch_id = leases[0]["dispatch_id"]
    maintenance = TeamStateRepository(tmp_path, maintenance=True)
    prepare = maintenance.store._prepare_updated_state
    injected = False

    def concurrent_change(**kwargs):
        nonlocal injected
        if not injected:
            injected = True
            # This independent writer also proves finalization validation runs
            # before the maintenance writer is acquired.
            if conflict == "revision":
                repo.update(
                    lambda state: setattr(state.projects["project"], "name", "Concurrent change")
                )
            else:
                repo.append_dispatch_activity(
                    TeamDispatchActivity(
                        id="late-activity",
                        tool_name="team_report_progress",
                        caller_session_id="session-backend",
                        caller_identity="agent:backend",
                        agent_id="backend",
                        dispatch_id=dispatch_id,
                        status="success",
                        progress_classification="progress",
                    )
                )
        return prepare(**kwargs)

    monkeypatch.setattr(maintenance.store, "_prepare_updated_state", concurrent_change)
    expected = TeamStateConflictError if conflict == "revision" else DispatchActivityConflictError
    with pytest.raises(expected):
        TeamRuntimeService(maintenance).finalize_agent_dispatch_from_activity(
            session_id="session-backend",
            dispatch_id=dispatch_id,
        )
    assert repo.read().dispatches[dispatch_id].status == TeamDispatchStatus.LEASED
    assert not repo.list_dispatch_activity(
        dispatch_id=dispatch_id, tool_name="team_dispatch_finished"
    )
    _, result = TeamRuntimeService(maintenance).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=dispatch_id,
    )
    assert result["finalized"]
    assert ("late-activity" in result["activity_ids"]) is (conflict == "activity")
    assert (
        len(
            repo.list_dispatch_activity(dispatch_id=dispatch_id, tool_name="team_dispatch_finished")
        )
        == 1
    )
    if conflict == "revision":
        assert repo.read().projects["project"].name == "Concurrent change"


def test_large_activity_retention_waits_for_offline_index_and_prunes_bounded_batch(tmp_path):
    import sqlite3
    from datetime import timedelta
    from evander.models import utc_now
    from evander_team_addon.models import TeamDispatchActivity
    from evander_team_addon.sqlite_schema import (
        check_team_activity_retention_index,
        prepare_team_activity_retention_index,
    )

    repo = TeamStateRepository(tmp_path)
    old = utc_now() - timedelta(days=40)
    activity = TeamDispatchActivity(
        id="seed",
        tool_name="team_report_progress",
        caller_session_id="session",
        caller_identity="agent:worker",
        agent_id="worker",
        dispatch_id="dispatch",
        status="success",
        created_at=old,
        updated_at=old,
    )
    repo.append_dispatch_activity(activity)
    assert check_team_activity_retention_index(repo.store.path)
    with sqlite3.connect(repo.store.path) as connection:
        connection.execute("DROP INDEX idx_team_dispatch_activity_retention")
        row = list(
            connection.execute("SELECT * FROM team_dispatch_activity WHERE id='seed'").fetchone()
        )
        placeholders = ",".join("?" for _ in row)
        connection.executemany(
            f"INSERT INTO team_dispatch_activity VALUES ({placeholders})",
            [(f"old-{i}", *row[1:]) for i in range(10000)],
        )
        connection.execute(
            "UPDATE team_state_metadata SET value='79' WHERE key='sqlite_schema_version'"
        )
    reopened = TeamStateRepository(tmp_path)
    assert not check_team_activity_retention_index(repo.store.path)
    recent = activity.model_copy(
        update={"id": "recent", "created_at": utc_now(), "updated_at": utc_now()}
    )
    reopened.append_dispatch_activity(recent)
    with sqlite3.connect(repo.store.path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM team_dispatch_activity").fetchone()[0] == 10002
        )
    prepare_team_activity_retention_index(repo.store.path)
    reopened.append_dispatch_activity(recent.model_copy(update={"id": "recent-2"}))
    with sqlite3.connect(repo.store.path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM team_dispatch_activity").fetchone()[0] == 9903
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM team_dispatch_activity WHERE id IN ('recent','recent-2')"
            ).fetchone()[0]
            == 2
        )
