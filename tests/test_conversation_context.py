from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event, current_thread
from types import SimpleNamespace

import pytest

from evander.admin.composition import build_admin_runtime
from evander.integration_services.teams import teams_conversation_key
from evander.conversation_context import (
    ConversationContextStore,
    launch_context,
    reject_launch_context,
)
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.session_reset_context import SessionResetContextEffectProvider
from evander.events.provider_models import (
    SessionResetContextEffectDefinition,
    CommunicationMessagesQueryDefinition,
)
from evander.gateway.communication_attachment_helpers import _filter_teams_messages_for_session
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.messaging.teams_inbox_store import TeamsInboxStore, TeamsInboxMessage
from evander.models import SessionManifest, TeamsMessageEnvelope
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.communication_messages import CommunicationMessagesQueryProvider

KEY = teams_conversation_key("chat:one")


def reset(store, request_id="request-1", session_id="session-1", key=KEY):
    return store.request_reset(
        session_id=session_id,
        conversation_key=key,
        request_id=request_id,
    )


def runtime_with_assistant(tmp_path):
    admin = build_admin_runtime(tmp_path / "runtime")
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(admin.runtime_dir)
    )
    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "assistant-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "chat:one",
                    "policy": {"capabilities": ["read", "write"]},
                }
            ]
        },
    )
    admin.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at="2099-01-01T00:00:00Z",
            teams_chat_allowlist=["chat:one"],
            model="fixture-model",
            metadata={
                "agent_thread_ids": {KEY: "old-thread", "other-conversation": "other-thread"},
                "personal_assistant_model": "fixture-model",
                "preferences": {"language": "Swedish"},
            },
        )
    )
    return admin


def test_provider_records_reset_only_for_bound_session_and_keeps_configuration(tmp_path):
    admin = runtime_with_assistant(tmp_path)
    original = admin.runtime.session_repository.get_session("session-1")
    provider = SessionResetContextEffectProvider()
    provider.initialize(
        SessionResetContextEffectDefinition(
            id="reset",
            display_name="Reset",
            type="effect:session:reset-context",
            target="session-1",
            conversation_key=KEY,
            request_id="message-1",
        ),
        EffectRuntimeContext(session_context_resetter=AdminRuntimeProviderAdapter(admin)),
    )
    result = provider.execute()
    stored = admin.runtime.session_repository.get_session("session-1")
    assert result["generation"] == 1 and result["pending"]
    assert stored.metadata["agent_thread_ids"] == {KEY: None, "other-conversation": "other-thread"}
    assert stored.model == original.model
    assert stored.metadata["preferences"] == original.metadata["preferences"]
    assert stored.metadata["personal_assistant_model"] == "fixture-model"
    assert stored.allowed_tools == original.allowed_tools


@pytest.mark.parametrize(
    "changes",
    [
        {"conversation_key": " "},
        {"directory_path": "root/another-team"},
        {"target": "missing"},
        {"request_id": ""},
    ],
)
def test_reset_rejects_wrong_conversation_scope_and_missing_identity(tmp_path, changes):
    admin = runtime_with_assistant(tmp_path)
    with pytest.raises(ValueError):
        admin.conversation_context_admin.reset_context(
            **{
                "target": "session-1",
                "conversation_key": KEY,
                "request_id": "one",
                **changes,
            }
        )
    assert (
        admin.runtime.session_repository.get_session("session-1").metadata["agent_thread_ids"][KEY]
        == "old-thread"
    )


def test_retry_and_restart_do_not_reset_a_newly_accepted_thread(tmp_path):
    admin = runtime_with_assistant(tmp_path)
    service = admin.conversation_context_admin
    command = dict(
        target="session-1",
        conversation_key=KEY,
        request_id="request-1",
    )
    service.reset_context(**command)
    store = ConversationContextStore(admin.runtime_dir)
    assert launch_context(admin.runtime_dir, "session-1", KEY, "launch-1")["pending"]
    assert store.accept_thread("session-1", KEY, 1, "fresh-thread")
    admin.issue_git_admin.session_update_metadata(
        target="session-1", metadata={"agent_thread_ids": {KEY: "fresh-thread"}}
    )
    result = service.reset_context(**command)
    assert result["duplicate"] and result["generation"] == 1
    assert (
        ConversationContextStore(admin.runtime_dir).get("session-1", KEY)["thread_id"]
        == "fresh-thread"
    )
    assert (
        admin.runtime.session_repository.get_session("session-1").metadata["agent_thread_ids"][KEY]
        == "fresh-thread"
    )
    assert service.reset_context(**{**command, "request_id": "request-2"})["generation"] == 2
    assert not store.accept_thread("session-1", KEY, 1, "late-thread")
    assert store.get("session-1", KEY)["pending"]


