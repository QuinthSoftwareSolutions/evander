import json

import pytest
from pydantic import ValidationError

from evander_test_fixtures_addon.models import Control, Ensure, FixtureError, WorkerSettings

LEASE = "fx-" + "1" * 32
OTHER = "fx-" + "2" * 32


def ensure(scope, lease_id=LEASE, profile="sqlserver-rabbitmq"):
    return Ensure(lease_id=lease_id, scope=scope, profile=profile)


def test_combined_lease_is_idempotent_and_keeps_credentials_in_job(worker, scope):
    first = worker.ensure(ensure(scope))
    second = worker.ensure(ensure(scope))
    assert first["state"] == second["state"] == "ready"
    assert len(worker.sql.databases) == 2
    assert worker.docker.brokers == {LEASE}
    assert set(owner for owner, _ in worker.sql.databases) == {LEASE}
    job_id, config = worker.docker.bindings[LEASE]
    assert job_id == "job-a"
    assert config["source"]["username"] != scope.session_id
    assert config["source"]["password"] == config["target"]["password"]
    assert config["rabbitmq"]["password"]
    assert "password" not in json.dumps(second) and "secret-" not in json.dumps(second)
    assert first["config_path"].endswith("/" + LEASE + "/connection.json")


def test_separate_lease_principals_and_reset_do_not_touch_other_resources(worker, scope):
    worker.ensure(ensure(scope))
    other_scope = scope.model_copy(update={"task_id": "task-b"})
    worker.ensure(ensure(other_scope, OTHER))
    first_password = worker.docker.bindings[LEASE][1]["rabbitmq"]["password"]
    worker.reset(LEASE, "a" * 32)
    worker.ensure(ensure(scope))
    worker.reset(LEASE, "a" * 32)  # Lost reset response replay must not erase seeded data.
    assert worker.sql.dropped == [LEASE]
    assert worker.get(LEASE)["state"] == "ready"
    assert worker.docker.bindings[LEASE][1]["rabbitmq"]["password"] != first_password
    assert (OTHER, "source") in worker.sql.databases
    assert OTHER in worker.docker.brokers


def test_worker_quota_and_assignment_reject_before_creation(worker, scope):
    worker.ensure(ensure(scope))
    worker.ensure(ensure(scope.model_copy(update={"task_id": "b"}), OTHER))
    with pytest.raises(FixtureError, match="quota"):
        worker.ensure(ensure(scope.model_copy(update={"task_id": "c"}), "fx-" + "3" * 32))
    with pytest.raises(FixtureError, match="assignment"):
        worker.ensure(ensure(scope.model_copy(update={"worker_id": "foreign"}), "fx-" + "4" * 32))
    assert len(worker.docker.brokers) == 2


def test_failed_partial_sql_intent_can_be_retried_or_cleaned(worker, scope):
    worker.sql.fail = True
    assert worker.ensure(ensure(scope))["state"] == "failed"
    assert worker.get(LEASE)["state"] == "failed"
    assert "synthetic secret" not in json.dumps(worker.get(LEASE))
    worker.sql.fail = False
    worker.ensure(ensure(scope))
    assert worker.get(LEASE)["state"] == "ready"
    assert worker.release(LEASE)["state"] == "released"
    assert not worker.sql.databases and not worker.docker.brokers and not worker.docker.bindings


def test_worker_expiry_reaps_without_control_plane_and_get_never_renews(worker, scope):
    ready = worker.ensure(ensure(scope))
    worker.test_jobs.clear()
    worker.clock[0] += 60
    worker.tick()
    assert not worker.docker.bindings
    assert worker.get(LEASE)["config_path"] is None
    assert worker.get(LEASE)["expires_at"] == ready["expires_at"]
    worker.clock[0] = ready["expires_at"]
    worker.tick()
    assert worker.get(LEASE)["state"] == "expired"
    assert not worker.sql.databases and not worker.docker.brokers


def test_fault_deadline_is_durable_and_replay_does_not_extend_it(worker, scope):
    worker.ensure(ensure(scope))
    control = Control(action="pause", duration_seconds=20)
    result = worker.control(LEASE, control, "a" * 32)
    worker.clock[0] += 10
    replay = worker.control(LEASE, control, "a" * 32)
    assert result["fault_until"] == replay["fault_until"] == 1020
    assert worker.docker.calls.count("pause") == 1
    worker.clock[0] = 1020
    worker.tick()
    assert worker.get(LEASE)["fault_until"] is None
    assert worker.docker.calls.count("recover") == 2


def test_fault_crash_before_command_recovers_without_extending_deadline(worker, scope, monkeypatch):
    worker.ensure(ensure(scope))
    original = worker.docker.control
    monkeypatch.setattr(
        worker.docker, "control", lambda *_: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError):
        worker.control(LEASE, Control(action="stop", duration_seconds=10), "b" * 32)
    worker.clock[0] += 11
    monkeypatch.setattr(worker.docker, "control", original)
    worker.control(LEASE, Control(action="stop", duration_seconds=10), "b" * 32)
    assert "stop" not in worker.docker.calls
    worker.tick()
    assert worker.get(LEASE)["fault_until"] is None


def test_cleanup_failure_is_visible_retains_identity_and_retries_with_allocations_disabled(
    worker, scope
):
    worker.ensure(ensure(scope))
    worker.settings.allocations_enabled = False
    worker.docker.fail_cleanup = True
    result = worker.release(LEASE)
    assert result["state"] == "cleanup_failed"
    assert LEASE in worker.docker.brokers
    assert not worker.sql.databases
    worker.docker.fail_cleanup = False
    worker.tick()
    assert worker.get(LEASE)["state"] in {"released", "expired"}
    assert not worker.sql.databases and not worker.docker.brokers
    with pytest.raises(FixtureError, match="disabled"):
        worker.ensure(ensure(scope, OTHER))


