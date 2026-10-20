"""Protect delivery, isolation and crash recovery at the real dispatch boundaries."""

import asyncio
import sys
import threading
from types import MethodType, SimpleNamespace

import httpx
import pytest

from evander.admin.services.conversation_delivery_service import ConversationDeliveryService
from evander.codex_auth import CodexAuthMode, CodexTurnSpec
from evander.config import GatewayConfig
from evander.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from evander.execution.container_worker_repository import ContainerWorkerExecutionRecord
from evander.messaging.agent_prompt_queue import AgentPromptQueueStore, QueuedAgentPromptRecord
from evander.worker.api import build_worker_app
from evander.worker.codex_app_server import CodexAppServerProcess, CodexAppServerTimeout
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.codex_subscription import CodexSubscriptionTurnRunner
from evander.worker.models import WorkerJobRecord, WorkerSteerRequest
from evander.worker.service import WorkerDaemon
from evander.worker.store import WorkerJobStore


def request(delivery_id="message-1", **kwargs):
    return WorkerSteerRequest(
        delivery_id=delivery_id,
        session_id="session-1",
        expected_thread_id="thread-1",
        expected_turn_id="turn-1",
        prompt="Move the button",
        **kwargs,
    )


class ActiveProcess:
    def __init__(self):
        self.calls = []
        self.running = True

    def request(self, method, params, *, response_observer):
        self.calls.append((method, params))
        response = {"result": {"turnId": "turn-1"}}
        response_observer(response)
        return response["result"]


def runner(tmp_path):
    r = CodexSubscriptionTurnRunner(
        command=["unused"],
        cwd="/home/agent",
        turn=CodexTurnSpec(
            auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
            model="fixture",
            prompt="original",
            resume_mode="same_context",
        ),
        broker=SimpleNamespace(external_tokens=lambda **kw: None),
        initial_tokens=CodexExternalAuthTokens("secret", "account", "plus"),
        log_path=tmp_path / "turn.log",
        finished_marker=tmp_path / "done",
    )
    r._process = ActiveProcess()
    r._thread_id, r._turn_id = "thread-1", "turn-1"
    return r


def worker(tmp_path):
    store = WorkerJobStore(tmp_path / "worker.db")
    store.put(
        WorkerJobRecord(
            job_id="job-1",
            session_id="session-1",
            image="fixture",
            command=["fixture"],
            container_name="fixture",
            status="running",
            agent_home_path="/home/agent",
            repo_root_path="/home/agent/repos",
            artifacts_path="/artifacts",
            logs_path="/logs",
        )
    )
    r = runner(tmp_path)
    daemon = SimpleNamespace(
        store=store,
        token="worker-secret",
        _codex_subscription_runners={"job-1": r},
        _codex_subscription_runners_lock=threading.Lock(),
    )
    daemon.steer_job = MethodType(WorkerDaemon.steer_job, daemon)
    return daemon, r


def test_native_delivery_keeps_job_and_turn_and_deduplicates(tmp_path):
    daemon, r = worker(tmp_path)
    job = daemon.store.get("job-1")
    for _ in range(3):
        assert daemon.steer_job(job, request())["status"] == "accepted"
    assert r._process.calls == [
        (
            "turn/steer",
            {
                "threadId": "thread-1",
                "expectedTurnId": "turn-1",
                "input": [{"type": "text", "text": "Move the button"}],
            },
        )
    ]
    assert daemon.store.get("job-1").status == "running"
    assert not r._completed.is_set() and not r._cancelled.is_set()
    with pytest.raises(ValueError, match="identity conflict"):
        daemon.steer_job(job, request().model_copy(update={"prompt": "different"}))
    with pytest.raises(ValueError, match="session"):
        daemon.steer_job(job, request().model_copy(update={"session_id": "other"}))
    assert len(r._process.calls) == 1


@pytest.mark.parametrize("change", [{"expected_thread_id": "other"}, {"expected_turn_id": "other"}])
def test_native_target_mismatch_never_sends(tmp_path, change):
    daemon, r = worker(tmp_path)
    assert (
        daemon.steer_job(daemon.store.get("job-1"), request().model_copy(update=change))["status"]
        == "rejected"
    )
    assert r._process.calls == []