def test_late_completion_cannot_restore_manifest_or_canonical_pointer(tmp_path):
    admin = runtime_with_assistant(tmp_path)
    admin.conversation_context_admin.reset_context(
        target="session-1",
        conversation_key=KEY,
        request_id="one",
    )
    worker = SimpleNamespace(
        config=admin.config,
        provider_factory=SimpleNamespace(admin_service=AdminRuntimeProviderAdapter(admin)),
    )
    record = SimpleNamespace(
        resolved_config={"resume_thread_metadata_key": KEY},
        input_payload={},
        outputs={"context_generation": 0},
    )
    AgentPromptEffectProvider._persist_container_session_thread_id(
        worker=worker, record=record, session_id="session-1", thread_id="old-thread"
    )
    assert (
        admin.runtime.session_repository.get_session("session-1").metadata["agent_thread_ids"][KEY]
        is None
    )
    assert ConversationContextStore(admin.runtime_dir).get("session-1", KEY)["thread_id"] is None
    launch_context(admin.runtime_dir, "session-1", KEY, "fresh-launch")
    record.outputs["context_generation"] = 1
    AgentPromptEffectProvider._persist_container_session_thread_id(
        worker=worker, record=record, session_id="session-1", thread_id="fresh-thread"
    )
    assert (
        admin.runtime.session_repository.get_session("session-1").metadata["agent_thread_ids"][KEY]
        == "fresh-thread"
    )
    assert not ConversationContextStore(admin.runtime_dir).get("session-1", KEY)["pending"]


def test_first_reset_racing_with_old_completion_keeps_the_pointer_cleared(tmp_path, monkeypatch):
    admin = runtime_with_assistant(tmp_path)
    store = ConversationContextStore(admin.runtime_dir)
    reached_guard, reset_finished = Event(), Event()
    original_get = ConversationContextStore.get
    original_lock = ConversationContextStore.lock

    def is_callback():
        return current_thread().name.startswith("late-callback")

    def observed_get(self, *args):
        state = original_get(self, *args)
        if is_callback():
            reached_guard.set()
        return state

    @contextmanager
    def observed_lock(self, *args):
        if is_callback():
            reached_guard.set()
        with original_lock(self, *args):
            yield

    def late_metadata_update(**kwargs):
        # A callback admitted before the first reset must not write after it.
        assert reset_finished.wait(3)
        return admin.issue_git_admin.session_update_metadata(**kwargs)

    monkeypatch.setattr(ConversationContextStore, "get", observed_get)
    monkeypatch.setattr(ConversationContextStore, "lock", observed_lock)
    worker = SimpleNamespace(
        config=admin.config,
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(session_update_metadata=late_metadata_update)
        ),
    )
    record = SimpleNamespace(
        resolved_config={"resume_thread_metadata_key": KEY},
        input_payload={},
        outputs={"context_generation": 0},
    )
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="late-callback") as pool:
        try:
            # Hold the same lock as the reset service while the callback arrives.
            with store.lock("session-1", KEY):
                callback = pool.submit(
                    AgentPromptEffectProvider._persist_container_session_thread_id,
                    worker=worker,
                    record=record,
                    session_id="session-1",
                    thread_id="old-thread",
                )
                assert reached_guard.wait(3)
                reset(store)
                admin.issue_git_admin.session_update_metadata(
                    target="session-1", metadata={"agent_thread_ids": {KEY: None}}
                )
        finally:
            reset_finished.set()
        callback.result(timeout=3)
    assert store.get("session-1", KEY)["thread_id"] is None
    assert (
        admin.runtime.session_repository.get_session("session-1").metadata["agent_thread_ids"][KEY]
        is None
    )


def test_context_generation_isolation_and_failed_launch_recovery(tmp_path):
    store = ConversationContextStore(tmp_path)
    assert store.get("session-1", KEY) is None
    reset(store)
    attempt = launch_context(tmp_path, "session-1", KEY, "attempt")
    retried = launch_context(tmp_path, "session-1", KEY, "attempt")
    assert retried["history_after"] == attempt["history_after"]
    assert store.get("session-1", KEY)["pending"]  # Admission/failed launch does not consume it.
    assert not store.accept_thread("session-1", KEY, 0, "old")
    assert store.get("session-2", KEY) is None
    assert store.get("session-1", "other") is None
    reset(store, "two")
    assert not store.accept_thread("session-1", KEY, 1, "superseded-launch")
    assert launch_context(tmp_path, "session-1", KEY, "new")["generation"] == 2
    assert store.accept_thread("session-1", KEY, 2, "new-thread")
    assert launch_context(tmp_path, "session-1", KEY, "later")["thread_id"] == "new-thread"


