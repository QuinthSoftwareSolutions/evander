import json
import multiprocessing

import pytest

from evander_test_fixtures_addon.models import (
    Conflict,
    Control,
    Ensure,
    FixtureError,
    Scope,
    Settings,
    Worker,
)
from evander_test_fixtures_addon.service import Service
from evander_test_fixtures_addon.state import Store


class Authority:
    def __init__(self, scope):
        self.scope = scope
        self.owner = True
        self.active = True
        self.notified = []

    def assignment(self, session):
        return "root", "http://192.0.2.10:11888"

    def resolve(self, session, task, tool, settings):
        if (
            not self.active
            or not self.owner
            or session != self.scope.session_id
            or task != self.scope.task_id
        ):
            raise FixtureError("Scope denied")
        return self.scope

    def owns(self, scope):
        return self.owner

    def notify(self, lease):
        self.notified.append((lease["lease_id"], lease["state"]))


class Client:
    def __init__(self, worker):
        self.worker = worker

    def call(self, operation, lease_id=None, payload=None):
        if operation == "ensure":
            return self.worker.ensure(Ensure.model_validate(payload))
        if operation == "reset":
            return self.worker.reset(lease_id, payload["operation_id"])
        if operation == "control":
            return self.worker.control(
                lease_id, Control.model_validate(payload["control"]), payload["operation_id"]
            )
        return getattr(self.worker, operation)(lease_id)


@pytest.fixture
def service(tmp_path, scope, worker):
    authority = Authority(scope)
    service = Service(
        tmp_path,
        None,
        authority=authority,
        client_factory=lambda _: Client(worker),
        now=lambda: worker.clock[0],
    )
    settings = Settings(
        allocations_enabled=True,
        allowed_team_ids=["team-a"],
        workers={
            "worker-a": Worker(
                worker_base_url="http://192.0.2.10:11888",
                fixture_base_url="http://192.0.2.10:11991",
                token_env="FIXTURE_TOKEN",
            )
        },
    )
    service.store.save_settings("root", 0, settings, "test")
    return service


def test_end_to_end_queue_ready_notification_status_and_release(service, worker):
    lease = service.acquire("session-a", "task-a", "sqlserver-rabbitmq", "request-a")
    assert lease["state"] == "provisioning"
    result = service.reconcile(lease["lease_id"])
    assert result["state"] == "ready"
    worker.clock[0] += 30
    assert service.get("session-a", lease["lease_id"])["expires_at"] == result["expires_at"]
    assert service.reconcile(lease["lease_id"])["expires_at"] > result["expires_at"]
    assert service.authority.notified == [(lease["lease_id"], "ready")]
    assert "password" not in json.dumps(service.store.list())
    service.queue("session-a", lease["lease_id"], "release")
    assert service.reconcile(lease["lease_id"])["state"] == "released"
    assert not worker.sql.databases and not worker.docker.brokers


def test_ownership_loss_releases_and_never_wakes_former_owner(service, worker):
    lease = service.acquire("session-a", "task-a", "sqlserver", "a")
    service.reconcile(lease["lease_id"])
    service.authority.owner = False
    with pytest.raises(FixtureError):
        service.get("session-a", lease["lease_id"])
    assert service.reconcile(lease["lease_id"])["state"] == "released"
    assert not worker.sql.databases
    assert service.authority.notified == [(lease["lease_id"], "ready")]


def test_disabled_allocations_keep_existing_leases_reconciling_and_cleaning(service, worker):
    lease = service.acquire("session-a", "task-a", "sqlserver", "a")
    revision, settings = service.store.settings("root")
    settings.allocations_enabled = False
    service.store.save_settings("root", revision, settings, "test")
    with pytest.raises(FixtureError, match="disabled"):
        service.acquire("session-a", "task-a", "sqlserver", "b")
    assert service.reconcile(lease["lease_id"])["state"] == "ready"
    service.authority.active = False
    worker.test_jobs.clear()
    worker.clock[0] += 7201
    worker.tick()
    assert service.reconcile(lease["lease_id"])["state"] in {"expired", "released"}


def test_request_id_and_task_bundle_are_guarded(service):
    first = service.acquire("session-a", "task-a", "rabbitmq", "a")
    assert service.acquire("session-a", "task-a", "rabbitmq", "a")["lease_id"] == first["lease_id"]
    assert service.acquire("session-a", "task-a", "rabbitmq", "b")["lease_id"] == first["lease_id"]
    with pytest.raises(Conflict):
        service.acquire("session-a", "task-a", "sqlserver", "a")
    with pytest.raises(Conflict):
        service.acquire("session-a", "task-a", "sqlserver", "c")
    with pytest.raises(FixtureError):
        service.get("foreign-session", first["lease_id"])


def test_reset_and_fault_are_reconciled_without_touching_task_lifecycle(service, worker):
    lease = service.acquire("session-a", "task-a", "sqlserver-rabbitmq", "a")
    service.reconcile(lease["lease_id"])
    service.queue("session-a", lease["lease_id"], "reset")
    assert service.reconcile(lease["lease_id"])["state"] == "ready"
    assert worker.sql.dropped == [lease["lease_id"]]
    service.queue(
        "session-a", lease["lease_id"], "control", dict(action="stop", duration_seconds=30)
    )
    assert service.reconcile(lease["lease_id"])["fault_until"] == 1030


