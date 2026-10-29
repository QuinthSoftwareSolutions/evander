from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest

from evander.admin.services.repository_index_service import RepositoryIndexService
from evander.code_index.models import IndexSettings
from evander.code_index.worker import IndexWorker
from evander.execution.container_worker_client import ContainerWorkerClientError
from test_repository_index import ManagedGit, OTHER_SHA, REPO, SHA


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    sessions, jobs, released, stopped, cleanup_attempts = {}, {}, [], [], []
    state = SimpleNamespace(fail_setup=None, cleanup_success=False)
    managed = ManagedGit()
    managed.release_retained_snapshot = lambda *args, **kwargs: released.append(kwargs)
    managed.unbind_all = lambda *args: None

    def get(job_id):
        if job_id not in jobs:
            raise ContainerWorkerClientError("Missing", status_code=404)
        return SimpleNamespace(job_id=job_id, status=jobs[job_id])

    def create(request):
        jobs[request.job_id] = "running"
        return get(request.job_id)

    def stop(job_id):
        get(job_id)
        stopped.append(job_id)
        jobs[job_id] = "stopped"

    def write(*args):
        if state.fail_setup == "write":
            raise TimeoutError("Bootstrap write failed")

    def cleanup(session_id):
        cleanup_attempts.append(session_id)
        if state.cleanup_success:
            sessions[session_id].metadata["container_lifecycle"] = {
                "cleanup_status": "cleanup_succeeded"
            }
        return SimpleNamespace(success=state.cleanup_success)

    client = SimpleNamespace(get=get, create=create, stop=stop, write_files=write)
    components = SimpleNamespace(
        runtime=SimpleNamespace(
            provider_config=None,
            session_repository=SimpleNamespace(get_session=sessions.get),
        ),
        managed_git_admin=managed,
        vm_agent_admin=SimpleNamespace(
            cleanup_container_session=cleanup,
            list_managed_git_access=lambda _: [],
        ),
    )
    worker = IndexWorker(components)
    monkeypatch.setattr(worker, "client", lambda _: client)

    class Factory:
        def __init__(self, **kwargs):
            pass

        def build(self, definition, **kwargs):
            def execute():
                if definition.type == "effect:agent:create":
                    if state.fail_setup == "create":
                        raise ValueError("Worker configuration is missing")
                    sessions[definition.session_id] = SimpleNamespace(metadata={})
                elif state.fail_setup == "clone":
                    raise TimeoutError("Clone failed")
                return {
                    "target_dir": "/home/agent/repos/App",
                    "container_image": "evander-code-index:1",
                }

            return SimpleNamespace(execute=execute)

    monkeypatch.setattr("evander.code_index.worker.EffectProviderFactory", Factory)
    service = RepositoryIndexService(
        tmp_path, managed, worker=worker, protected_snapshots=lambda: set()
    )
    service.save_settings(
        REPO,
        "root/app",
        IndexSettings(enabled=True, semantic=False, refresh_on_git_change=False).model_dump(),
        expected_revision=0,
    )
    return SimpleNamespace(
        service=service,
        worker=worker,
        sessions=sessions,
        jobs=jobs,
        client=client,
        released=released,
        stopped=stopped,
        cleanup_attempts=cleanup_attempts,
        state=state,
        components=components,
    )


def expire(service, build):
    with service.store.connect(write=True) as db:
        db.execute(
            "UPDATE snapshots SET created=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(days=40)).isoformat(), build["id"]),
        )


@pytest.mark.parametrize("phase", ["create", "clone", "write"])
def test_setup_failure_does_not_strand_compiler_capacity(lifecycle, phase):
    service = lifecycle.service
    first = service.ensure(REPO, "root/app", SHA)
    next_build = service.ensure(REPO, "root/app", OTHER_SHA)
    lifecycle.state.fail_setup = phase
    for _ in range(3):
        service.reconcile("root/app")
        assert not service.store.compiler_running(except_build=next_build["id"])
    assert service.store.get(first["id"])["status"] == "failed"
    assert not lifecycle.jobs
    lifecycle.state.fail_setup = None
    service.reconcile("root/app")
    assert service.store.get(next_build["id"])["status"] == "running"
    assert list(lifecycle.jobs) == [next_build["id"]]


def test_legacy_reservation_without_session_can_be_released(lifecycle):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    service.store.update(
        build["id"], status="failed", metrics={"compiler_submission_pending": True}
    )
    service.cancel(REPO, "root/app", build["id"])
    assert service.store.get(build["id"])["status"] == "cancelled"
    assert not service.store.compiler_running(except_build="another-build")