def test_query_and_agent_history_filter_before_limits_and_leave_stored_messages(tmp_path):
    admin = runtime_with_assistant(tmp_path)
    runtime_dir = admin.runtime_dir
    store = ConversationContextStore(runtime_dir)
    old = datetime.now(UTC) - timedelta(hours=1)
    reset(store)
    # A running turn still sees its existing history boundary.
    sample = SimpleNamespace(created_at=old)
    assert store.filter_history([sample], session_id="session-1", conversation_key=KEY) == [sample]
    assert (
        store.filter_history(
            [sample], session_id="session-1", conversation_key=KEY, classification=True
        )
        == []
    )
    launch_context(runtime_dir, "session-1", KEY, "first")
    assert store.accept_thread("session-1", KEY, 1, "fresh")
    after = datetime.now(UTC) + timedelta(seconds=1)
    inbox = TeamsInboxStore(runtime_dir / "teams-inbox-store.db")
    for cursor, timestamp in [("1", old), ("2", after), ("3", old)]:
        # The last record is a late-ingested old message, not a fresh-context message.
        inbox.add_message_if_new(
            TeamsInboxMessage(
                cursor=cursor,
                message_id=cursor,
                team_id="",
                channel_id="",
                chat_id="one",
                created_at=timestamp,
                author_id="user",
                content=cursor,
                metadata={"source": "fixture", "target": "chat:one", "chat_id": "one"},
            )
        )
    provider = CommunicationMessagesQueryProvider()
    provider.initialize(
        CommunicationMessagesQueryDefinition(
            id="history",
            display_name="History",
            type="query:communication:messages",
            communication_target_id="assistant-chat",
            before_cursor="4",
            limit=1,
            context_session_id="session-1",
            conversation_key=KEY,
        ),
        QueryRuntimeContext(
            admin_service=AdminRuntimeProviderAdapter(admin),
            session_context_reader=AdminRuntimeProviderAdapter(admin),
            metadata={"directory_path": "root"},
        ),
    )
    assert [m["message_id"] for m in provider.execute()["messages"]] == ["2"]
    messages = [
        TeamsMessageEnvelope(
            cursor=i,
            message_id=i,
            team_id="",
            channel_id="",
            created_at=t,
            author_id="user",
            content=i,
            metadata={"target": "chat:one", "chat_id": "one"},
        )
        for i, t in [("1", old), ("2", after), ("3", old)]
    ]
    # Run the shared helper used by the optional Teams tool with a synthetic policy.
    context = SimpleNamespace(
        config=admin.config, policy=SimpleNamespace(require_teams_message_source=lambda *args: None)
    )
    filtered, denied, _ = _filter_teams_messages_for_session(
        context,
        "session-1",
        object(),
        messages,
        {"channel_target": "chat:one"},
        lambda *args: None,
    )
    assert [m["message_id"] for m in filtered] == ["2"] and denied == 0
    assert len(inbox.list_all_messages()) == 3


def test_context_query_bindings_are_paired_schema_inputs():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="supplied together"):
        CommunicationMessagesQueryDefinition(
            id="history",
            display_name="History",
            type="query:communication:messages",
            communication_target_id="assistant-chat",
            context_session_id="session-1",
        )
    schema = SessionResetContextEffectDefinition.model_json_schema()
    assert "communication_target_id" not in schema["properties"]
    assert set(schema["required"]) >= {
        "target",
        "conversation_key",
        "request_id",
    }


def test_uncertain_admitted_launch_keeps_its_generation_across_a_later_reset(tmp_path):
    original = launch_context(
        tmp_path,
        "session-1",
        KEY,
        "uncertain",
        resume_thread_id="old-thread",
        resume_mode="same_context",
    )
    assert original["generation"] == 0
    store = ConversationContextStore(tmp_path)
    reset(store)
    recovered = launch_context(
        tmp_path,
        "session-1",
        KEY,
        "uncertain",
        resume_thread_id=None,
        resume_mode="new_context",
    )
    assert (recovered["generation"], recovered["thread_id"], recovered["resume_mode"]) == (
        0,
        "old-thread",
        "same_context",
    )
    assert not store.accept_thread("session-1", KEY, 0, "old-thread")
    assert store.get("session-1", KEY)["pending"]
    following = launch_context(tmp_path, "session-1", KEY, "following")
    assert following["generation"] == 1 and following["thread_id"] is None
    assert following["resume_mode"] == "new_context"


