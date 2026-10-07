from __future__ import annotations

from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
import sqlite3
from types import SimpleNamespace

import pytest

from evander.effects.providers.base import EffectRuntimeContext
from evander.messaging.agent_prompt_queue import AgentPromptQueueStore, QueuedAgentPromptRecord
from evander_team_addon.models import (
    AgentContextClearingPolicy,
    TeamDispatchMcpCapabilityReceipt,
    TeamDispatchStatus,
    TeamMemoryRecord,
    TeamMemoryStatus,
    TeamIdempotencyRecord,
    TeamOutboxRecord,
)
from evander_team_addon.provider_completion import TeamAgentPromptCompletionFinalizeHook
from evander_team_addon.provider_schemas import TeamWakeDueAgentsEffectDefinition
from evander_team_addon.provider_wake import TeamWakeDueAgentsEffectProvider
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.services.runtime_service import (
    AgentDispatchNativeThreadReceipt,
    TeamRuntimeService,
)
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.sqlite_dispatch_bookkeeping import TeamDispatchBookkeepingConflictError
from evander_team_addon.sqlite_store import TeamStateReadProjection, TeamStateSqliteStore

from team_runtime_support import FakeFileCopier, FakeResumer, FakeSessionStore, _seed_team


NOW = datetime(2026, 1, 1, tzinfo=UTC)
NATIVE = "01a085f1-767c-7592-a03e-b276a20b2aa1"
COLLECTIONS = ("agents", "dispatches", "agent_runtimes")


def _reserved(tmp_path):
    repo = _seed_team(tmp_path)
    TeamAdminService(repo).update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.WORK_LINEAGE,
        set_context_clearing_policy=True,
        expected_revision=repo.read_revision(),
    )
    runtime = TeamRuntimeService(repo)
    state, leases = runtime.lease_due_agents(agent_id="backend", now=NOW, defer_activation=True)
    return repo, runtime, state, leases[0]["dispatch_id"]


def _native_ready(tmp_path):
    repo, runtime, _state, dispatch_id = _reserved(tmp_path)
    runtime.activate_delivered_agent_dispatch(dispatch_id=dispatch_id, now=NOW)
    return repo, runtime, dispatch_id


def _no_full_state(*_args, **_kwargs):
    raise AssertionError("dispatch bookkeeping reconstructed complete Team state")


def test_native_receipt_write_is_bounded_and_preserves_archived_history(tmp_path, monkeypatch):
    repo, runtime, dispatch_id = _native_ready(tmp_path)

    def archive(state):
        for number in range(80):
            archived = state.dispatches[dispatch_id].model_copy(
                update={
                    "id": f"archived-{number}",
                    "status": TeamDispatchStatus.COMPLETED,
                    "completed_at": NOW,
                },
                deep=True,
            )
            state.dispatches[archived.id] = archived
            memory = TeamMemoryRecord(
                id=f"memory-{number}",
                team_id="team",
                title="Archived history",
                summary="Historical context",
                body="archived context " * 400,
                status=TeamMemoryStatus.ARCHIVED,
            )
            state.memories[memory.id] = memory

    before = repo.update(archive)
    before_activity = repo.list_dispatch_activity()
    snapshot = repo.read_projection(collections=COLLECTIONS)
    original_reader = repo.store._read_dispatches
    rows_read = []

    def tracked_reader(connection, payload, **kwargs):
        original_reader(connection, payload, **kwargs)
        rows_read.append(set(payload["dispatches"]))

    with monkeypatch.context() as patch:
        patch.setattr(repo.store, "_read_state", _no_full_state)
        patch.setattr(repo.store, "_read_state_payload", _no_full_state)
        patch.setattr(repo.store, "_read_dispatches", tracked_reader)
        after, deferred = runtime.reconcile_agent_dispatch_native_threads(
            state=snapshot,
            receipts=[
                AgentDispatchNativeThreadReceipt("session-backend", dispatch_id, NATIVE, NOW)
            ],
            now=NOW,
        )
    assert rows_read == [{dispatch_id}]
    assert isinstance(after, TeamStateReadProjection)
    with pytest.raises(ValueError, match="did not load"):
        after.memories
    assert deferred == set()
    assert after.revision == before.revision + 1
    assert after.dispatches[dispatch_id].native_thread_id == NATIVE
    # Snapshot updates must not mutate the snapshot a concurrent reader holds.
    assert snapshot.dispatches[dispatch_id].native_thread_id is None
    persisted = TeamStateRepository(tmp_path).read()
    assert persisted.memories == before.memories
    assert {key: row for key, row in persisted.dispatches.items() if key != dispatch_id} == {
        key: row for key, row in before.dispatches.items() if key != dispatch_id
    }
    assert repo.list_outbox_records() == []
    assert repo.list_dispatch_activity() == before_activity