def test_operator_revision_and_directory_guards(service):
    lease = service.acquire("session-a", "task-a", "rabbitmq", "a")
    with pytest.raises(Conflict):
        service.operator_release(lease["lease_id"], 0, "operator:test", "root")
    with pytest.raises(FixtureError):
        service.operator_release(
            lease["lease_id"], lease["revision"], "operator:test", "root/foreign"
        )
    assert (
        service.operator_release(lease["lease_id"], lease["revision"], "operator:test", "root")[
            "state"
        ]
        == "releasing"
    )


def test_reset_reconciles_partial_failure_and_lost_response_without_repeating_completed_reset(
    service, worker, monkeypatch
):
    lease = service.acquire("session-a", "task-a", "sqlserver-rabbitmq", "reset-recovery")
    service.reconcile(lease["lease_id"])
    service.queue("session-a", lease["lease_id"], "reset")
    original = worker.sql.drop_session
    failure = [True]

    def fail_once(owner):
        original(owner)
        if failure[0]:
            failure[0] = False
            raise RuntimeError("simulated SQL response loss")

    monkeypatch.setattr(worker.sql, "drop_session", fail_once)
    assert service.reconcile(lease["lease_id"])["state"] == "failed"
    assert worker.get(lease["lease_id"])["state"] == "resetting"
    assert not worker.docker.bindings
    assert service.reconcile(lease["lease_id"])["state"] == "ready"
    assert len(worker.sql.databases) == 2
    assert service.store.get(lease["lease_id"])["operation"] is None


def test_control_plane_reconciles_worker_expiry_after_outage_then_allows_fresh_request(
    service, worker
):
    lease = service.acquire("session-a", "task-a", "sqlserver-rabbitmq", "outage")
    service.reconcile(lease["lease_id"])
    worker.clock[0] += 7201
    worker.tick()
    assert worker.get(lease["lease_id"])["state"] == "expired"
    assert service.reconcile(lease["lease_id"])["state"] == "expired"
    replacement = service.acquire("session-a", "task-a", "sqlserver-rabbitmq", "after-outage")
    assert replacement["lease_id"] != lease["lease_id"]
    assert service.reconcile(replacement["lease_id"])["state"] == "ready"


def test_new_dispatch_does_not_receive_stale_ready_path_before_rebinding(service, worker):
    lease = service.acquire("session-a", "task-a", "sqlserver-rabbitmq", "dispatch-change")
    service.reconcile(lease["lease_id"])
    service.authority.scope = service.authority.scope.model_copy(
        update={"dispatch_id": "dispatch-b"}
    )
    stale = service.get("session-a", lease["lease_id"])
    assert stale["state"] == "idle" and stale["config_path"] is None
    with pytest.raises(FixtureError, match="ready"):
        service.queue("session-a", lease["lease_id"], "reset")
    worker.test_jobs[0].metadata["dispatch_id"] = "dispatch-b"
    worker.test_jobs[0].job_id = "job-b"
    result = service.reconcile(lease["lease_id"])
    assert result["state"] == "ready" and result["bound_job_id"] == "job-b"


def test_control_returns_confirmed_worker_action_and_reset_exposes_pending_state(service, worker):
    lease = service.acquire("session-a", "task-a", "sqlserver-rabbitmq", "fault-ack")
    service.reconcile(lease["lease_id"])
    result = service.control("session-a", lease["lease_id"], "pause", 30)
    assert result["last_control"]["status"] == "applied"
    assert result["last_control"]["action"] == "pause" and result["fault_until"] == 1030
    assert result["operation"] is None
    queued = service.queue("session-a", lease["lease_id"], "reset")
    assert queued["state"] == "resetting" and queued["operation"]["kind"] == "reset"


def test_provisioning_finishes_and_wakes_task_after_requesting_agent_exits(
    service, worker, monkeypatch
):
    from evander_test_fixtures_addon.models import NotReady

    original = worker.docker.ensure_broker
    monkeypatch.setattr(
        worker.docker, "ensure_broker", lambda *_: (_ for _ in ()).throw(NotReady("starting"))
    )
    lease = service.acquire_ready("session-a", "task-a", "sqlserver-rabbitmq", "handoff")
    assert lease["state"] == "provisioning"
    expiry = lease["expires_at"]
    service.authority.active = False
    worker.test_jobs[0].status = "exited"
    worker.clock[0] += 30
    monkeypatch.setattr(worker.docker, "ensure_broker", original)
    result = service.reconcile(lease["lease_id"])
    assert result["state"] == "awaiting_job" and result["expires_at"] == expiry
    assert not worker.docker.bindings
    assert service.authority.notified[-1] == (lease["lease_id"], "awaiting_job")
    service.authority.active = True
    service.authority.scope = service.authority.scope.model_copy(
        update={"dispatch_id": "dispatch-b"}
    )
    worker.test_jobs[0].status = "running"
    worker.test_jobs[0].metadata["dispatch_id"] = "dispatch-b"
    worker.test_jobs[0].job_id = "job-b"
    rebound = service.acquire_ready("session-a", "task-a", "sqlserver-rabbitmq", "handoff-resume")
    assert rebound["state"] == "ready" and rebound["bound_job_id"] == "job-b"


def _concurrent_acquire(root, payload, output):
    store = Store(root)
    scope = Scope.model_validate(payload)
    with store.locked():
        lease = store.acquire(scope, "rabbitmq", "same-request", Settings(), now=1000)
    output.put(lease["lease_id"])


def test_independent_processes_admit_only_one_durable_bundle(tmp_path, scope):
    root = str(tmp_path / "registry")
    Store(root)
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    processes = [
        context.Process(target=_concurrent_acquire, args=(root, scope.model_dump(), output))
        for _ in range(4)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
        assert len({output.get(timeout=2) for _ in processes}) == 1
        assert len(Store(root).list()) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