def test_old_dispatch_job_cannot_renew_or_receive_replacement_binding(worker, scope):
    ready = worker.ensure(ensure(scope))
    worker.clock[0] += 100
    updated = scope.model_copy(update={"dispatch_id": "dispatch-b"})
    result = worker.ensure(ensure(updated))
    assert not worker.docker.bindings
    assert result["expires_at"] == ready["expires_at"]
    worker.test_jobs[0].metadata["dispatch_id"] = "dispatch-b"
    worker.test_jobs[0].job_id = "job-b"
    result = worker.ensure(ensure(updated))
    assert result["bound_job_id"] == "job-b"
    assert result["expires_at"] > ready["expires_at"]


@pytest.mark.parametrize("value", [-1, 0, 121, 10000])
def test_fault_duration_is_bounded(value):
    with pytest.raises(ValidationError):
        Control(action="stop", duration_seconds=value)


def test_image_requires_digest_and_client_cannot_supply_commands(scope):
    with pytest.raises(ValidationError):
        WorkerSettings(worker_id="a", rabbitmq_image="rabbitmq:latest")
    with pytest.raises(ValidationError):
        Ensure(**ensure(scope).model_dump(), command=["sh"])


def test_dispatch_expiry_withdraws_binding_but_retains_idle_resources(worker, scope):
    request = ensure(scope.model_copy(update={"dispatch_expires_at": 1010}))
    ready = worker.ensure(request)
    worker.clock[0] = 1010
    worker.tick()
    assert not worker.docker.bindings
    assert worker.get(LEASE)["state"] == "idle"
    assert worker.get(LEASE)["expires_at"] == ready["expires_at"]
    assert worker.sql.databases and worker.docker.brokers


def test_ready_renewal_does_not_reapply_sql_or_rotate_credentials(worker, scope, monkeypatch):
    worker.ensure(ensure(scope))
    original = worker.docker.bindings[LEASE][1]["source"]["password"]
    monkeypatch.setattr(
        worker.sql,
        "provision_database",
        lambda *_: (_ for _ in ()).throw(AssertionError("Unexpected SQL mutation")),
    )
    assert worker.ensure(ensure(scope))["state"] == "ready"
    assert worker.docker.bindings[LEASE][1]["source"]["password"] == original


def test_normal_broker_startup_remains_provisioning_without_failure_notice(
    worker, scope, monkeypatch
):
    from evander_test_fixtures_addon.models import NotReady

    original = worker.docker.ensure_broker
    monkeypatch.setattr(
        worker.docker, "ensure_broker", lambda *_: (_ for _ in ()).throw(NotReady("starting"))
    )
    result = worker.ensure(ensure(scope))
    assert result["state"] == "provisioning" and result["error"] is None
    assert len(worker.sql.databases) == 2
    monkeypatch.setattr(worker.docker, "ensure_broker", original)
    assert worker.ensure(ensure(scope))["state"] == "ready"


def test_worker_restart_recovers_resources_after_crash_before_projection(
    worker, scope, monkeypatch
):
    from evander_test_fixtures_addon.worker import WorkerService

    original = worker.docker.bind

    def crash(*args):
        original(*args)
        raise KeyboardInterrupt("simulated process termination")

    monkeypatch.setattr(worker.docker, "bind", crash)
    with pytest.raises(KeyboardInterrupt):
        worker.ensure(ensure(scope))
    durable = worker.store.get(LEASE)
    assert durable["bound_container_id"] == worker.test_jobs[0].container_id
    assert durable["secrets"]["source"]["password"]
    monkeypatch.setattr(worker.docker, "bind", original)
    restarted = WorkerService(
        worker.settings, docker=worker.docker, sql=worker.sql, jobs=worker.jobs, now=worker.now
    )
    assert restarted.ensure(ensure(scope))["state"] == "ready"
    assert len(worker.sql.databases) == 2 and worker.docker.brokers == {LEASE}
    assert restarted.release(LEASE)["state"] == "released"
    assert not worker.docker.bindings and not worker.sql.databases and not worker.docker.brokers


def test_active_fault_is_renewed_without_restarting_or_probing_stopped_broker(
    worker, scope, monkeypatch
):
    worker.ensure(ensure(scope))
    worker.control(LEASE, Control(action="stop", duration_seconds=60), "c" * 32)
    worker.clock[0] += 30

    def fail(*args):
        raise AssertionError("Intentional fault was probed or restarted")

    monkeypatch.setattr(worker.docker, "ensure_broker", fail)
    monkeypatch.setattr(worker.docker, "bind", fail)
    result = worker.ensure(ensure(scope))
    assert result["state"] == "ready" and result["fault_until"] == 1060
    assert worker.docker.calls == ["recover", "stop"]


def test_broker_startup_failure_is_bounded_and_can_later_recover(worker, scope, monkeypatch):
    from evander_test_fixtures_addon.models import NotReady

    original = worker.docker.ensure_broker
    monkeypatch.setattr(
        worker.docker, "ensure_broker", lambda *_: (_ for _ in ()).throw(NotReady("starting"))
    )
    assert worker.ensure(ensure(scope))["state"] == "provisioning"
    worker.clock[0] += 121
    result = worker.ensure(ensure(scope))
    assert result["state"] == "failed" and "readiness deadline" in result["error"]
    monkeypatch.setattr(worker.docker, "ensure_broker", original)
    assert worker.ensure(ensure(scope))["state"] == "ready"