def test_queued_marker_and_mcp_grant_use_current_snapshot_without_full_state(tmp_path, monkeypatch):
    repo, runtime, state, dispatch_id = _reserved(tmp_path)

    def receipt(state):
        state.dispatches[dispatch_id].mcp_capability_receipt = TeamDispatchMcpCapabilityReceipt(
            required_tool_names=["team_decide_review"],
        )

    state = repo.update(receipt)
    before_revision = state.revision
    with monkeypatch.context() as patch:
        patch.setattr(repo.store, "_read_state", _no_full_state)
        patch.setattr(repo.store, "_read_state_payload", _no_full_state)
        state = runtime.mark_agent_dispatch_queued(
            agent_id="backend",
            dispatch_id=dispatch_id,
            now=NOW,
            state=state,
        )
        state = runtime.confirm_agent_dispatch_mcp_capability_grant(
            agent_id="backend",
            dispatch_id=dispatch_id,
            granted_tool_names=["team_decide_review"],
            now=NOW,
            state=state,
        )
        replay = runtime.mark_agent_dispatch_queued(
            agent_id="backend",
            dispatch_id=dispatch_id,
            now=NOW,
            state=state,
        )
    assert replay.revision == before_revision + 2
    assert state.dispatches[dispatch_id].queued_at == NOW
    assert state.dispatches[dispatch_id].mcp_capability_receipt.grant_confirmed_at == NOW
    assert state == repo.read()


@pytest.mark.parametrize("failure", ["wrong_agent", "lost_owner", "missing_tools", "terminal"])
def test_bookkeeping_rejects_invalid_dispatch_authority_without_partial_writes(tmp_path, failure):
    repo, runtime, state, dispatch_id = _reserved(tmp_path)

    def setup(state):
        state.dispatches[dispatch_id].mcp_capability_receipt = TeamDispatchMcpCapabilityReceipt(
            required_tool_names=["team_decide_review"],
        )
        if failure == "lost_owner":
            state.agent_runtimes["backend"].active_dispatch_id = None
        elif failure == "terminal":
            state.dispatches[dispatch_id].status = TeamDispatchStatus.COMPLETED
            state.dispatches[dispatch_id].completed_at = NOW
            state.agent_runtimes["backend"].active_dispatch_id = None

    state = repo.update(setup)
    with pytest.raises(ValueError):
        runtime.confirm_agent_dispatch_mcp_capability_grant(
            agent_id="manager" if failure == "wrong_agent" else "backend",
            dispatch_id=dispatch_id,
            granted_tool_names=[] if failure == "missing_tools" else ["team_decide_review"],
            state=state,
            now=NOW,
        )
    assert repo.read() == state


@pytest.mark.parametrize("mutation", ["scope", "runtime_identity", "new_row", "lineage", "receipt"])
def test_store_rejects_relationship_changes_and_rolls_back_all_rows(tmp_path, mutation):
    repo, _runtime, state, dispatch_id = _reserved(tmp_path)

    def receipt(state):
        state.dispatches[dispatch_id].mcp_capability_receipt = TeamDispatchMcpCapabilityReceipt(
            required_tool_names=["team_decide_review"],
        )

    state = repo.update(receipt)

    def mutate(scope):
        # A permitted write to a second row must roll back with the invalid change.
        scope.agent_runtimes["backend"].updated_at = NOW
        dispatch = scope.dispatches[dispatch_id]
        if mutation == "scope":
            dispatch.project_ids = []
        elif mutation == "runtime_identity":
            scope.agent_runtimes["backend"].agent_id = "manager"
        elif mutation == "new_row":
            scope.dispatches["invented"] = dispatch.model_copy(update={"id": "invented"})
        elif mutation == "lineage":
            scope.agent_runtimes["backend"].work_lineage_thread_ids["missing-lineage"] = NATIVE
        else:
            dispatch.mcp_capability_receipt.required_tool_names = []

    with pytest.raises(ValueError):
        repo.store.update_dispatch_bookkeeping(
            mutate, dispatch_ids=[dispatch_id], include_state=False
        )
    assert repo.read() == state


