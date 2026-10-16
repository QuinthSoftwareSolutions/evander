"""Opt-in native broker tests. No image pulls or published ports."""

import os
from pathlib import Path
import time
import uuid
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evander_rabbitmq_addon.adapter import Adapter
from evander_rabbitmq_addon.docker import Brokers
from evander.resources.models import Lease, Owner, Profile
from evander.resources.state import Store

IMAGE = os.environ.get("EVANDER_RABBITMQ_SMOKE_IMAGE")
pytestmark = pytest.mark.skipif(
    not IMAGE, reason="Select a preseeded RabbitMQ image for disposable native tests"
)


def test_native_brokers_shared_vhosts_faults_and_cleanup(tmp_path):
    import pika

    adapter = Adapter(docker=Brokers(tmp_path / "brokers", [IMAGE]))
    store = Store(tmp_path / "journal")
    isolated = Lease(
        lease_id="rs-" + uuid.uuid4().hex,
        owner=Owner(key="owner", directory="root"),
        profile_id="isolated",
        profile=Profile(worker_id="test", options={"mode": "isolated", "image": IMAGE}),
        created_at=time.time(),
        expires_at=time.time() + 7200,
    )

    class Credentials:
        def resolve(self, *args):
            return {
                "username": isolated.resources["admin_user"],
                "password": isolated.resources["admin_password"],
            }

    credentials = Credentials()
    shared = []
    try:
        deadline = time.monotonic() + 120
        while True:
            try:
                adapter.provision(isolated, lambda: store.put(isolated), credentials)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        private = adapter.private_connections(isolated)["broker"]
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=private["host"],
                port=private["port"],
                virtual_host=private["vhost"],
                credentials=pika.PlainCredentials(private["username"], private["password"]),
                socket_timeout=5,
            )
        )
        channel = connection.channel()
        channel.queue_declare(queue="probe", durable=True)
        channel.queue_declare(queue="batch-probe", durable=True)
        channel.confirm_delivery()
        channel.basic_publish(
            exchange="",
            routing_key="probe",
            body=b"retained",
            properties=pika.BasicProperties(delivery_mode=2),
        )
        connection.close()
        assert adapter.lease_call(
            isolated, "publish", {"routing_key": "batch-probe", "payload": "single"}
        )["routed"]
        batch = adapter.lease_call(
            isolated,
            "publish",
            {
                "messages": [
                    {"routing_key": "batch-probe", "payload": value} for value in ("one", "two")
                ]
            },
        )
        assert batch["count"] == 2 and all(value["routed"] for value in batch["results"])
        received = adapter.lease_call(
            isolated, "get_messages", {"name": "batch-probe", "count": 3, "requeue": False}
        )
        assert [value["payload"] for value in received["messages"]] == ["single", "one", "two"]
        result = adapter.lease_call(isolated, "get_messages", {"name": "probe", "requeue": True})
        assert result["messages"][0]["payload"] == "retained"
        adapter.docker.control(isolated, "pause")
        assert adapter.docker.inspect(isolated.lease_id)["State"]["Paused"]
        adapter.docker.recover(isolated)
        adapter.docker.control(isolated, "stop")
        assert not adapter.docker.inspect(isolated.lease_id)["State"]["Running"]
        adapter.docker.recover(isolated)
        deadline = time.monotonic() + 60
        while True:
            try:
                adapter.http(
                    isolated.resources["connection"],
                    {
                        "username": isolated.resources["admin_user"],
                        "password": isolated.resources["admin_password"],
                    },
                    "GET",
                    "health/checks/alarms",
                )
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        assert (
            adapter.lease_call(isolated, "get_messages", {"name": "probe", "requeue": False})[
                "messages"
            ][0]["payload"]
            == "retained"
        )
        for index in range(2):
            lease = Lease(
                lease_id="rs-" + uuid.uuid4().hex,
                owner=Owner(key=f"shared-{index}", directory="root"),
                profile_id="shared",
                profile=Profile(connection_id="existing", options={"mode": "shared"}),
                connection=isolated.resources["connection"],
                created_at=time.time(),
                expires_at=time.time() + 7200,
            )
            shared.append(lease)
            adapter.provision(lease, lambda: store.put(lease), credentials)
        adapter.reset(shared[0], lambda: store.put(shared[0]), credentials)
        adapter.provision(shared[0], lambda: store.put(shared[0]), credentials)
        adapter.provision(shared[1], lambda: store.put(shared[1]), credentials)
        assert shared[0].resources["vhost"] != shared[1].resources["vhost"]
        for lease in shared:
            adapter.release(lease, lambda: store.put(lease), credentials)
            adapter.verify_absent(
                lease, {"existing": isolated.resources["connection"]}, credentials
            )
        shared.clear()
        adapter.reset(isolated, lambda: store.put(isolated), credentials)
    finally:
        for lease in shared:
            adapter.release(lease, lambda: store.put(lease), credentials)
        adapter.release(isolated, lambda: store.put(isolated), credentials)
        adapter.verify_absent(isolated, {}, credentials)