@pytest.mark.parametrize("stop_fails", [False, True])
def test_cancellation_serializes_with_submission_and_lost_response(lifecycle, stop_fails):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    next_build = service.ensure(REPO, "root/app", OTHER_SHA)
    entered, proceed, cancelling = Event(), Event(), Event()
    original_create = lifecycle.client.create
    original_stop = lifecycle.client.stop

    def pause_before_submit(*args):
        entered.set()
        assert proceed.wait(5)

    def lose_response(request):
        assert not lifecycle.stopped
        assert service.store.get(build["id"])["status"] == "queued"
        assert service.store.compiler_running(except_build=next_build["id"])
        original_create(request)
        raise TimeoutError("Worker accepted the job but its response was lost")

    def stop(job_id):
        if stop_fails:
            raise TimeoutError("Cannot confirm shutdown")
        return original_stop(job_id)

    lifecycle.client.write_files = pause_before_submit
    lifecycle.client.create = lose_response
    lifecycle.client.stop = stop
    # A different service instance models another request/process using the same files.
    operator = RepositoryIndexService(
        service.store.root.parent, service.managed_git, worker=lifecycle.worker
    )
    original_lock = operator._lifecycle_lock

    @contextmanager
    def observed_lock():
        cancelling.set()
        with original_lock() as acquired:
            yield acquired

    operator._lifecycle_lock = observed_lock
    with ThreadPoolExecutor(max_workers=2) as pool:
        reconciliation = pool.submit(service.reconcile, "root/app")
        try:
            assert entered.wait(5)
            cancellation = pool.submit(operator.cancel, REPO, "root/app", build["id"])
            assert cancelling.wait(5)
            with pytest.raises(TimeoutError):
                cancellation.result(timeout=0.1)
        finally:
            proceed.set()
        reconciliation.result(timeout=5)
        if stop_fails:
            with pytest.raises(TimeoutError, match="confirm shutdown"):
                cancellation.result(timeout=5)
        else:
            assert cancellation.result(timeout=5)["status"] == "cancelled"

    if stop_fails:
        current = service.store.get(build["id"])
        assert current["status"] == "queued"
        assert current["metrics"]["compiler_submission_pending"]
        assert lifecycle.jobs[build["id"]] == "running"
        lifecycle.client.stop = original_stop
        operator.cancel(REPO, "root/app", build["id"])
    assert lifecycle.jobs[build["id"]] == "stopped"
    assert not service.store.compiler_running(except_build=next_build["id"])
    lifecycle.client.create = original_create
    service.reconcile("root/app")
    assert service.store.get(next_build["id"])["status"] == "running"


@pytest.mark.parametrize("status", ["importing", "ready", "partial", "failed", "cancelled"])
def test_imported_or_terminal_build_retires_session_without_pruning_snapshot(lifecycle, status):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    service.reconcile("root/app")
    service.store.update(build["id"], status=status)
    lifecycle.state.cleanup_success = True

    assert service.maintenance("root/app")["deleted"] == []

    current = service.store.get(build["id"])
    assert current["status"] == status
    assert current["metrics"]["worker_session_cleanup_attempts"] == 1
    assert current["metrics"]["worker_session_cleanup_completed_at"]
    assert lifecycle.cleanup_attempts == [build["id"]]
    assert lifecycle.sessions[build["id"]].metadata["container_lifecycle"] == {
        "cleanup_status": "cleanup_succeeded"
    }
    assert lifecycle.released == []

    assert service.maintenance("root/app")["deleted"] == []
    assert lifecycle.cleanup_attempts == [build["id"]]


def test_terminal_worker_cleanup_retries_before_marking_completion(lifecycle):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    service.reconcile("root/app")
    service.store.update(build["id"], status="ready")

    assert service.maintenance("root/app")["deleted"] == []
    first = service.store.get(build["id"])
    assert first["metrics"]["worker_session_cleanup_attempts"] == 1
    assert first["metrics"]["worker_session_cleanup_last_error"] == "RuntimeError"
    assert "worker_session_cleanup_completed_at" not in first["metrics"]
    assert lifecycle.released == []

    lifecycle.state.cleanup_success = True
    assert service.maintenance("root/app")["deleted"] == []
    completed = service.store.get(build["id"])
    assert completed["metrics"]["worker_session_cleanup_attempts"] == 2
    assert completed["metrics"]["worker_session_cleanup_completed_at"]
    assert "worker_session_cleanup_last_error" not in completed["metrics"]
    assert lifecycle.cleanup_attempts == [build["id"], build["id"]]
    assert lifecycle.released == []