def test_finished_turn_is_rejected_without_interrupt_or_send(tmp_path):
    daemon, r = worker(tmp_path)
    r._completed.set()
    assert daemon.steer_job(daemon.store.get("job-1"), request())["status"] == "rejected"
    assert r._process.calls == []


def test_uncertain_receipt_survives_restart_and_late_ack_wins(tmp_path):
    daemon, _ = worker(tmp_path)
    daemon.store.claim_steering("job-1", request())
    reloaded = WorkerJobStore(daemon.store.path)
    reloaded.recover_steering()
    assert reloaded.steering("job-1", "message-1")["status"] == "delivery_unknown"
    reloaded.finish_steering("job-1", "message-1", "accepted")
    reloaded.finish_steering("job-1", "message-1", "delivery_unknown")
    assert reloaded.steering("job-1", "message-1")["status"] == "accepted"
    assert "Move the button" not in reloaded.path.read_bytes().decode(errors="ignore")


def test_private_worker_api_authentication_and_status_readback(tmp_path):
    daemon, r = worker(tmp_path)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(build_worker_app(daemon)), base_url="http://worker"
        ) as client:
            assert (
                await client.post("/v1/jobs/job-1/steer", json=request().model_dump())
            ).status_code == 401
            client.headers["Authorization"] = "Bearer worker-secret"
            assert (
                await client.post("/v1/jobs/absent/steer", json=request().model_dump())
            ).status_code == 404
            assert (await client.post("/v1/jobs/job-1/steer", json=request().model_dump())).json()[
                "status"
            ] == "accepted"
            assert (await client.get("/v1/jobs/job-1/steering/message-1")).json()[
                "status"
            ] == "accepted"
            assert (await client.get("/v1/jobs/job-1/steering/absent")).status_code == 404
            bad = request().model_dump() | {"session_id": "other"}
            assert (await client.post("/v1/jobs/job-1/steer", json=bad)).status_code == 409
            assert (await client.get("/healthz")).json()["capabilities"][
                "codex_native_steering"
            ] == 1

    asyncio.run(run())
    assert len(r._process.calls) == 1


def test_real_jsonl_client_retains_late_acknowledgement(tmp_path):
    script = """import json,sys,time
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if m['method']=='turn/steer': time.sleep(.15)
 print(json.dumps({'id':m['id'],'result':{'turnId':'turn-1'}}),flush=True)
"""
    process = CodexAppServerProcess([sys.executable, "-u", "-c", script])
    received, responses = threading.Event(), []

    def observe(value):
        responses.append(value)
        received.set()

    process.start()
    try:
        with pytest.raises(CodexAppServerTimeout):
            process.request("turn/steer", {}, timeout_seconds=0.1, response_observer=observe)
        assert received.wait(2)
        assert responses[0]["result"]["turnId"] == "turn-1"
    finally:
        process.close()


class Client:
    def __init__(self):
        self.receipts, self.calls, self.capability = {}, [], 1
        self.lose_response = False

    def health(self):
        return {"capabilities": {"codex_native_steering": self.capability}}

    def steering_receipt(self, job, delivery):
        return self.receipts.get((job, delivery))

    def steer(self, job, payload):
        self.calls.append((job, payload))
        receipt = {"status": "accepted"}
        self.receipts[job, payload["delivery_id"]] = receipt
        if self.lose_response:
            raise TimeoutError()
        return receipt


def backend(tmp_path):
    b = ContainerWorkerVmExecutionBackend(
        config=GatewayConfig(
            runtime_dir=tmp_path,
            execution_backend="container_worker_vm",
            container_worker_base_url="http://worker",
        )
    )
    c = Client()
    b._client_for_mapping = lambda record: c
    b.inspect = lambda execution: SimpleNamespace(
        status=SimpleNamespace(value=b.repository.get(execution).last_status)
    )
    b.repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="session-1",
            worker_job_id="job-1",
            worker_url="http://worker",
            target_name="job-1",
            last_status="running",
            metadata={
                "codex_subscription_thread_id": "thread-1",
                "codex_subscription_turn_id": "turn-1",
                "conversation_key": "teams:chat-1",
                "dispatch_id": "original",
                "model": "fixture",
            },
        )
    )
    return b, c


