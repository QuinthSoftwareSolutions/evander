from types import SimpleNamespace
import time
import pytest
from evander.resources.endpoints import Endpoints
from evander.resources.models import ResourceError
from evander_application_environments_addon.models import Settings, CreateRequest
from evander_application_environments_addon.service import Service


class Peer:
    def __init__(self):
        self.calls = []
        self.fail_close = False

    def __call__(self, base, token):
        return SimpleNamespace(
            call=lambda method, path, payload=None: self.call(base, method, path, payload)
        )

    def call(self, base, method, path, payload):
        self.calls.append((base, method, path, payload))
        if path == "/healthz":
            return {"worker_id": "worker-a", "capabilities": {"application_environments": 1}}
        if path.endswith("/close"):
            if self.fail_close:
                raise ResourceError("Offline")
            return {"released": True}
        id = path.rsplit("/", 1)[-1]
        return {
            "id": id,
            "state": "accepted" if method == "POST" else "running",
            "workspace_path": "/home/agent/repos/app",
            "endpoints": [
                {"ref": "ep-" + "a" * 32, "origin": "http://application:4200", "port": 4200}
            ],
        }


@pytest.fixture
def domain(tmp_path):
    peers = Peer()
    parent = lambda id, tool=None: SimpleNamespace(
        session_id=id,
        directory="root/example",
        worker_id="worker-a",
        worker_url="https://worker.test",
        expires_at=time.time() + 20000,
    )
    service = Service(tmp_path, None, parent=parent, peers=peers)
    settings = Settings(
        allocations_enabled=True,
        workers={
            "worker-a": {
                "base_url": "https://application-worker.test",
                "parent_worker_url": "https://worker.test",
                "token_env": "APPLICATION_TOKEN",
            }
        },
        profiles={"web": {"image": "approved:1"}},
    )
    service.store.save_settings("root", 0, settings)
    request = CreateRequest(
        request_id="one",
        profile_id="web",
        workspace_ref="a" * 32,
        launch_manifest_path=".evander/preview.json",
    )
    return service, peers, request


def test_creation_is_idempotent_ready_endpoints_are_scoped_and_close_is_pinned(domain, tmp_path):
    service, peers, request = domain
    created = service.create("agent-a", request)
    assert created["endpoints"] == []
    assert service.create("agent-a", request)["id"] == created["id"]
    assert (
        sum(method == "POST" and not path.endswith("/close") for _, method, path, _ in peers.calls)
        == 1
    )
    ready = service.status("agent-a", created["id"])
    endpoint = Endpoints(tmp_path).resolve(
        ready["endpoints"][0]["ref"], session_id="agent-a", directory="root/example"
    )
    assert endpoint.resource_id == ready["id"]
    with pytest.raises(ResourceError):
        service.status("agent-b", ready["id"])
    revision, settings = service.store.settings("root")
    settings.workers["worker-a"].base_url = "https://replacement.test"
    service.store.save_settings("root", revision, settings)
    assert service.close(ready["id"])["state"] == "closed"
    assert peers.calls[-1][0] == "https://application-worker.test"
    with pytest.raises(ResourceError):
        Endpoints(tmp_path).resolve(endpoint.ref, session_id="agent-a", directory="root/example")


def test_conflicts_and_cleanup_failure_reserve_resource_and_guard(domain):
    service, peers, request = domain
    created = service.create("agent-a", request)
    with pytest.raises(ResourceError, match="different inputs"):
        service.create("agent-a", request.model_copy(update={"title": "changed"}))
    peers.fail_close = True
    with pytest.raises(ResourceError):
        service.close(created["id"])
    assert service.store.get(created["id"])["state"] == "cleanup_failed"
    with pytest.raises(ResourceError, match="quota"):
        service.create("agent-b", request)
    peers.fail_close = False
    assert service.close(created["id"])["state"] == "closed"


def test_inherited_configuration_requires_effective_revision(domain):
    service, _, _ = domain
    revision, value = service.store.settings("root/example")
    assert revision == 1
    with pytest.raises(ResourceError, match="revision"):
        service.store.save_settings("root/example", 0, value)
    assert service.store.save_settings("root/example", revision, value) == 2


def test_close_cannot_overtake_an_in_flight_worker_allocation(domain):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    service, peers, request = domain
    entered, proceed, closing = threading.Event(), threading.Event(), threading.Event()
    ids = []
    original = peers.call

    def call(base, method, path, payload):
        if method == "POST" and not path.endswith("/close"):
            ids.append(path.rsplit("/", 1)[-1])
            entered.set()
            assert proceed.wait(3)
        return original(base, method, path, payload)

    peers.call = call

    def close():
        closing.set()
        return service.close(ids[0])

    with ThreadPoolExecutor(2) as pool:
        create = pool.submit(service.create, "agent-a", request)
        assert entered.wait(3)
        release = pool.submit(close)
        assert closing.wait(3)
        try:
            assert not release.done()
        finally:
            proceed.set()
        create.result(timeout=3)
        assert release.result(timeout=3)["state"] == "closed"
    assert service.store.get(ids[0])["state"] == "closed"
    mutations = [path for _, method, path, _ in peers.calls if method == "POST"]
    assert mutations[-1].endswith("/close")


def test_registered_workflow_nodes_share_the_application_lifecycle(domain, monkeypatch):
    from evander.events.models import WorkflowNodeEnvelope
    from evander_application_environments_addon import providers

    service, _, request = domain
    monkeypatch.setattr(providers, "Service", lambda *args: service)
    registration = providers.register_providers()
    context = SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=service.runtime_dir),
        admin_service=None,
        metadata={"directory_path": "root/example"},
    )

    def execute(type, config):
        table = (
            registration.query_providers
            if type.startswith("query:")
            else registration.effect_providers
        )
        provider = table[type]()
        provider.initialize(
            WorkflowNodeEnvelope(
                id="application",
                display_name="Application",
                type=type,
                config={"agent_session_id": "agent-a", **config},
            ),
            context,
        )
        return provider.execute()

    created = execute("effect:application-environment:ensure", {"request": request.model_dump()})
    ready = execute("query:application-environment:status", {"environment_id": created["id"]})
    assert ready["state"] == "ready" and ready["result"]["endpoints"]
    closed = execute("effect:application-environment:close", {"environment_id": ready["id"]})
    assert closed["state"] == "closed" and closed["id"] == created["id"]