def test_confirmed_rejection_recaptures_history_and_cannot_release_a_newer_generation(tmp_path):
    store = ConversationContextStore(tmp_path)
    reset(store)
    first = launch_context(tmp_path, "session-1", KEY, "queued")
    assert reject_launch_context(tmp_path, "session-1", KEY, "queued", generation=1)
    rejected = ConversationContextStore(tmp_path).get("session-1", KEY)
    assert rejected["pending"] and rejected["launch_dispatch_id"] is None
    # No job started, so a late callback cannot consume this pending reset.
    assert not store.accept_thread("session-1", KEY, 1, "never-started")
    retry_started = datetime.now(UTC)
    rebound = launch_context(tmp_path, "session-1", KEY, "queued")
    assert rebound["generation"] == 1 and rebound["resume_mode"] == "new_context"
    assert rebound["thread_id"] is None
    assert rebound["history_after"] != first["history_after"]
    assert datetime.fromisoformat(rebound["history_after"]) >= retry_started
    assert reject_launch_context(tmp_path, "session-1", KEY, "queued", generation=1)
    reset(store, "another-reset")
    current = launch_context(tmp_path, "session-1", KEY, "queued")
    assert current["generation"] == 2
    assert not reject_launch_context(tmp_path, "session-1", KEY, "queued", generation=1)
    recovered = launch_context(tmp_path, "session-1", KEY, "queued")
    assert recovered["generation"] == 2 and recovered["history_after"] == current["history_after"]
    assert store.accept_thread("session-1", KEY, 2, "fresh-thread")
    assert launch_context(tmp_path, "session-1", KEY, "next")["thread_id"] == "fresh-thread"


@pytest.mark.parametrize("thread_id", [None, "old-browser-thread"])
def test_reset_accepts_an_opaque_conversation_without_communication_configuration(tmp_path, thread_id):
    admin = build_admin_runtime(tmp_path / "runtime")
    key = "browser:conversation-one"
    threads = {"other": "keep-thread"}
    if thread_id:
        threads[key] = thread_id
    admin.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="browser-assistant",
            allowed_tools=[],
            expires_at="2099-01-01T00:00:00Z",
            metadata={"agent_thread_ids": threads},
        )
    )
    other_session = SessionManifest(
        session_id="second-assistant",
        allowed_tools=[],
        expires_at="2099-01-01T00:00:00Z",
        metadata={"agent_thread_ids": {key: "second-assistant-thread"}},
    )
    admin.runtime.session_repository.upsert_session(other_session)
    provider = SessionResetContextEffectProvider()
    provider.initialize(
        SessionResetContextEffectDefinition(
            id="reset",
            display_name="Reset",
            type="effect:session:reset-context",
            target="browser-assistant",
            conversation_key=key,
            request_id="browser-message-one",
        ),
        EffectRuntimeContext(session_context_resetter=AdminRuntimeProviderAdapter(admin)),
    )
    assert provider.execute()["generation"] == 1
    assert provider.execute()["duplicate"]
    saved = admin.runtime.session_repository.get_session("browser-assistant")
    assert saved.metadata["agent_thread_ids"] == {key: None, "other": "keep-thread"}
    state = ConversationContextStore(admin.runtime_dir).get("browser-assistant", key)
    assert state["pending"] and state["generation"] == 1
    assert ConversationContextStore(admin.runtime_dir).get("second-assistant", key) is None
    assert admin.runtime.session_repository.get_session("second-assistant") == other_session
    assert launch_context(admin.runtime_dir, "browser-assistant", key, "next")["thread_id"] is None