def deliver(service, **kw):
    return service.try_steer(
        session_id="session-1",
        delivery_id="message-1",
        prompt="Move button",
        conversation_key="teams:chat-1",
        **kw,
    )


def test_control_plane_reconciles_lost_response_without_new_job_or_duplicate(tmp_path):
    b, c = backend(tmp_path)
    c.lose_response = True
    service = ConversationDeliveryService(tmp_path, b)
    assert deliver(service)["delivery_status"] == "delivery_unknown"
    b.repository.put(b.repository.get("exec-1").model_copy(update={"last_status": "exited"}))
    recovered = ConversationDeliveryService(tmp_path, b)
    result = deliver(recovered)
    assert (
        result["delivery_status"] == "steered" and result["steered_into_dispatch_id"] == "original"
    )
    assert len(c.calls) == 1
    assert deliver(recovered)["delivery_status"] == "steered"
    with pytest.raises(ValueError, match="identity conflict"):
        recovered.try_steer(
            session_id="session-1",
            delivery_id="message-1",
            prompt="Other",
            conversation_key="teams:chat-1",
        )


@pytest.mark.parametrize(
    "change", ["thread", "conversation", "model", "old-worker", "idle", "startup"]
)
def test_wrong_context_unsupported_and_idle_are_queued(tmp_path, change):
    b, c = backend(tmp_path)
    kwargs = {}
    record = b.repository.get("exec-1")
    if change == "thread":
        kwargs["thread_id"] = "other"
    if change == "conversation":
        record.metadata["conversation_key"] = "other"
    if change == "model":
        kwargs["model"] = "different"
    if change == "old-worker":
        c.capability = 0
    if change == "idle":
        record.last_status = "exited"
    if change == "startup":
        record.metadata.pop("codex_subscription_turn_id")
    b.repository.put(record)
    assert deliver(ConversationDeliveryService(tmp_path, b), **kwargs) is None
    assert c.calls == []


def test_pending_followups_preserve_fifo_and_cancellation(tmp_path):
    b, c = backend(tmp_path)
    queue = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    row = QueuedAgentPromptRecord(
        queue_key="earlier",
        target="session-1",
        vm_name="session-1",
        session_id="session-1",
        prompt="Earlier",
        dispatch_id="earlier",
        effect_id="effect",
        metadata={"delivery_mode": "steer_if_running", "conversation_key": "teams:chat-1"},
    )
    queue.put(row, count_retry=False)
    service = ConversationDeliveryService(tmp_path, b)
    assert deliver(service)["delivery_status"] == "queued"
    assert c.calls == []
    service.store.cancel("session-1", "message-1")
    assert deliver(service)["delivery_status"] == "cancelled"
    assert c.calls == []


def test_enqueue_provider_steers_without_launch_and_queues_unsupported_worker(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime, timedelta
    from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
    from evander.effects.providers.base import EffectRuntimeContext
    from evander.effects.providers.schemas import AgentEnqueuePromptEffectDefinition
    from evander.models import SessionManifest
    from evander.repositories.session_manifests import JsonSessionManifestRepository

    b, c = backend(tmp_path)
    config = b.config
    sessions = JsonSessionManifestRepository(config.session_store_path)
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            model="fixture",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"execution_backend": "container_worker_vm"},
        )
    )
    resolver = SimpleNamespace(
        resolve_for_directory=lambda _: config, resolve_for_session_id=lambda _: config
    )
    context = EffectRuntimeContext(resolved_config=config, provider_config_resolver=resolver)
    monkeypatch.setattr(
        "evander.effects.providers.agent_enqueue_prompt.container_backend", lambda *args: b
    )
    provider = AgentEnqueuePromptEffectProvider()
    effect = AgentEnqueuePromptEffectDefinition(
        id="enqueue",
        display_name="Chat",
        type="effect:agent:enqueue-prompt",
        session_id="session-1",
        prompt="Move button",
        delivery_mode="steer_if_running",
        dispatch_id="message-1",
        conversation_key="teams:chat-1",
        resume_mode="same_context",
    )
    provider.initialize(effect, context)
    result = provider.execute()
    assert result["delivery_status"] == "steered" and not result["queued"]
    assert result["steered_into_dispatch_id"] == "original"
    assert not provider.on_execution_started(record=object(), outputs=result)
    assert AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").read_all() == {}
    assert len(c.calls) == 1
    assert provider.execute()["delivery_status"] == "steered"
    assert len(c.calls) == 1
    c.capability = 0
    for delivery in ("second", "third"):
        provider.initialize(effect.model_copy(update={"dispatch_id": delivery}), context)
        for _ in range(5):
            assert provider.execute()["queued"] is True
    rows = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").read_all()
    assert {row.dispatch_id for row in rows.values()} == {"second", "third"}
    assert all(row.retry_count == 0 for row in rows.values())


