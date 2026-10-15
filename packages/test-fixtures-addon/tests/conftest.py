import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.worker.models import WorkerMssqlDatabaseRecord
from evander_test_fixtures_addon.models import Scope, WorkerSettings
from evander_test_fixtures_addon.worker import WorkerService


class FakeSQL:
    def __init__(self):
        self.databases = {}
        self.dropped = []
        self.fail = False

    def provision_database(self, owner, request):
        self.databases[owner, request.logical_name] = True
        if self.fail:
            raise RuntimeError("synthetic secret must not escape")
        return WorkerMssqlDatabaseRecord(
            session_id=owner,
            logical_name=request.logical_name,
            database_name=owner + "_" + request.logical_name,
            server="192.0.2.1",
            port=1433,
            username="principal-" + owner,
            password="secret-" + owner,
            connection_string="secret-connection-" + owner,
        )

    def drop_session(self, owner):
        self.dropped.append(owner)
        for key in list(self.databases):
            if key[0] == owner:
                del self.databases[key]


class FakeDocker:
    def __init__(self):
        self.brokers = set()
        self.bindings = {}
        self.calls = []
        self.fail_cleanup = False
        self.running = True

    def current_job(self, job):
        return {"NetworkSettings": {"Networks": {}}} if self.running else None

    def ensure_broker(self, lease):
        self.brokers.add(lease["lease_id"])
        return "198.51.100.10"

    def bind(self, lease, job, connections):
        self.bindings[lease["lease_id"]] = (job.job_id, connections)

    def unbind(self, lease):
        self.bindings.pop(lease["lease_id"], None)

    def remove_broker(self, lease):
        if self.fail_cleanup:
            raise RuntimeError("secret cleanup failure")
        self.brokers.discard(lease["lease_id"])

    def control(self, lease, action):
        self.calls.append(action)

    def recover(self, lease):
        self.calls.append("recover")

    def firewall(self, lease, job_ip):
        self.calls.append("firewall")


@pytest.fixture
def scope():
    return Scope(
        session_id="session-a",
        agent_id="agent-a",
        team_id="team-a",
        project_id="project-a",
        task_id="task-a",
        dispatch_id="dispatch-a",
        dispatch_expires_at=100000,
        directory="root",
        worker_id="worker-a",
    )


@pytest.fixture
def worker(tmp_path):
    clock = [1000.0]
    job = SimpleNamespace(
        job_id="job-a",
        container_id="a" * 64,
        status="running",
        metadata={"dispatch_id": "dispatch-a"},
    )
    jobs = [job]
    service = WorkerService(
        WorkerSettings(
            runtime_dir=str(tmp_path / "worker"), worker_id="worker-a", allocations_enabled=True
        ),
        sql=FakeSQL(),
        docker=FakeDocker(),
        jobs=SimpleNamespace(list_by_session=lambda _: jobs),
        now=lambda: clock[0],
    )
    service.clock = clock
    service.test_jobs = jobs
    return service


@pytest.fixture(autouse=True)
def addon_pythonpath(monkeypatch):
    paths = [str(Path(__file__).resolve().parents[1])]
    if os.environ.get("PYTHONPATH"):
        paths.append(os.environ["PYTHONPATH"])
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))