def test_terminal_worker_cleanup_waits_for_uncertain_compiler_shutdown(lifecycle):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    service.reconcile("root/app")
    service.store.update(
        build["id"],
        status="failed",
        metrics={
            **service.store.get(build["id"])["metrics"],
            "compiler_submission_pending": True,
        },
    )
    original_stop = lifecycle.client.stop

    def fail_stop(_):
        raise TimeoutError("Cannot confirm shutdown")

    lifecycle.client.stop = fail_stop

    assert service.maintenance("root/app")["deleted"] == []
    uncertain = service.store.get(build["id"])
    assert uncertain["metrics"]["compiler_submission_pending"]
    assert uncertain["metrics"]["worker_session_cleanup_last_error"] == "TimeoutError"
    assert lifecycle.cleanup_attempts == []

    lifecycle.client.stop = original_stop
    lifecycle.state.cleanup_success = True
    assert service.maintenance("root/app")["deleted"] == []
    completed = service.store.get(build["id"])
    assert not completed["metrics"]["compiler_submission_pending"]
    assert completed["metrics"]["worker_session_cleanup_completed_at"]
    assert lifecycle.stopped == [build["id"]]
    assert lifecycle.cleanup_attempts == [build["id"]]


def test_retention_reclaims_cancelled_build_that_never_had_a_session(lifecycle):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    service.cancel(REPO, "root/app", build["id"])
    expire(service, build)
    assert not lifecycle.sessions
    assert service.maintenance("root/app")["deleted"] == [build["id"]]
    assert lifecycle.released[0]["binding_id"] == build["id"]
    assert lifecycle.released[0]["expected_commit_sha"] == SHA
    assert not service.store.list(REPO)


def test_retention_retries_unsuccessful_worker_cleanup_before_releasing_pin(lifecycle):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    service.reconcile("root/app")
    service.cancel(REPO, "root/app", build["id"])
    expire(service, build)
    for _ in range(2):
        assert service.maintenance("root/app")["deleted"] == []
        assert service.store.get(build["id"])
        assert not lifecycle.released
    lifecycle.state.cleanup_success = True
    assert service.maintenance("root/app")["deleted"] == [build["id"]]
    assert len(lifecycle.released) == 1


def test_retention_can_retry_pin_release_after_worker_already_cleaned(lifecycle):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    service.reconcile("root/app")
    service.cancel(REPO, "root/app", build["id"])
    expire(service, build)
    lifecycle.state.cleanup_success = True
    original_release = service.managed_git.release_retained_snapshot

    def unavailable(*args, **kwargs):
        raise OSError("Git storage unavailable")

    service.managed_git.release_retained_snapshot = unavailable
    assert service.maintenance("root/app")["deleted"] == []
    lifecycle.components.vm_agent_admin.cleanup_container_session = lambda _: pytest.fail(
        "Already cleaned"
    )
    service.managed_git.release_retained_snapshot = original_release
    assert service.maintenance("root/app")["deleted"] == [build["id"]]


def test_missing_session_after_submission_is_not_evidence_of_shutdown(lifecycle):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    service.store.update(
        build["id"],
        metrics={
            "compiler_submission_pending": True,
            "compiler_submission_attempted": True,
        },
    )
    with pytest.raises(RuntimeError, match="missing after compiler submission"):
        service.cancel(REPO, "root/app", build["id"])
    with pytest.raises(RuntimeError, match="missing after compiler submission"):
        lifecycle.worker.cleanup(service.store.get(build["id"]))
    assert not lifecycle.released
    assert service.store.compiler_running(except_build="another-build")


def test_partial_provisioning_without_manifest_requires_remote_cleanup(lifecycle):
    service = lifecycle.service
    build = service.ensure(REPO, "root/app", SHA)
    lifecycle.state.fail_setup = "create"
    service.reconcile("root/app")
    service.cancel(REPO, "root/app", build["id"])
    expire(service, build)
    attempts = []

    def delete_session(session_id):
        attempts.append(session_id)
        if not lifecycle.state.cleanup_success:
            raise ContainerWorkerClientError("Deletion failed", status_code=503)

    def backend(**kwargs):
        assert kwargs["directory_path"] == "root/app"
        assert kwargs["session_id"] == service.store.get(build["id"])["session_id"]
        return SimpleNamespace(delete_session=delete_session)

    lifecycle.components.vm_agent_admin.container_worker_backend = backend
    assert service.maintenance("root/app")["deleted"] == []
    assert len(attempts) == 1
    assert not lifecycle.released
    lifecycle.state.cleanup_success = True
    assert service.maintenance("root/app")["deleted"] == [build["id"]]
    assert len(attempts) == 2
    assert len(lifecycle.released) == 1