@pytest.mark.parametrize("settings", [{"reasoning_effort": "max"}, {"fast_mode": True}])
def test_changed_turn_settings_wait_for_a_new_turn(tmp_path, settings):
    b, c = backend(tmp_path)
    assert deliver(ConversationDeliveryService(tmp_path, b), turn_settings=settings) is None
    assert c.calls == []


def test_queued_steering_reconciles_ack_without_completing_original_execution(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime
    from evander.daemons.effect_orchestrator import EffectExecutionRecord, SqliteExecutionStore
    from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook

    b, c = backend(tmp_path)
    queue = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    row = QueuedAgentPromptRecord(
        queue_key="message:one",
        target="session-1",
        vm_name="session-1",
        session_id="session-1",
        prompt="Move button",
        dispatch_id="message-1",
        effect_id="enqueue",
        metadata={"delivery_mode": "steer_if_running", "conversation_key": "teams:chat-1"},
    )
    queue.put(row, count_retry=False)
    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    now = datetime.now(UTC)
    original = EffectExecutionRecord(
        execution_id="effect-original",
        idempotency_key="original",
        event_id="original",
        trace_id="original",
        source_node_id="enqueue",
        source_trigger_type="message",
        effect_id="enqueue",
        effect_type="effect:agent:enqueue-prompt",
        status="waiting",
        started_at=now,
        finished_at=now,
        resolved_config_hash="fixture",
        outputs={"dispatch_id": "original", "execution_id": "exec-1"},
    )
    store.record_execution(original)
    publications = []
    worker = SimpleNamespace(
        execution_store=store,
        _publish_effect_completion_event_or_mark_pending=lambda **kw: publications.append(kw),
    )
    hook = AgentEnqueuePromptQueueDispatchHook()
    monkeypatch.setattr(
        hook,
        "_container_record_session_context",
        lambda **kw: (
            "session-1",
            SimpleNamespace(expires_at=now.replace(year=now.year + 1)),
            b.config,
        ),
    )
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend", lambda *args, **kw: b
    )
    c.lose_response = True
    assert (
        hook._dispatch_container_record_if_idle(
            worker=worker, queue_store=queue, queue_key=row.queue_key, record=row
        )
        is False
    )
    assert queue.get(row.queue_key) is not None
    assert publications == []
    assert (
        hook._dispatch_container_record_if_idle(
            worker=worker, queue_store=queue, queue_key=row.queue_key, record=row
        )
        is True
    )
    assert queue.get(row.queue_key) is None
    assert publications[0]["branch_port"] == "steered"
    assert store.find_execution_by_idempotency_key("original").status == "waiting"
    assert len(c.calls) == 1


def test_simultaneous_worker_retries_send_only_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    daemon, r = worker(tmp_path)
    job = daemon.store.get("job-1")
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: daemon.steer_job(job, request()), range(8)))
    assert all(receipt["status"] in {"accepted", "sending"} for receipt in receipts)
    assert daemon.store.steering("job-1", "message-1")["status"] == "accepted"
    assert len(r._process.calls) == 1


def test_uncertain_delivery_does_not_follow_a_reassigned_worker(tmp_path):
    b, c = backend(tmp_path)
    c.lose_response = True
    service = ConversationDeliveryService(tmp_path, b)
    assert deliver(service)["delivery_status"] == "delivery_unknown"
    record = b.repository.get("exec-1")
    record.worker_url = "http://other-worker"
    b.repository.put(record)
    assert deliver(service)["delivery_status"] == "delivery_unknown"
    assert len(c.calls) == 1