def test_callback_failure_rolls_back_bookkeeping_and_revision(tmp_path):
    repo, _runtime, state, dispatch_id = _reserved(tmp_path)

    def fail(scope):
        scope.dispatches[dispatch_id].queued_at = NOW
        raise RuntimeError("simulated domain failure")

    with pytest.raises(RuntimeError, match="simulated"):
        repo.store.update_dispatch_bookkeeping(
            fail, dispatch_ids=[dispatch_id], include_state=False
        )
    assert TeamStateRepository(tmp_path).read() == state


def test_storage_failure_rolls_back_dispatch_runtime_and_preserves_command_journal(tmp_path):
    repo, runtime, dispatch_id = _native_ready(tmp_path)
    key = dict(
        tool_name="team_report",
        caller_identity="agent:backend",
        scope_kind="dispatch",
        scope_id=dispatch_id,
        request_id="report-request",
    )

    def journal(state, tx):
        tx.store_idempotency_record(
            TeamIdempotencyRecord(
                id="existing-receipt",
                **key,
                normalized_payload_digest=repo.normalized_payload_digest({"progress": "saved"}),
                response_payload={"status": "saved"},
            )
        )
        tx.enqueue_outbox_record(
            TeamOutboxRecord(
                id="existing-event",
                intent_type="internal_event",
                payload={"kind": "progress"},
            )
        )

    state = repo.update_with_command_transaction(journal)
    before_receipt = repo.get_idempotency_record(**key)
    before_outbox = repo.list_outbox_records()
    before_activity = repo.list_dispatch_activity()
    with sqlite3.connect(repo.store.path) as connection:
        connection.execute("""CREATE TRIGGER reject_runtime_write BEFORE UPDATE ON team_agent_runtimes
                              BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated storage failure"):
        runtime.reconcile_agent_dispatch_native_threads(
            state=repo.read_projection(collections=COLLECTIONS),
            receipts=[
                AgentDispatchNativeThreadReceipt("session-backend", dispatch_id, NATIVE, NOW)
            ],
        )
    assert TeamStateRepository(tmp_path).read() == state
    assert repo.get_idempotency_record(**key) == before_receipt
    assert repo.list_outbox_records() == before_outbox
    assert repo.list_dispatch_activity() == before_activity


def test_stale_revision_prevents_callback_and_snapshot_refresh_preserves_concurrent_changes(
    tmp_path,
):
    repo, runtime, snapshot, dispatch_id = _reserved(tmp_path)
    other = TeamStateRepository(tmp_path)
    other.update(lambda state: setattr(state.projects["project"], "name", "Concurrent edit"))
    callbacks = []
    with pytest.raises(TeamDispatchBookkeepingConflictError):
        repo.store.update_dispatch_bookkeeping(
            lambda scope: callbacks.append(scope),
            dispatch_ids=[dispatch_id],
            expected_revision=snapshot.revision,
            include_state=False,
        )
    assert callbacks == []
    updated = runtime.mark_agent_dispatch_queued(
        agent_id="backend",
        dispatch_id=dispatch_id,
        now=NOW,
        state=snapshot,
    )
    assert updated.projects["project"].name == "Concurrent edit"
    assert updated.revision == snapshot.revision + 2
    assert updated == repo.read()


def _race_bookkeeping_revisions(repo, monkeypatch, *, mutate_current=None):
    """Commit through another repository in each snapshot-to-writer gap."""
    other = TeamStateRepository(repo.runtime_dir)
    original = TeamStateSqliteStore.update_dispatch_bookkeeping
    attempts = []

    def racing_update(store, updater, **kwargs):
        attempts.append(kwargs.get("expected_revision"))

        def concurrent_edit(state):
            state.projects["project"].name = f"Concurrent edit {len(attempts)}"
            if mutate_current is not None:
                mutate_current(state, len(attempts))

        other.update(concurrent_edit)
        return original(store, updater, **kwargs)

    monkeypatch.setattr(TeamStateSqliteStore, "update_dispatch_bookkeeping", racing_update)
    return attempts


@pytest.mark.parametrize("projected", [False, True])
@pytest.mark.parametrize("change_marker", [False, True])
def test_exhausted_snapshot_retries_return_fresh_state_and_run_callback_once(
    tmp_path, monkeypatch, projected, change_marker,
):
    repo, _runtime, baseline, dispatch_id = _reserved(tmp_path)
    snapshot = (
        repo.read_projection(collections=(*COLLECTIONS, "projects")) if projected else baseline
    )
    if projected:
        monkeypatch.setattr(repo.store, "_read_state", _no_full_state)
    attempts = _race_bookkeeping_revisions(repo, monkeypatch)
    callbacks = []

    def mutate(scope):
        callbacks.append(scope.revision)
        if change_marker:
            scope.dispatches[dispatch_id].queued_at = NOW

    updated = repo.update_dispatch_bookkeeping(
        mutate, dispatch_ids=[dispatch_id], snapshot=snapshot,
    )

    assert len(attempts) == 4 and attempts[-1] is None
    assert callbacks == [baseline.revision + 4]
    assert updated.revision == baseline.revision + 4 + int(change_marker)
    assert updated.projects["project"].name == "Concurrent edit 4"
    assert snapshot.projects["project"].name == "Project"
    assert snapshot.dispatches[dispatch_id].queued_at is None
    persisted = TeamStateRepository(tmp_path).read()
    assert updated.dispatches == persisted.dispatches
    assert updated.agent_runtimes == persisted.agent_runtimes
    if projected:
        assert isinstance(updated, TeamStateReadProjection)
        assert updated._loaded_collections == snapshot._loaded_collections
        with pytest.raises(ValueError, match="did not load"):
            updated.memories
        with pytest.raises(ValueError, match="projections cannot be persisted"):
            repo.store.commit_prepared_state_with_command_transaction(
                previous_payload=persisted.model_dump(mode="json"),
                candidate=updated,
                command_writer=lambda _tx: None,
            )
    else:
        assert updated == persisted


def test_exhausted_snapshot_retry_fallback_still_rejects_lost_dispatch_owner(
    tmp_path, monkeypatch,
):
    repo, runtime, snapshot, dispatch_id = _reserved(tmp_path)

    def release_owner(state, attempt):
        if attempt == 4:
            state.agent_runtimes["backend"].active_dispatch_id = None

    attempts = _race_bookkeeping_revisions(repo, monkeypatch, mutate_current=release_owner)
    with pytest.raises(ValueError, match="no longer owns"):
        runtime.mark_agent_dispatch_queued(
            agent_id="backend", dispatch_id=dispatch_id, now=NOW, state=snapshot,
        )
    persisted = repo.read()
    assert len(attempts) == 4
    assert persisted.revision == snapshot.revision + 4
    assert persisted.dispatches[dispatch_id].queued_at is None
    assert persisted.agent_runtimes["backend"].active_dispatch_id is None


@pytest.mark.parametrize("chat_message", [False, True])
def test_revision_churn_preserves_accepted_queued_ordinary_and_chat_prompts(
    tmp_path, monkeypatch, chat_message,
):
    repo = _seed_team(tmp_path)
    message_id = None
    if chat_message:
        chat = TeamChatService(repo)
        state, conversation, _created = chat.create_or_get_conversation(
            agent_id="backend", operator_user_id="operator@example.com",
            expected_revision=repo.read_revision(),
        )
        state, message = chat.record_operator_chat_message(
            conversation_id=conversation.id, body="Handle this queued operator request.",
            created_by="operator:operator@example.com", expected_revision=state.revision,
        )
        message_id = message.id
        chat.queue_chat_message_dispatch(
            message_id=message.id, dispatch_id=f"team-chat:{message.id}",
        )
    queue = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")

    def persist_accepted_prompt(kwargs):
        queue.put(
            QueuedAgentPromptRecord(
                queue_key="accepted-team-prompt", target=kwargs["target"],
                session_id=kwargs["target"], vm_name="vm-backend", prompt=kwargs["prompt"],
                dispatch_id=kwargs["dispatch_id"], effect_id="wake",
            ),
            count_retry=False,
        )

    resumer = FakeResumer(
        {"success": True, "queued": True, "queue_key": "accepted-team-prompt"},
        on_resume=persist_accepted_prompt,
    )
    sessions = FakeSessionStore()
    provider = TeamWakeDueAgentsEffectProvider()
    provider.initialize(
        TeamWakeDueAgentsEffectDefinition(
            id="wake", display_name="Wake", type="effect:team:wake-due-agents",
            run_global_maintenance=False, limit=1,
        ),
        EffectRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)}, agent_resumer=resumer,
            vm_file_copier=FakeFileCopier(), session_lookup=sessions,
            session_metadata_updater=sessions,
        ),
    )
    attempts = _race_bookkeeping_revisions(repo, monkeypatch)
    result = provider.execute()
    # Another wake cycle must keep the durable reservation instead of submitting twice.
    repeated = provider.execute()
    state = repo.read()
    accepted = queue.read_all()
    assert len(attempts) == 4
    assert result["success"] and repeated["success"]
    assert result["retry_event_ids"] == repeated["retry_event_ids"] == []
    assert result["transient_conflicts"] == repeated["transient_conflicts"] == []
    assert len(resumer.calls) == len(accepted) == len(state.dispatches) == 1
    record = accepted["accepted-team-prompt"]
    assert record.coalesced_count == 1
    dispatch = state.dispatches[record.dispatch_id]
    assert dispatch.status == TeamDispatchStatus.PREPARING
    assert dispatch.queued_at is not None
    assert state.agent_runtimes["backend"].active_dispatch_id == dispatch.id
    worker = SimpleNamespace(config=SimpleNamespace(runtime_dir=tmp_path))
    assert TeamAgentPromptCompletionFinalizeHook().validate_queued_prompt(
        worker=worker, record=record,
    )
    if message_id is not None:
        assert state.conversation_messages[message_id].status == "dispatching"
        assert state.conversation_messages[message_id].error is None


def test_newer_receipt_cannot_restore_invalidated_lineage_or_old_session(tmp_path):
    repo, runtime, dispatch_id = _native_ready(tmp_path)

    def bind(state):
        agent_runtime = state.agent_runtimes["backend"]
        agent_runtime.context_clearing_policy = AgentContextClearingPolicy.WORK_LINEAGE
        agent_runtime.work_lineage_thread_ids[state.dispatches[dispatch_id].work_lineage_id] = (
            NATIVE
        )
        agent_runtime.work_lineage_thread_dispatch_ids[
            state.dispatches[dispatch_id].work_lineage_id
        ] = dispatch_id

    repo.update(bind)
    state, _ = runtime.record_agent_dispatch_native_thread(
        session_id="session-backend",
        dispatch_id=dispatch_id,
        thread_id=NATIVE,
        observed_at=NOW,
    )
    state, invalidated = runtime.invalidate_agent_work_lineage_thread_binding(
        agent_id="backend",
        dispatch_id=dispatch_id,
        resume_thread_id=NATIVE,
    )
    assert invalidated
    updated, deferred = runtime.reconcile_agent_dispatch_native_threads(
        state=state,
        receipts=[
            AgentDispatchNativeThreadReceipt(
                "session-backend", dispatch_id, NATIVE, NOW + timedelta(seconds=1)
            )
        ],
    )
    assert deferred == set()
    assert updated.agent_runtimes["backend"].work_lineage_thread_ids == {}
    state, recorded = runtime.record_agent_dispatch_native_thread(
        session_id="session-other",
        dispatch_id=dispatch_id,
        thread_id=NATIVE,
    )
    assert not recorded and state.revision == updated.revision


def _competing_queue_marker(runtime_dir, dispatch_id, barrier, result):
    try:
        repo = TeamStateRepository(runtime_dir)
        snapshot = repo.read_projection(collections=COLLECTIONS)
        barrier.wait(timeout=10)
        state = TeamRuntimeService(repo).mark_agent_dispatch_queued(
            agent_id="backend",
            dispatch_id=dispatch_id,
            now=NOW,
            state=snapshot,
        )
        result.put(("ok", state.revision))
    except Exception as exc:
        result.put(("error", repr(exc)))


def test_competing_processes_commit_one_identical_marker_without_lost_revision(tmp_path):
    repo, _runtime, state, dispatch_id = _reserved(tmp_path)
    context = get_context("spawn")
    barrier = context.Barrier(2)
    result = context.Queue()
    processes = [
        context.Process(
            target=_competing_queue_marker,
            args=(tmp_path, dispatch_id, barrier, result),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        received = [result.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=5)
        assert received == [("ok", state.revision + 1)] * 2
        assert all(process.exitcode == 0 for process in processes)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result.close()
        result.join_thread()
    persisted = repo.read()
    assert persisted.revision == state.revision + 1
    assert persisted.dispatches[dispatch_id].queued_at == NOW