@pytest.mark.parametrize("failed_step", [None, "revoke", "authorized_sessions", "sessions", "pin"])
def test_retirement_revokes_ssh_authorization_and_git_bindings_before_deletion(
    tmp_path, failed_step
):
    from evander.admin.services.managed_git_service import ManagedGitConflictError
    from evander.admin.services.vm_agent.git_access import VmAgentGitAccessMixin
    from evander.models import SessionManifest
    from evander.repositories.session_manifests import JsonSessionManifestRepository
    from test_managed_git_service import _service, _source_repository

    source, _, sha = _source_repository(tmp_path)
    managed = _service(tmp_path)
    repo_id = managed.register_repository("root/app", str(source))["managed_repo_id"]
    service = RepositoryIndexService(
        managed.runtime_dir, managed, protected_snapshots=lambda: set()
    )
    service.save_settings(
        repo_id,
        "root/app",
        IndexSettings(enabled=True, semantic=False, refresh_on_git_change=False).model_dump(),
        expected_revision=0,
    )
    build = service.store.get(service.ensure(repo_id, "root/app", sha)["id"])
    session_id = build["session_id"]
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_text(
        "ssh-ed25519 AAAAIndexSession index\nssh-ed25519 AAAAOtherSession other\n"
    )
    sessions = JsonSessionManifestRepository(tmp_path / "sessions.json")
    sessions.upsert_session(
        SessionManifest(
            session_id=session_id,
            directory_path="root/app",
            vm_provider="container_worker_vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "managed_git_access_grants": {
                    repo_id: {
                        "managed_repo_id": repo_id,
                        "access_mode": "read-only",
                        "lifetime": "session",
                        "execution_backend": "container_worker_vm",
                        "public_key_body": "AAAAIndexSession",
                        "authorized_keys_path": str(authorized_keys),
                        "key_name": "index-key",
                    }
                }
            },
        )
    )
    managed.bind(repo_id, "sessions", session_id)
    managed.bind(repo_id, "authorized_sessions", session_id)
    service.store.update(build["id"], status="failed", worker_job_id=build["id"])
    expire(service, build)

    # Exercise the real Git-access service and persisted manifests/registry, while
    # replacing only remote workspace deletion with its successful tombstone result.
    git_access = VmAgentGitAccessMixin()
    git_access._deps = SimpleNamespace(
        runtime=SimpleNamespace(session_repository=sessions),
        managed_git_admin=managed,
    )
    deleted_workspaces = []

    def cleanup(target):
        deleted_workspaces.append(target)
        session = sessions.get_session(target)
        sessions.upsert_session(
            session.model_copy(
                update={
                    "expires_at": datetime.now(UTC) - timedelta(seconds=1),
                    "metadata": {
                        **session.metadata,
                        "container_lifecycle": {"cleanup_status": "cleanup_succeeded"},
                    },
                }
            )
        )
        return SimpleNamespace(success=True)

    admin = SimpleNamespace(
        cleanup_container_session=cleanup,
        list_managed_git_access=git_access.list_managed_git_access,
        revoke_managed_git_access=git_access.revoke_managed_git_access,
    )
    service.worker = IndexWorker(
        SimpleNamespace(
            runtime=SimpleNamespace(session_repository=sessions),
            vm_agent_admin=admin,
            managed_git_admin=managed,
        )
    )
    original_revoke = admin.revoke_managed_git_access
    original_unbind = managed.unbind_all
    original_release = managed.release_retained_snapshot

    def unavailable(*args, **kwargs):
        raise OSError("Retirement dependency unavailable")

    def unbind(kind, target):
        if kind == failed_step:
            unavailable()
        return original_unbind(kind, target)

    if failed_step == "revoke":
        admin.revoke_managed_git_access = unavailable
    elif failed_step == "pin":
        managed.release_retained_snapshot = unavailable
    elif failed_step:
        managed.unbind_all = unbind
    with pytest.raises(ManagedGitConflictError, match="active bindings"):
        managed.delete_repository(repo_id)

    if failed_step:
        assert service.maintenance("root/app")["deleted"] == []
        assert service.store.get(build["id"])
        assert any(
            binding["kind"] == "code_indexes"
            for binding in managed.get_repository(repo_id)["bindings"]
        )
        assert deleted_workspaces == [session_id]
        if failed_step == "revoke":
            assert "AAAAIndexSession" in authorized_keys.read_text()
        admin.revoke_managed_git_access = original_revoke
        managed.unbind_all = original_unbind
        managed.release_retained_snapshot = original_release

    assert service.maintenance("root/app")["deleted"] == [build["id"]]
    assert deleted_workspaces == [session_id]
    assert authorized_keys.read_text() == "ssh-ed25519 AAAAOtherSession other\n"
    assert not git_access.list_managed_git_access(session_id)
    assert managed.get_repository(repo_id)["bindings"] == []
    assert not service.store.list(repo_id)
    assert managed.delete_repository(repo_id)["deleted"]
