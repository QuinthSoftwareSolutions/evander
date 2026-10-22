"""Browser worker regressions use synthetic Docker; no application add-on import."""

import json
from types import SimpleNamespace
import pytest
from evander.resources.container_runtime import RuntimeError as WorkerError
from evander_browser_sessions_addon.models import BrowserError
from evander_browser_sessions_addon.worker import Supervisor, WorkerSettings


class Docker:
    def __init__(self):
        self.calls = []
        self.resources = {}
        self.fail_remove = False

    def __call__(self, *args, check=True, env=None):
        self.calls.append(args)
        result = SimpleNamespace(returncode=0, stdout="{}")
        if args[0] != "docker":
            return result
        cmd = args[1:]
        if cmd[:2] == ("network", "create"):
            self.resources[cmd[-1]] = {
                "Labels": {"evander.resource": cmd[cmd.index("--label") + 1].split("=", 1)[1]},
                "IPAM": {"Config": [{"Subnet": "172.30.66.0/24"}]},
            }
        elif cmd[0] == "create":
            name = cmd[cmd.index("--name") + 1]
            self.resources[name] = {
                "Id": name,
                "Config": {
                    "Labels": {"evander.resource": cmd[cmd.index("--label") + 1].split("=", 1)[1]}
                },
                "State": {"Running": False},
                "NetworkSettings": {"Networks": {}},
            }
        elif cmd[0] == "start":
            self.resources[cmd[-1]]["State"]["Running"] = True
        elif len(cmd) > 1 and cmd[1] == "inspect":
            result.returncode = 0 if cmd[2] in self.resources else 1
            result.stdout = json.dumps([self.resources[cmd[2]]]) if result.returncode == 0 else ""
        elif cmd[0] == "rm" or cmd[:2] == ("network", "rm"):
            if self.fail_remove:
                raise WorkerError("remove failed")
            self.resources.pop(cmd[-1], None)
        return result


@pytest.fixture
def worker(tmp_path, hosted):
    docker = Docker()
    settings = WorkerSettings(
        worker_id="worker-a",
        worker_root=tmp_path / "worker",
        runtime_dir=tmp_path / "state",
        allocations_enabled=True,
        profiles={"web": {"image": "seeded-web:1"}},
        relay_urls=["https://relay.test/_browser/endpoint"],
        http_target_browser_proxy_url="http://192.0.2.10:11778",
    )
    jobs = SimpleNamespace(allow_rule=lambda *args: None)
    supervisor = Supervisor(settings, command=docker, jobs=jobs)
    payload = {
        "session": hosted.model_dump(),
        "profile": settings.profiles["web"].model_dump(),
        "relay_url": settings.relay_urls[0],
        "endpoint_token": "endpoint-private",
        "proxy_token": "proxy-private",
    }
    return supervisor, docker, payload


def test_browser_only_allocation_has_no_application_or_checkout(worker, monkeypatch):
    supervisor, docker, payload = worker
    monkeypatch.setattr("socket.gethostbyname", lambda host: "192.0.2.10")
    result = supervisor.create(payload)
    assert supervisor.create(payload) == result
    assert not docker.calls
    supervisor.reconcile()
    lease = supervisor.get(result["id"])
    assert lease["state"] == "running"
    creates = [call for call in docker.calls if call[:2] == ("docker", "create")]
    assert len(creates) == 1 and creates[0][creates[0].index("--name") + 1].endswith("-browser")
    assert "/workspace" not in str(creates) and "--publish" not in creates[0]
    assert "endpoint-private" not in str(creates)
    assert "proxy-private" not in str(supervisor.receipt(lease))
    assert supervisor.close(result["id"]) == {"released": True}
    assert not docker.resources


def test_worker_profile_and_relay_cannot_be_overridden(worker):
    supervisor, docker, payload = worker
    payload["profile"]["image"] = "foreign:1"
    with pytest.raises(BrowserError, match="approval"):
        supervisor.create(payload)
    payload["profile"] = supervisor.settings.profiles["web"].model_dump()
    payload["relay_url"] = "https://foreign.test/_browser/endpoint"
    with pytest.raises(BrowserError, match="approved"):
        supervisor.create(payload)
    assert not docker.calls


