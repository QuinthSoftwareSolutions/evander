from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander.daemons.effect_orchestrator import EffectExecutionRecord, SqliteExecutionStore
from evander.model_usage.runtime import register_model_usage_correlation_context
from evander.models import SessionManifest
from evander.worker.codex_subscription import CodexSubscriptionTurnRunner
from evander_browser_sessions_addon.models import (
    BrowserError,
    CreateRequest,
    MessageRequest,
    Settings,
)
from evander_browser_sessions_addon.service import Bindings, Service


@pytest.fixture(params=["effect:agent:enqueue-prompt", "effect:agent:prompt"])
def conversations(tmp_path, request):
    now = datetime.now(UTC)
    owner = SessionManifest(
        session_id="owner",
        directory_path="root/demo",
        allowed_tools=["browser_session_create", "browser_session_reply"],
        expires_at=now + timedelta(hours=1),
        metadata={
            "agent_thread_ids": {
                "teams:origin:agent_thread_id": "thread-origin",
                "teams:other:agent_thread_id": "thread-other",
            }
        },
    )
    store_path = tmp_path / "orchestration.db"
    executions = SqliteExecutionStore(store_path)
    queued = []
    admin = SimpleNamespace(
        runtime=SimpleNamespace(
            policy=SimpleNamespace(
                get_session=lambda _: owner,
                require_tool=lambda *args: None,
            )
        ),
        workflow_run_store_paths=lambda: {"orchestration_store_path": store_path},
        enqueue_agent_prompt=lambda *args, **kwargs: (
            queued.append((args, kwargs)) or SimpleNamespace(queued=True)
        ),
    )
    service = Service(tmp_path, bindings=Bindings(tmp_path, admin))
    service.store.save_settings(
        "root", 0, Settings(enabled=True, public_base_url="https://control.test")
    )

    def record(dispatch, thread):
        outputs = {
            "session_id": "owner",
            "dispatch_id": dispatch,
            "execution_id": "exec-" + dispatch,
            "thread_id": thread or dispatch,
        }
        if thread:
            outputs.update(native_thread_id=thread, native_thread_execution_id="exec-" + dispatch)
        executions.put(
            EffectExecutionRecord(
                execution_id="effect-" + dispatch,
                idempotency_key="key-" + dispatch,
                event_id="event-" + dispatch,
                trace_id="trace-" + dispatch,
                source_node_id="prompt",
                source_trigger_type="message",
                effect_id="prompt",
                effect_type=request.param,
                status="waiting",
                started_at=now,
                finished_at=now,
                resolved_config_hash="fixture",
                outputs=outputs,
            )
        )

    def activate(dispatch, offset=0):
        register_model_usage_correlation_context(
            runtime_dir=tmp_path,
            session_id="owner",
            dispatch_id=dispatch,
            started_at=now + timedelta(seconds=offset),
            execution_backend="container_worker_vm",
        )

    def create():
        return service.create("owner", CreateRequest(mode="user_hosted", request_id="browser"))

    return SimpleNamespace(
        service=service, owner=owner, queued=queued, record=record, activate=activate, create=create
    )


def test_extension_feedback_resumes_origin_even_after_another_conversation_runs(conversations):
    f = conversations
    f.record("dispatch-origin", "thread-origin")
    f.activate("dispatch-origin")
    session = f.create()
    assert session["native_thread_id"] == "thread-origin"
    f.record("dispatch-other", "thread-other")
    f.activate("dispatch-other", 1)
    f.service.enqueue_message(
        session["id"], "guest:host", MessageRequest(request_id="question", body="Continue here")
    )
    f.service.dispatch_messages()
    assert len(f.queued) == 1
    args, kwargs = f.queued[0]
    assert args[0] == "owner"
    runner = object.__new__(CodexSubscriptionTurnRunner)
    runner.cwd = "/home/agent"
    runner.turn = SimpleNamespace(
        **kwargs,
        model="fixture",
        reasoning_effort=None,
        fast_mode=False,
        reasoning_effort_source=None,
        mcp_gateway_url=None,
        web_search_enabled=False,
    )
    calls = []

    class Process:
        def request(self, method, params):
            calls.append(method)
            if method == "thread/list":
                return {"data": [{"id": "thread-other"}]}
            return {"thread": {"id": params["threadId"]}}

    assert runner._open_thread(Process()) == "thread-origin"
    assert calls == ["thread/resume"]


def test_feedback_waits_for_creating_dispatch_receipt_and_persists_its_thread(conversations):
    f = conversations
    f.record("dispatch-origin", None)
    f.activate("dispatch-origin")
    session = f.create()
    assert session["native_thread_id"] is None
    f.record("dispatch-other", "thread-other")
    f.activate("dispatch-other", 1)
    f.service.enqueue_message(
        session["id"], "guest:host", MessageRequest(request_id="question", body="Continue here")
    )
    f.service.dispatch_messages()
    assert f.queued == []
    f.record("dispatch-origin", "thread-origin")
    f.service.dispatch_messages()
    assert len(f.queued) == 1
    assert f.queued[0][1]["resume_thread_id"] == "thread-origin"
    assert f.service.store.get(session["id"]).native_thread_id == "thread-origin"


def test_ambiguous_legacy_context_never_resumes_the_latest_conversation(conversations):
    f = conversations
    session = f.create()
    assert session["native_thread_id"] is None
    with pytest.raises(BrowserError, match="creating conversation"):
        f.service.enqueue_message(
            session["id"], "guest:host", MessageRequest(request_id="question", body="Continue here")
        )
    assert f.queued == []
    assert f.service.store.messages(session["id"]) == []


def test_single_channel_context_can_be_pinned_without_an_active_dispatch(conversations):
    f = conversations
    f.owner.metadata["agent_thread_ids"] = {"teams:origin:agent_thread_id": "thread-origin"}
    assert f.create()["native_thread_id"] == "thread-origin"