def test_communication_context_binding_checks_session_target_permission(tmp_path):
    from evander.policy import PolicyError

    admin = runtime_with_assistant(tmp_path)
    admin.runtime.session_repository.patch_metadata("session-1", {"agent_thread_ids": {}})
    session = admin.runtime.session_repository.get_session("session-1")
    admin.runtime.session_repository.upsert_session(
        session.model_copy(update={"teams_chat_allowlist": []})
    )
    provider = CommunicationMessagesQueryProvider()
    provider.initialize(
        CommunicationMessagesQueryDefinition(
            id="binding",
            display_name="Binding",
            type="query:communication:messages",
            communication_target_id="assistant-chat",
            context_session_id="session-1",
            conversation_key=KEY,
            limit=1,
        ),
        QueryRuntimeContext(
            admin_service=AdminRuntimeProviderAdapter(admin),
            session_context_reader=AdminRuntimeProviderAdapter(admin),
            metadata={"directory_path": "root"},
        ),
    )
    with pytest.raises(PolicyError, match="allowlisted"):
        provider.execute()
    assert ConversationContextStore(admin.runtime_dir).get("session-1", KEY) is None


@pytest.mark.parametrize("change", ["key", "missing-session", "session-scope", "directory-scope"])
def test_communication_context_binding_rejects_wrong_identity_or_scope(tmp_path, change):
    admin = runtime_with_assistant(tmp_path)
    definition = dict(
        id="binding",
        display_name="Binding",
        type="query:communication:messages",
        communication_target_id="assistant-chat",
        context_session_id="session-1",
        conversation_key=KEY,
        limit=1,
    )
    scope = "root"
    if change == "key":
        definition["conversation_key"] = teams_conversation_key("chat:other")
    elif change == "missing-session":
        definition["context_session_id"] = "missing"
    elif change == "session-scope":
        session = admin.runtime.session_repository.get_session("session-1")
        admin.runtime.session_repository.upsert_session(
            session.model_copy(update={"directory_path": "root/other"})
        )
        definition["directory_path"] = "root/selected"
    else:
        scope = "root/selected"
        definition["directory_path"] = "root"
    provider = CommunicationMessagesQueryProvider()
    provider.initialize(
        CommunicationMessagesQueryDefinition(**definition),
        QueryRuntimeContext(
            admin_service=AdminRuntimeProviderAdapter(admin),
            metadata={"directory_path": scope},
        ),
    )
    with pytest.raises(ValueError):
        provider.execute()
    assert ConversationContextStore(admin.runtime_dir).get("session-1", KEY) is None


def test_existing_reset_receipts_replay_without_a_communication_target(tmp_path):
    import sqlite3

    path = tmp_path / "agent-message-deliveries.db"
    with sqlite3.connect(path) as db:
        # Prior schema: origin is required and has no default. Keep old receipts
        # and fresh thread mappings usable without requiring that origin again.
        db.execute("""CREATE TABLE conversation_contexts(
            session_id TEXT NOT NULL, conversation_key TEXT NOT NULL,
            generation INTEGER NOT NULL, pending INTEGER NOT NULL,
            thread_id TEXT, communication_target TEXT NOT NULL,
            history_after TEXT, launch_dispatch_id TEXT, requested_at TEXT NOT NULL,
            PRIMARY KEY(session_id,conversation_key))""")
        db.execute("""CREATE TABLE context_reset_receipts(
            session_id TEXT NOT NULL, conversation_key TEXT NOT NULL,
            request_id TEXT NOT NULL, communication_target TEXT NOT NULL,
            generation INTEGER NOT NULL,
            PRIMARY KEY(session_id,conversation_key,request_id))""")
        db.execute(
            "INSERT INTO conversation_contexts VALUES(?,?,1,0,?,?,NULL,?,?)",
            (
                "session-1",
                KEY,
                "fresh-thread",
                "chat:one",
                "original-launch",
                "2026-01-01T00:00:00Z",
            ),
        )
        db.execute(
            "INSERT INTO context_reset_receipts VALUES(?,?,?,?,1)",
            ("session-1", KEY, "original-reset", "chat:one"),
        )
    store = ConversationContextStore(tmp_path)
    replayed = store.request_reset(
        session_id="session-1", conversation_key=KEY, request_id="original-reset"
    )
    assert replayed["duplicate"] and replayed["generation"] == 1
    assert store.get("session-1", KEY)["thread_id"] == "fresh-thread"
    assert launch_context(tmp_path, "session-1", KEY, "later")["thread_id"] == "fresh-thread"
    newer = store.request_reset(
        session_id="session-1", conversation_key=KEY, request_id="another-reset"
    )
    assert newer["generation"] == 2 and newer["pending"]
    assert store.get("session-1", KEY)["thread_id"] is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM context_reset_receipts").fetchone()[0] == 2
        assert (
            db.execute(
                "SELECT communication_target FROM context_reset_receipts WHERE request_id='original-reset'"
            ).fetchone()[0]
            == "chat:one"
        )