def test_interrupted_startup_is_not_replayed_and_cleanup_reserves_failures(worker, monkeypatch):
    supervisor, docker, payload = worker
    monkeypatch.setattr("socket.gethostbyname", lambda host: "192.0.2.10")
    result = supervisor.create(payload)
    supervisor.reconcile()
    docker.fail_remove = True
    with pytest.raises(BrowserError, match="cleanup"):
        supervisor.close(result["id"])
    assert supervisor.get(result["id"])["state"] == "cleanup_failed"
    docker.fail_remove = False
    assert supervisor.close(result["id"])["released"]
    assert not docker.resources


def test_unknown_application_endpoint_is_denied_before_container_allocation(worker):
    supervisor, docker, payload = worker
    payload["session"]["endpoint_refs"] = ["ep-" + "a" * 32]
    payload["session"]["endpoint_origins"] = {"ep-" + "a" * 32: "http://application:4200"}
    payload["session"]["allowed_origins"] = ["http://application:4200"]
    with pytest.raises(ValueError):
        supervisor.create(payload)
    assert not docker.calls and not supervisor.leases()


def test_attached_app_network_retains_only_declared_browser_services(worker, monkeypatch):
    from evander.resources.endpoints import Endpoint

    supervisor, docker, payload = worker
    monkeypatch.setattr("socket.gethostbyname", lambda host: "192.0.2.10")
    endpoint = Endpoint(
        ref="ep-" + "a" * 32,
        resource_id="environment-one",
        session_id=payload["session"]["agent_session_id"],
        directory=payload["session"]["directory"],
        worker_id="worker-a",
        origin="http://application:4200",
        port=4200,
        expires_at=payload["session"]["expires_at"],
        ready=True,
        network="application-network",
        address="172.30.77.2",
    )
    supervisor.endpoints.put(endpoint)
    docker.resources["application-network"] = {"IPAM": {"Config": [{"Subnet": "172.30.77.0/24"}]}}
    payload["session"].update(
        endpoint_refs=[endpoint.ref],
        endpoint_origins={endpoint.ref: endpoint.origin},
        allowed_origins=[*payload["session"]["allowed_origins"], endpoint.origin],
    )
    allowed = []
    supervisor.jobs.allow_rule = lambda chain, rule: allowed.append(rule)
    created = supervisor.create(payload)
    supervisor.reconcile()
    assert supervisor.get(created["id"])["state"] == "running"
    assert all(rule[rule.index("-s") + 1] == "172.30.77.3/32" for rule in allowed)
    assert {(rule[rule.index("-d") + 1], rule[rule.index("--dport") + 1]) for rule in allowed} == {
        ("172.30.77.2/32", "4200"),
        ("192.0.2.10/32", "443"),
        ("192.0.2.10/32", "11778"),
    }


def test_close_during_startup_waits_for_cleanup_and_cannot_resurrect_lease(worker, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading, time

    supervisor, docker, payload = worker
    monkeypatch.setattr("socket.gethostbyname", lambda host: "192.0.2.10")
    entered, proceed = threading.Event(), threading.Event()

    def blocked(*args, **kwargs):
        result = docker(*args, **kwargs)
        if args[:3] == ("docker", "network", "create"):
            entered.set()
            assert proceed.wait(3)
        return result

    supervisor.command = blocked
    created = supervisor.create(payload)
    with ThreadPoolExecutor(2) as pool:
        start = pool.submit(supervisor.reconcile)
        assert entered.wait(3)
        close = pool.submit(supervisor.close, created["id"])
        deadline = time.monotonic() + 3
        while supervisor.get(created["id"])["state"] != "closing" and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            assert supervisor.get(created["id"])["state"] == "closing"
        finally:
            proceed.set()
        start.result(timeout=3)
        assert close.result(timeout=3) == {"released": True}
    assert supervisor.get(created["id"])["state"] == "closed"
    assert not docker.resources