def test_chat_subscription_deferral_reuses_admitted_message_slot(tmp_path):
    from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
    from evander.effects.providers.base import EffectRuntimeContext

    provider = AgentEnqueuePromptEffectProvider()
    provider.runtime_context = EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path)
    )
    provider._delivery_metadata = {
        "delivery_mode": "steer_if_running",
        "conversation_key": "teams:chat-1",
    }
    common = dict(
        target="session-1",
        vm_name="session-1",
        session_id="session-1",
        prompt="Continue",
        model="fixture",
        vm_user="agent",
        dispatch_id="one",
        resume_thread_id=None,
        effect_id="enqueue",
    )
    first = provider._queue_prompt(**common, queue_key="message:session-1:one")
    from datetime import UTC, datetime, timedelta

    reset = datetime.now(UTC) + timedelta(minutes=5)
    deferred = provider._queue_prompt(
        **common,
        queue_key="message:session-1:one:subscription:one",
        capacity_deferral=True,
        queue_reason="subscription_rate_limited",
        subscription_profile_id="default",
        retry_not_before=reset,
    )
    rows = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").read_all()
    assert len(rows) == 1 and deferred.queue_key == first.queue_key
    assert (
        deferred.retry_not_before == reset and deferred.queue_reason == "subscription_rate_limited"
    )
    assert deferred.retry_count == 0


def test_explicit_new_context_does_not_steer_running_turn(tmp_path):
    b, c = backend(tmp_path)
    assert deliver(ConversationDeliveryService(tmp_path, b), allow_native=False) is None
    assert c.calls == []


def test_unsupported_vm_browser_owner_can_cancel_queued_feedback(tmp_path):
    from evander.admin.services.vm_agent.agent_prompt import VmAgentPromptMixin

    session = SimpleNamespace(session_id="vm-session")
    admin = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        runtime=SimpleNamespace(session_repository=SimpleNamespace(get_session=lambda _: session)),
    )
    queue = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue.put(
        QueuedAgentPromptRecord(
            queue_key="browser:one",
            target="vm-session",
            vm_name="vm-one",
            session_id="vm-session",
            prompt="Update page",
            dispatch_id="browser:one",
            effect_id="enqueue",
        ),
        count_retry=False,
    )
    assert (
        VmAgentPromptMixin.agent_message_delivery(admin, "vm-session", "browser:one")[
            "delivery_status"
        ]
        == "queued"
    )
    VmAgentPromptMixin.cancel_agent_message(admin, "vm-session", "browser:one")
    assert queue.read_all() == {}
    assert (
        VmAgentPromptMixin.agent_message_delivery(admin, "vm-session", "browser:one")[
            "delivery_status"
        ]
        == "cancelled"
    )


def test_admin_vm_dispatch_keeps_queue_fallback_without_container_configuration(
    tmp_path, monkeypatch
):
    from evander.admin.composition import build_admin_runtime
    from evander.models import SessionManifest

    service = build_admin_runtime(tmp_path / "runtime")
    session = SessionManifest(
        session_id="vm-session", vm_id="vm-one", allowed_tools=[], expires_at="2099-01-01T00:00:00Z"
    )
    service.runtime.session_repository.upsert_session(session)
    admin = service.vm_agent_admin
    monkeypatch.setattr(
        admin, "_reconcile_managed_git_remote_before_dispatch", lambda **kw: session
    )

    def container_forbidden(**kwargs):
        raise AssertionError("A VM follow-up must not require a configured container worker")

    monkeypatch.setattr(admin, "container_worker_backend", container_forbidden)
    provider = SimpleNamespace(execute=lambda **kw: {"success": True, "queued": True})
    monkeypatch.setattr(
        "evander.admin.services.vm_agent.agent_prompt.EffectProviderFactory",
        lambda **kw: SimpleNamespace(build=lambda *args, **kwargs: provider),
    )
    result = admin.prompt_admin_agent(
        "vm-session",
        backend="libvirt_vm",
        payload={
            "prompt": "Continue",
            "delivery_mode": "steer_if_running",
            "dispatch_id": "one",
            "conversation_key": "teams:chat",
        },
    )
    assert result["success"] and result["queued"]