@pytest.mark.skipif(
    not os.environ.get("EVANDER_RESOURCE_SMOKE_RUNNER_IMAGE"),
    reason="Select a preseeded runner for native private-job binding",
)
@pytest.mark.parametrize("read_only", [False, True])
def test_native_private_job_binding_rebind_and_reaping(tmp_path, read_only):
    import json
    import subprocess
    from evander.resources.jobs import Jobs
    from evander.resources.worker import WorkerService
    from evander.resources.models import WorkerSettings, ResourceError
    from evander.resources.projection import PRIVATE_TMPFS_SPEC
    from evander.worker.models import WorkerJobRecord
    from evander.worker.store import WorkerJobStore

    image = os.environ["EVANDER_RESOURCE_SMOKE_RUNNER_IMAGE"]
    session = "smoke-session-" + uuid.uuid4().hex[:16]
    name = "evander-resource-job-" + uuid.uuid4().hex[:16]
    job_network = name + "-network"
    foreign_network = name + "-foreign"
    names = [name, name + "-other"]

    def docker(*args, check=True):
        return (
            subprocess.run(["docker", *args], check=check, capture_output=True, timeout=30)
            .stdout.decode()
            .strip()
        )

    # This harness has no host NET_ADMIN. Exercise real job/network/file operations,
    # while the host firewall command adapter is synthetic and separately asserted.
    rules = set()

    def run(args, **kwargs):
        if args[0] != "iptables":
            return subprocess.run(args, **kwargs)
        action = args[3]
        rule = tuple(args[6:] if action == "-I" else args[5:])
        code = 0
        if action == "-C":
            code = 0 if rule in rules else 1
        elif action == "-I":
            rules.add(rule)
        elif action == "-D":
            rules.discard(rule)
        return subprocess.CompletedProcess(args, code, stdout=b"", stderr=b"")

    jobs_store = WorkerJobStore(tmp_path / "jobs.db")
    jobs = Jobs("rabbitmq-addon", tmp_path / "jobs.db", jobs=jobs_store, run=run)
    clock = [time.time()]
    settings = WorkerSettings(
        runtime_dir=str(tmp_path / "worker"),
        worker_id="smoke-worker",
        token_env="TEST_SMOKE_TOKEN",
        allocations_enabled=True,
        allowed_images=[IMAGE],
    )
    adapter = Adapter(docker=Brokers(tmp_path / "brokers", [IMAGE]))
    service = WorkerService("rabbitmq-addon", settings, adapter, jobs=jobs, now=lambda: clock[0])
    lease = Lease(
        lease_id="rs-" + uuid.uuid4().hex,
        owner=Owner(
            provider="synthetic-consumer",
            key="task",
            session_id=session,
            directory="root",
            job_tags={"dispatch_id": "dispatch-one"},
            valid_until=time.time() + 300,
        ),
        profile_id="isolated",
        profile=Profile(worker_id="smoke-worker", options={"mode": "isolated", "image": IMAGE}),
        created_at=clock[0],
        expires_at=clock[0] + 7200,
    )
    try:
        for network in [job_network, foreign_network]:
            docker("network", "create", "--internal", "--label", "evander.smoke=" + name, network)
        records = []
        for index, container in enumerate(names):
            cid = docker(
                "run",
                "--detach",
                "--pull=never",
                "--name",
                container,
                "--label",
                "evander.smoke=" + name,
                "--network",
                job_network if index == 0 else foreign_network,
                "--user",
                "1000:1000",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                *(
                    ["--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777"]
                    if read_only
                    else []
                ),
                "--tmpfs",
                PRIVATE_TMPFS_SPEC,
                "--entrypoint",
                "python3",
                image,
                "-c",
                "import time; time.sleep(300)",
            )
            record = WorkerJobRecord(
                job_id="job-" + str(index),
                session_id=session,
                image=image,
                command=["python3"],
                container_name=container,
                container_id=cid,
                status="running",
                metadata={"dispatch_id": "dispatch-one" if index == 0 else "dispatch-other"},
                agent_home_path="/home/agent",
                repo_root_path="/home/agent/repo",
                artifacts_path="/artifacts",
                logs_path=str(tmp_path / "logs"),
            )
            jobs_store.put(record)
            records.append(record)
        deadline = time.monotonic() + 120
        while True:
            value = service.ensure(lease)
            if value["state"] == "ready":
                break
            if time.monotonic() >= deadline:
                pytest.fail("Worker did not bind its private configuration")
            time.sleep(1)
        assert value["bound_job_id"] == "job-0"
        assert "password" not in json.dumps(value)
        proof = json.loads(
            docker(
                "exec",
                names[0],
                "python3",
                "-c",
                'import json,os,stat,sys; p=sys.argv[1]; s=os.stat(p); d=json.load(open(p)); print(json.dumps({"mode":stat.S_IMODE(s.st_mode),"uid":s.st_uid,"has_password":bool(d["broker"]["password"])}))',
                value["config_path"],
            )
        )
        assert proof == {"mode": 0o600, "uid": 1000, "has_password": True}
        assert (
            docker(
                "exec",
                names[0],
                "python3",
                "-c",
                "import os,sys; print(os.access(os.path.dirname(sys.argv[1]), os.W_OK))",
                value["config_path"],
            )
            == "False"
        )
        assert (
            docker(
                "exec",
                names[1],
                "python3",
                "-c",
                "import os,sys; print(os.path.exists(sys.argv[1]))",
                value["config_path"],
            )
            == "False"
        )
        assert rules and all(
            "ACCEPT" in rule and lease.lease_id in " ".join(rule) for rule in rules
        )
        changed = lease.model_copy(deep=True)
        changed.owner.key = "foreign-task"
        with pytest.raises(ResourceError):
            service.ensure(changed)
        # Simulate junk left by the legacy job-writable delivery protocol. The
        # capless root helper cannot traverse this job-owned mode-000 directory.
        docker(
            "exec",
            "--user",
            "0:0",
            names[0],
            "python3",
            "-c",
            "import os,sys; os.chmod(os.path.dirname(sys.argv[1]), 0o733)",
            value["config_path"],
        )
        docker(
            "exec",
            names[0],
            "python3",
            "-c",
            "import os,sys; p=os.path.dirname(sys.argv[1]); open(p+'/connection.new','w').write('old-private'); open('/tmp/keep','w').write('unrelated'); os.symlink('/tmp/keep',p+'/link'); os.mkdir(p+'/blocked',0o700); open(p+'/blocked/junk','w').write('job-owned'); os.chmod(p+'/blocked',0)",
            value["config_path"],
        )
        bound = service.store.get(lease.lease_id)
        bound.owner.valid_until = time.time() - 1
        service.store.put(bound)
        provision = adapter.provision
        attempts = []

        def unavailable(*args):
            attempts.append(True)
            raise OSError("Provider unavailable")

        adapter.provision = unavailable
        service.tick()
        assert not attempts and not rules
        assert jobs.inspect(records[0].container_id)["State"]["Running"]
        assert service.store.get(lease.lease_id).config_path is None
        assert (
            docker(
                "exec",
                names[0],
                "python3",
                "-c",
                "import os,sys; assert not os.path.exists(os.path.dirname(sys.argv[1])); assert open('/tmp/keep').read()=='unrelated'; print('withdrawn')",
                value["config_path"],
            )
            == "withdrawn"
        )
        adapter.provision = provision
        assert service.ensure(lease)["state"] == "ready"
        docker("stop", names[0])
        records[0].status = "exited"
        jobs_store.put(records[0])
        service.tick()
        assert not rules and service.store.get(lease.lease_id).config_path is None
        # Docker retains the default writable root layer after stop. Neither
        # copying it nor restarting that container may recover private files.
        assert (
            subprocess.run(
                ["docker", "cp", names[0] + ":" + value["config_path"], "-"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            ).returncode
            != 0
        )
        docker("start", names[0])
        assert (
            docker(
                "exec",
                names[0],
                "python3",
                "-c",
                "import os,sys; print(os.path.exists(sys.argv[1]))",
                value["config_path"],
            )
            == "False"
        )
        docker("stop", names[0])
        clock[0] += 7201
        service.tick()
        assert service.store.get(lease.lease_id).state == "released"
        adapter.verify_absent(lease, {}, service.credentials)
    finally:
        current = (
            service.store.get(lease.lease_id)
            if (service.store.path.exists() and service.store.leases())
            else lease
        )
        jobs.unbind(current, lambda: service.store.put(current))
        if current.resources:
            adapter.release(current, lambda: service.store.put(current), service.credentials)
        for container in names:
            docker("rm", "--force", container, check=False)
        for network in [job_network, foreign_network]:
            docker("network", "rm", network, check=False)
