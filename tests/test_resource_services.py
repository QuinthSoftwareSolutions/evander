"""Provider-neutral regression tests; optional provider projects are not imported."""

import json
from types import SimpleNamespace

import pytest

from evander.addons import AddonContext, AddonRegistration
from evander.resources.authority import Authorities
from evander.resources.models import (
    Conflict,
    Lease,
    Owner,
    Profile,
    ResourceError,
    Settings,
    WorkerSettings,
)
from evander.resources.service import ResourceService
from evander.resources.state import Store
from evander.resources.worker import WorkerService


class Adapter:
    prefix = "sample"
    read_operations = {"inspect"}
    operations = {"inspect", "write"}

    def __init__(self):
        self.resources = set()
        self.writes = 0
        self.fail = False

    def validate_connection(self, raw):
        return raw

    def validate_profile(self, raw):
        return raw

    def validate_call(self, op, arguments):
        if op not in self.operations:
            raise ResourceError("Unsupported operation")

    def validate_request(self, connection, op, arguments):
        return self.validate_call(op, arguments)

    def provision(self, lease, save, credentials):
        if not lease.resources:
            lease.resources = {"secret": "synthetic-private-password", "identity": lease.lease_id}
            save()
        self.resources.add(lease.lease_id)
        if self.fail:
            raise RuntimeError("synthetic-private-password")

    def release(self, lease, save, credentials):
        if self.fail:
            raise RuntimeError("synthetic-private-password")
        self.resources.discard(lease.lease_id)

    def reset(self, lease, save, credentials):
        self.release(lease, save, credentials)
        self.provision(lease, save, credentials)

    def call(self, *args):
        self.writes += 1
        if self.fail:
            raise RuntimeError("synthetic-private-password")
        return {"accepted": True}

    def private_connections(self, lease):
        return {
            "app": {"host": "192.0.2.1", "port": 1234, "password": "synthetic-private-password"}
        }


class Authority:
    active = True

    def owns(self, owner):
        return self.active

    def notify(self, owner, value):
        pass


@pytest.fixture
def domain(tmp_path):
    context = AddonContext(
        "sample-addon", tmp_path, tmp_path / "addons/sample-addon", service=SimpleNamespace()
    )
    domain = ResourceService(context, Adapter(), authority=Authority(), now=lambda: 1000)
    domain.store.save_settings(
        "root",
        0,
        Settings(
            allocations_enabled=True,
            connections={"local": {"operations": ["inspect", "write"]}},
            profiles={"test": Profile(connection_id="local", options={"aliases": ["a", "b", "c"]})},
        ),
        "test",
    )
    return domain


def owner(key="session-a", directory="root"):
    return Owner(key=key, directory=directory)


def test_reload_partial_creation_recovery_and_private_receipt(domain):
    domain.adapter.fail = True
    first = domain.acquire(owner(), "test", "first")
    assert first["state"] == "failed"
    assert "synthetic-private-password" not in json.dumps(first)
    domain.store = Store(domain.store.root)
    domain.adapter.fail = False
    ready = domain.acquire(owner(), "test", "first")
    assert ready["state"] == "ready" and ready["lease_id"] == first["lease_id"]
    assert len(domain.adapter.resources) == 1
    assert domain.get(owner(), ready["lease_id"])["expires_at"] == ready["expires_at"]
    assert domain.queue(owner(), ready["lease_id"], "release")["state"] == "released"
    assert not domain.adapter.resources


def test_scope_quota_and_repeated_request_conflict(domain):
    first = domain.acquire(owner(), "test", "first")
    domain.acquire(owner("session-b"), "test", "second")
    with pytest.raises(ResourceError, match="quota"):
        domain.acquire(owner("session-c"), "test", "third")
    with pytest.raises(ResourceError, match="scope"):
        domain.get(owner("session-b"), first["lease_id"])
    _, settings = domain.store.settings("root")
    settings.profiles["other"] = Profile(connection_id="local")
    domain.store.save_settings("root", 1, settings, "test")
    with pytest.raises(Conflict):
        domain.acquire(owner(), "other", "first")


def test_disable_keeps_existing_lease_and_ownership_loss_verifies_cleanup(domain):
    first = domain.acquire(owner(), "test", "one")
    revision, settings = domain.store.settings("root")
    settings.allocations_enabled = False
    domain.store.save_settings("root", revision, settings, "test")
    assert domain.acquire(owner(), "test", "new-request")["lease_id"] == first["lease_id"]
    with pytest.raises(ResourceError, match="disabled"):
        domain.acquire(owner("new"), "test", "new")
    domain.authority.active = False
    domain.adapter.fail = True
    assert domain.reconcile(first["lease_id"])["state"] == "cleanup_failed"
    domain.adapter.fail = False
    assert domain.reconcile(first["lease_id"])["state"] == "released"


def test_unknown_write_is_not_replayed_after_reload_or_new_service(domain):
    domain.adapter.fail = True
    first = domain.call(owner(), "local", "write", {"value": 1}, "write-one")
    assert first["state"] == "uncertain"
    domain.store = Store(domain.store.root)
    domain.adapter.fail = False
    assert domain.call(owner(), "local", "write", {"value": 1}, "write-one") == first
    assert domain.adapter.writes == 1
    with pytest.raises(Conflict):
        domain.call(owner(), "local", "write", {"value": 2}, "write-one")
    assert "synthetic-private-password" not in json.dumps(domain.store.operations("root"))


def test_settings_cas_and_operator_release_guard(domain):
    first = domain.acquire(owner(), "test", "one")
    with pytest.raises(Conflict):
        domain.store.save_settings("root", 0, Settings(), "test")
    with pytest.raises(Conflict):
        domain.operator_release("root", first["lease_id"], first["revision"] - 1)
    with pytest.raises(ResourceError):
        domain.operator_release("root/other", first["lease_id"], first["revision"])
    assert (
        domain.operator_release("root", first["lease_id"], first["revision"])["state"] == "released"
    )


def test_reset_request_replay_does_not_repeat_destructive_work(domain):
    first = domain.acquire(owner(), "test", "one")
    calls = []
    original = domain.adapter.reset

    def reset(*args):
        calls.append("reset")
        return original(*args)

    domain.adapter.reset = reset
    domain.queue(owner(), first["lease_id"], "reset", request_id="reset-one")
    domain.store = Store(domain.store.root)
    replay = domain.queue(owner(), first["lease_id"], "reset", request_id="reset-one")
    assert replay["state"] == "ready" and calls == ["reset"]
    with pytest.raises(Conflict):
        domain.queue(owner(), first["lease_id"], "release", request_id="reset-one")


def test_notice_retry_preserves_frozen_payload_and_id(domain):
    notices = []

    def notify(owner, value):
        notices.append(value)
        if len(notices) == 1:
            raise RuntimeError("Publication acknowledgement was lost")

    domain.authority.notify = notify
    with pytest.raises(RuntimeError):
        domain.acquire(owner(), "test", "first")
    domain.store = Store(domain.store.root)
    lease = domain.store.leases()[0]
    domain.reconcile(lease.lease_id)
    domain.reconcile(lease.lease_id)
    assert len(notices) == 2 and notices[0] == notices[1]
    assert domain.store.get(lease.lease_id).pending_notice is None


def test_resource_credentials_remain_bound_to_connection_and_directory(tmp_path):
    from evander.credentials.store import CredentialStore, CredentialUnavailable
    from evander.resources.credentials import Credentials

    runtime, key = tmp_path / "runtime", tmp_path / "custody-key"
    store = CredentialStore(runtime, key_file=key)
    store.initialize()
    credentials = Credentials(runtime, "sample-addon", store=store)
    raw = {"host": "service.example.invalid", "operations": ["inspect"]}
    created = credentials.create(
        "existing", raw, "read", "root/one", {"username": "reader", "password": "synthetic-secret"}
    )
    configured = {**raw, "credentials": {"read": {"credential_ref": created["id"]}}}
    assert credentials.resolve("existing", configured, "read", "root/one")["username"] == "reader"
    with pytest.raises(CredentialUnavailable):
        credentials.resolve("existing", configured, "read", "root/two")
    with pytest.raises(CredentialUnavailable):
        credentials.resolve(
            "existing", {**configured, "host": "other.example.invalid"}, "read", "root/one"
        )


def test_optional_consumer_cannot_be_bypassed_by_session_scope():
    authority = SimpleNamespace(handles_session=lambda _: True)
    runtime = SimpleNamespace(
        policy=SimpleNamespace(
            require_tool=lambda *a: None,
            get_session=lambda _: SimpleNamespace(directory_path="root"),
        ),
        resource_authorities={"consumer": authority},
    )
    with pytest.raises(ResourceError, match="owning add-on"):
        Authorities(runtime).resolve("session", "session", {}, "sample_acquire")
    assert (
        AddonRegistration(
            "sample", "Sample", resource_authority=lambda _: authority
        ).resource_authority(None)
        is authority
    )


class Jobs:
    live = True
    bound = None

    def current(self, owner):
        return (SimpleNamespace(job_id="job-a"), {}) if self.live else None

    def bind(self, lease, job, connections, save):
        lease.bound_job_id, lease.config_path = "job-a", "/tmp/private/connection.json"
        self.bound = connections
        save()

    def refresh(self, lease, job, save):
        pass

    def unbind(self, lease, save):
        lease.bound_job_id = lease.config_path = self.bound = None
        save()


def test_worker_reaper_unbinds_then_releases_after_deadline(tmp_path):
    clock = [1000]
    jobs, adapter = Jobs(), Adapter()
    service = WorkerService(
        "sample-addon",
        WorkerSettings(
            runtime_dir=str(tmp_path),
            worker_id="worker-a",
            token_env="TEST_TOKEN",
            allocations_enabled=True,
            connections={"local": {}},
        ),
        adapter,
        jobs=jobs,
        now=lambda: clock[0],
    )
    lease = Lease(
        lease_id="rs-" + "1" * 32,
        owner=owner(),
        profile_id="test",
        profile=Profile(connection_id="local", worker_id="worker-a"),
        created_at=1000,
        expires_at=8200,
    )
    first = service.ensure(lease)
    assert first["state"] == "ready" and jobs.bound["app"]["password"]
    assert "synthetic-private-password" not in json.dumps(first)
    jobs.live = False
    service.tick()
    assert jobs.bound is None
    clock[0] = 8201
    service.tick()
    assert service.store.get(lease.lease_id).state == "released"
    assert not adapter.resources


def test_operator_api_guards_directory_revision_origin_and_private_results(domain, monkeypatch):
    import asyncio
    from dataclasses import replace
    import httpx
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from evander.resources.surfaces import admin_routes
    import evander.resources.surfaces as surfaces

    class Auth:
        def require(self, request):
            return {"login": "operator"}

        def require_permission(self, request, permission, directory_path):
            if directory_path != "root":
                raise PermissionError

    context = replace(domain.context, auth=Auth())
    monkeypatch.setattr(surfaces, "request_has_privileged_operations", lambda **kw: True)
    app = Starlette(
        routes=[Mount("/api/addons/sample-addon", routes=admin_routes(context, lambda _: domain))]
    )
    domain.store.put_operation(
        "private",
        "fingerprint",
        {
            "id": "private",
            "directory": "root",
            "operation": "write",
            "state": "completed",
            "result": {"private": "private-application-data"},
        },
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://control.test"
        ) as client:
            assert (
                await client.get("/api/addons/sample-addon/settings?directory=root/foreign")
            ).status_code == 403
            response = await client.get("/api/addons/sample-addon/operations?directory=root")
            assert response.status_code == 200 and "private-application-data" not in response.text
            candidate = domain.store.settings("root")[1].model_dump()
            assert (
                await client.put("/api/addons/sample-addon/settings", json={"settings": candidate})
            ).status_code == 428
            assert (
                await client.put(
                    "/api/addons/sample-addon/settings",
                    json={"expected_revision": 0, "settings": candidate},
                )
            ).status_code == 409
            assert (
                await client.put(
                    "/api/addons/sample-addon/settings",
                    json={"expected_revision": 1, "settings": candidate},
                    headers={"Origin": "http://foreign.test"},
                )
            ).status_code == 403
            good = await client.put(
                "/api/addons/sample-addon/settings",
                json={"expected_revision": 1, "settings": candidate},
            )
            assert good.status_code == 200 and good.json()["revision"] == 2

    asyncio.run(scenario())


def test_workflow_capabilities_trace_scope_and_write_replay(domain):
    from evander.resources.providers import register_providers
    from evander.events.models import WorkflowNodeEnvelope

    revision, settings = domain.store.settings("root")
    settings.workflow_operations = ["write", "inspect", "acquire", "reconcile"]
    domain.store.save_settings("root", revision, settings, "operator")
    registration = register_providers(
        lambda context: domain, "sample", Adapter.read_operations, {"write"}
    )
    context = SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=domain.context.runtime_dir),
        metadata={"directory_path": "root", "graph_id": "graph-one"},
        admin_service=None,
    )
    query = registration.query_providers["query:sample:read"]()
    query.initialize(
        WorkflowNodeEnvelope(
            id="query",
            display_name="Read",
            type="query:sample:read",
            config={"operation": "write", "connection_id": "local", "arguments": {}},
        ),
        context,
    )
    with pytest.raises(ResourceError, match="capability"):
        query.execute(execution_context={"workflow_run_id": "run-one", "graph_id": "graph-one"})
    assert domain.adapter.writes == 0
    effect = registration.effect_providers["effect:sample:operate"]()
    effect.initialize(
        WorkflowNodeEnvelope(
            id="effect",
            display_name="Write",
            type="effect:sample:operate",
            config={"operation": "write", "connection_id": "local", "arguments": {}},
        ),
        context,
    )
    with pytest.raises(ResourceError, match="trusted"):
        effect.execute()
    execution = {
        "workflow_run_id": "run-one",
        "graph_id": "graph-one",
        "idempotency_key": "x" * 400,
    }
    first = effect.execute(execution_context=execution)
    assert first["result"]["state"] == "completed"
    assert effect.execute(execution_context=execution) == first and domain.adapter.writes == 1
    execution["graph_id"] = "graph-two"
    effect.execute(execution_context=execution)
    assert domain.adapter.writes == 2


def test_mcp_lifecycle_schema_and_real_dispatch_use_ambient_scope(domain, monkeypatch):
    import asyncio
    from mcp.server.mcpserver import MCPServer
    from evander.resources.surfaces import register_lifecycle
    import evander.resources.surfaces as surfaces

    monkeypatch.setattr(
        surfaces, "_resolve_session_id_from_context", lambda *a, **kw: "ambient-session"
    )
    calls = []

    def resolve(session, provider, reference, tool):
        calls.append((session, tool))
        return Owner(key=session, directory="root")

    domain.authority.resolve = resolve
    server = MCPServer("resource-test")
    register_lifecycle(server, domain.context, lambda _: domain, "sample")

    async def scenario():
        tools = await server.list_tools()
        reset = next(t for t in tools if t.name == "sample_reset")
        assert "request_id" in reset.input_schema["required"]
        assert "session_id" not in reset.input_schema["properties"]
        result = await server.call_tool(
            "sample_acquire", {"profile_id": "test", "request_id": "mcp-one"}
        )
        assert json.loads(result.content[0].text)["state"] == "ready"

    asyncio.run(scenario())
    assert calls == [("ambient-session", "sample_acquire")]


def test_worker_release_supersedes_a_pending_reset(tmp_path):
    clock = [1000]
    jobs, adapter = Jobs(), Adapter()
    service = WorkerService(
        "sample-addon",
        WorkerSettings(
            runtime_dir=str(tmp_path),
            worker_id="worker-a",
            token_env="TEST_TOKEN",
            allocations_enabled=True,
            connections={"local": {}},
        ),
        adapter,
        jobs=jobs,
        now=lambda: clock[0],
    )
    lease = Lease(
        lease_id="rs-" + "a" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(connection_id="local", worker_id="worker-a"),
        created_at=1000,
        expires_at=8200,
    )
    service.ensure(lease)
    jobs.live = False
    lease.operation = {"kind": "reset", "id": "reset", "arguments": {}}
    assert service.ensure(lease)["state"] == "awaiting_job"
    lease.operation = {"kind": "release", "id": "release", "arguments": {}}
    assert service.ensure(lease)["state"] == "released" and not adapter.resources


def test_fault_deadline_survives_an_unknown_ack_and_service_reload(tmp_path):
    class Faults(Adapter):
        paused = False
        lost = True

        def validate_control(self, profile, arguments):
            pass

        def control(self, lease, action):
            self.paused = True
            if self.lost:
                self.lost = False
                raise RuntimeError("Acknowledgement lost after applying pause")

        def recover(self, lease):
            self.paused = False

    clock = [1000]
    settings = WorkerSettings(
        runtime_dir=str(tmp_path),
        worker_id="worker-a",
        token_env="TEST_TOKEN",
        allocations_enabled=True,
        connections={"local": {}},
    )
    jobs, adapter = Jobs(), Faults()

    def service():
        return WorkerService("sample-addon", settings, adapter, jobs=jobs, now=lambda: clock[0])

    worker = service()
    lease = Lease(
        lease_id="rs-" + "b" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(connection_id="local", worker_id="worker-a"),
        created_at=1000,
        expires_at=8200,
    )
    worker.ensure(lease)
    lease.operation = {
        "kind": "control",
        "id": "pause-once",
        "arguments": {"action": "pause", "duration_seconds": 20},
    }
    first = worker.ensure(lease)
    assert first["state"] == "failed" and first["fault_until"] == 1020 and adapter.paused
    clock[0] = 1010
    worker = service()
    lease.operation = {
        "kind": "control",
        "id": "pause-once",
        "arguments": {"action": "pause", "duration_seconds": 20},
    }
    second = worker.ensure(lease)
    assert second["last_control"]["status"] == "applied" and second["fault_until"] == 1020
    clock[0] = 1021
    worker.tick()
    assert not adapter.paused and worker.store.get(lease.lease_id).fault_until is None


@pytest.mark.parametrize("action", ["start", "resume"])
def test_failed_fault_recovery_preserves_deadline_without_a_job(tmp_path, action):
    class Faults(Adapter):
        paused = False
        recoveries = 0

        def validate_control(self, profile, arguments):
            pass

        def control(self, lease, action):
            if action in {"start", "resume"}:
                raise OSError("Docker operation failed")
            self.paused = True

        def recover(self, lease):
            self.paused = False
            self.recoveries += 1

    clock = [1000]
    settings = WorkerSettings(
        runtime_dir=str(tmp_path),
        worker_id="worker-a",
        token_env="TEST_TOKEN",
        allocations_enabled=True,
        connections={"local": {}},
    )
    jobs, adapter = Jobs(), Faults()
    worker = WorkerService("sample-addon", settings, adapter, jobs=jobs, now=lambda: clock[0])
    lease = Lease(
        lease_id="rs-" + "e" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(connection_id="local", worker_id="worker-a"),
        created_at=1000,
        expires_at=8200,
    )
    worker.ensure(lease)
    lease.operation = {
        "kind": "control",
        "id": "pause-once",
        "arguments": {"action": "pause", "duration_seconds": 60},
    }
    assert worker.ensure(lease)["fault_until"] == 1060
    lease.operation = {"kind": "control", "id": action, "arguments": {"action": action}}
    clock[0] = 1010
    failed = worker.ensure(lease)
    assert failed["state"] == "failed" and failed["fault_until"] == 1060
    jobs.live = False
    clock[0] = 1061
    # An independent reaper must retain the safety deadline across a restart.
    worker = WorkerService("sample-addon", settings, adapter, jobs=jobs, now=lambda: clock[0])
    worker.tick()
    recovered = worker.store.get(lease.lease_id)
    assert not adapter.paused and adapter.recoveries == 1
    assert recovered.fault_until is None and recovered.state == "awaiting_job"
    worker.tick()
    assert adapter.recoveries == 1


def test_unrelated_worker_write_does_not_block_fault_recovery_or_replay_guards(tmp_path):
    from threading import Event, Thread

    started, finish_write, reaped = Event(), Event(), Event()
    recovered, results, failures = [], [], []

    class Faults(Adapter):
        lease_operations = {"write"}

        def validate_control(self, profile, arguments):
            pass

        def control(self, lease, action):
            pass

        def recover(self, lease):
            recovered.append(lease.lease_id)

        def lease_call(self, lease, operation, arguments):
            self.writes += 1
            started.set()
            if not finish_write.wait(5):
                raise TimeoutError("Test did not release blocked operation")
            return {"accepted": True}

    clock = [1000]
    settings = WorkerSettings(
        runtime_dir=str(tmp_path),
        worker_id="worker-a",
        token_env="TEST_TOKEN",
        allocations_enabled=True,
        connections={"local": {}},
    )
    jobs, adapter = Jobs(), Faults()
    api, reaper = (
        WorkerService("sample-addon", settings, adapter, jobs=jobs, now=lambda: clock[0])
        for _ in range(2)
    )
    # Put the busy lease first in journal order: reapers must skip its lock.
    busy, paused = (
        Lease(
            lease_id="rs-" + digit * 32,
            owner=owner(),
            profile_id="p-" + digit,
            profile=Profile(connection_id="local", worker_id="worker-a"),
            created_at=1000,
            expires_at=8200,
        )
        for digit in ("0", "1")
    )
    api.ensure(busy)
    api.ensure(paused)
    paused.operation = {
        "kind": "control",
        "id": "pause-once",
        "arguments": {"action": "pause", "duration_seconds": 60},
    }
    api.ensure(paused)

    def write():
        try:
            results.append(api.lease_call(busy.lease_id, owner(), "write", {}, "write-once"))
        except Exception as exc:
            failures.append(exc)

    def reap():
        try:
            reaper.tick()
        except Exception as exc:
            failures.append(exc)
        finally:
            reaped.set()

    writing, reaping = Thread(target=write), Thread(target=reap)
    writing.start()
    try:
        assert started.wait(2), "Provider call did not start"
        clock[0] = 1061
        reaping.start()
        assert reaped.wait(2), "An unrelated provider call delayed independent recovery"
        assert recovered == [paused.lease_id]
        assert reaper.store.get(paused.lease_id).fault_until is None
        # The short journal reservation must still reject cross-lease ID reuse
        # while the original provider call has not acknowledged completion.
        with pytest.raises(Conflict, match="another operation"):
            reaper.lease_call(paused.lease_id, owner(), "write", {}, "write-once")
    finally:
        finish_write.set()
        writing.join(3)
        if reaping.ident is not None:
            reaping.join(3)
    assert not writing.is_alive() and not reaping.is_alive() and not failures
    assert results[0]["state"] == "completed"
    assert api.lease_call(busy.lease_id, owner(), "write", {}, "write-once") == results[0]
    assert adapter.writes == 1


def test_maintenance_starter_is_pure_scoped_and_needs_no_data_grants(tmp_path, domain):
    from evander.resources.starters import maintenance
    from evander.resources.providers import register_providers
    from evander.integration_registry import IntegrationRegistry
    from evander.integration_contracts import AddonIntegration
    from evander.addons import LoadedAddonCatalog

    starter = maintenance("sample")
    addon = AddonRegistration(
        "sample-addon", "Sample", integration=AddonIntegration(workflow_starters=(starter,))
    )
    context = AddonContext(
        "sample-addon",
        tmp_path / "preview",
        tmp_path / "preview/addons/sample-addon",
        directory_path="root/one",
    )
    registry = IntegrationRegistry(LoadedAddonCatalog((addon,), ()))
    pack = registry.build_workflow_starter("sample-addon", "maintenance", context, {})
    assert len(pack.graphs) == 1 and pack.graphs[0].directory_path == "root/one"
    assert not (tmp_path / "preview").exists()
    registration = register_providers(lambda _: domain, "sample", {"inspect"}, {"write"})
    for node in pack.ir.nodes:
        registration.node_definition_resolvers[node.node.type](node.node)
    assert {n.node.type for n in pack.ir.nodes} == {
        "trigger:sample:pending",
        "effect:sample:operate",
    }
    domain.store.save_settings("root/one", 0, domain.store.settings("root")[1], "operator")
    lease = domain.acquire(owner(directory="root/one"), "test", "maintenance-test")
    runtime = SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=tmp_path),
        metadata={"directory_path": "root/one"},
        admin_service=None,
    )
    effect = registration.effect_providers["effect:sample:operate"]()
    effect.initialize(pack.ir.nodes[1].node, runtime)
    result = effect.execute(resolved_config={"arguments": {"lease_id": lease["lease_id"]}})
    assert result["result"]["state"] == "ready"
    assert domain.store.settings("root/one")[1].workflow_operations == []
    runtime.metadata["directory_path"] = "root/foreign"
    with pytest.raises(ResourceError, match="outside"):
        effect.execute(resolved_config={"arguments": {"lease_id": lease["lease_id"]}})


def test_worker_startup_is_pending_and_not_a_permanent_failure(tmp_path):
    from evander.resources.models import NotReady

    class Starting(Adapter):
        first = True

        def provision(self, lease, save, credentials):
            super().provision(lease, save, credentials)
            if self.first:
                self.first = False
                raise NotReady("Starting")

    jobs, adapter = Jobs(), Starting()
    worker = WorkerService(
        "sample-addon",
        WorkerSettings(
            runtime_dir=str(tmp_path),
            worker_id="worker-a",
            token_env="TEST_TOKEN",
            allocations_enabled=True,
            connections={"local": {}},
        ),
        adapter,
        jobs=jobs,
        now=lambda: 1000,
    )
    lease = Lease(
        lease_id="rs-" + "c" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(connection_id="local", worker_id="worker-a"),
        created_at=1000,
        expires_at=8200,
    )
    first = worker.ensure(lease)
    assert first["state"] == "provisioning" and first["error"] is None
    worker.tick()
    assert worker.store.get(lease.lease_id).state == "ready"


def test_worker_api_authentication_scope_and_uncertain_data_write(tmp_path, monkeypatch):
    import asyncio
    import httpx
    from evander.resources.worker import create_app

    class Data(Adapter):
        lease_operations = {"inspect", "write"}

        def lease_call(self, lease, operation, arguments):
            if operation == "inspect":
                return {"count": self.writes}
            self.writes += 1
            raise RuntimeError("synthetic-private-password: acknowledgement lost")

    monkeypatch.setenv("TEST_WORKER_TOKEN", "host-private-token")
    settings = WorkerSettings(
        runtime_dir=str(tmp_path),
        worker_id="worker-a",
        token_env="TEST_WORKER_TOKEN",
        allocations_enabled=True,
        connections={"local": {}},
    )
    jobs, adapter = Jobs(), Data()
    service = WorkerService("sample-addon", settings, adapter, jobs=jobs, now=lambda: 1000)
    lease = Lease(
        lease_id="rs-" + "d" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(connection_id="local", worker_id="worker-a"),
        created_at=1000,
        expires_at=8200,
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app(service, periodic=False)),
            base_url="http://worker.test",
        ) as client:
            assert (await client.get("/healthz")).status_code == 401
            client.headers["Authorization"] = "Bearer host-private-token"
            health = await client.get("/healthz")
            assert health.json()["worker_id"] == "worker-a"
            path = "/v1/leases/" + lease.lease_id
            payload = {"addon_id": "foreign-addon", "lease": lease.model_dump()}
            assert (await client.post(path + "/ensure", json=payload)).status_code == 400
            assert service.store.leases() == []
            payload["addon_id"] = "sample-addon"
            ready = await client.post(path + "/ensure", json=payload)
            assert ready.status_code == 200 and ready.json()["state"] == "ready"
            assert (
                "synthetic-private-password" not in ready.text and "resources" not in ready.json()
            )
            expiry = service.store.get(lease.lease_id).expires_at
            assert (await client.get(path + "/get")).json()["state"] == "ready"
            assert service.store.get(lease.lease_id).expires_at == expiry
            call = {
                "addon_id": "sample-addon",
                "owner": owner("foreign").model_dump(),
                "operation": "inspect",
                "arguments": {},
                "request_id": "read",
            }
            assert (await client.post(path + "/call", json=call)).status_code == 400
            call.update(owner=owner().model_dump(), operation="write", request_id="write-once")
            uncertain = await client.post(path + "/call", json=call)
            assert uncertain.json()["state"] == "uncertain" and adapter.writes == 1
            assert "synthetic-private-password" not in uncertain.text
            service.store = Store(tmp_path)
            assert (await client.post(path + "/call", json=call)).json() == uncertain.json()
            assert adapter.writes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_check", [4, 0])
def test_network_withdrawal_keeps_intent_until_actual_rule_absence(
    tmp_path, monkeypatch, failed_check
):
    import subprocess
    from evander.resources.jobs import Jobs as JobBindings

    checks = [failed_check, 0] if failed_check == 0 else [failed_check]

    def run(args, **kwargs):
        code = checks.pop(0) if args[3] == "-C" else 0
        return subprocess.CompletedProcess(args, code, stdout=b"", stderr=b"")

    bindings = JobBindings("sample-addon", tmp_path / "jobs.db", run=run)
    monkeypatch.setattr(bindings, "inspect", lambda _: None)
    store = Store(tmp_path / "journal")
    lease = Lease(
        lease_id="rs-" + "e" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(),
        created_at=1000,
        expires_at=8200,
        bound_job_id="job-a",
        config_path="/tmp/private",
        job_binding={
            "container_id": "job-container",
            "rules": [["-s", "192.0.2.10/32", "-j", "ACCEPT"]],
        },
    )
    store.put(lease)
    with pytest.raises(ResourceError, match="cleanup is incomplete"):
        bindings.unbind(lease, lambda: store.put(lease))
    retained = Store(store.root).get(lease.lease_id)
    assert retained.bound_job_id == "job-a" and retained.job_binding["rules"]
    checks[:] = [1]
    bindings.unbind(retained, lambda: store.put(retained))
    removed = Store(store.root).get(lease.lease_id)
    assert not removed.job_binding and removed.bound_job_id is None and removed.config_path is None


def test_authorized_workflow_data_activity_refreshes_authority_and_idle_deadline(domain):
    clock = [1000]
    domain.now = lambda: clock[0]
    domain.authority.owns = lambda value: value.valid_until is None or clock[0] < value.valid_until
    domain.adapter.lease_operations = {"inspect"}
    domain.adapter.lease_call = lambda *args: {"ok": True}
    initial = Owner(provider="workflow", key="graph:run", directory="root", valid_until=1100)
    lease = domain.acquire(initial, "test", "one")
    clock[0] = 1200
    current = initial.model_copy(update={"valid_until": 2000})
    assert domain.lease_call(current, lease["lease_id"], "inspect", {}, "") == {"ok": True}
    domain.store = Store(domain.store.root)
    retained = domain.store.get(lease["lease_id"])
    assert retained.owner.valid_until == 2000 and retained.expires_at == 8400
    assert domain.reconcile(lease["lease_id"])["state"] == "ready"
    clock[0] = 1300
    assert domain.get(current, lease["lease_id"])["expires_at"] == 8400


@pytest.mark.parametrize("change", ["ended", "expired", "replaced", "unknown"])
def test_worker_withdraws_obsolete_job_access_before_unavailable_provider(
    tmp_path, monkeypatch, change
):
    from evander.resources.jobs import Jobs as JobBindings

    clock = [1000]
    monkeypatch.setattr("evander.resources.jobs.time", SimpleNamespace(time=lambda: clock[0]))
    records = [
        SimpleNamespace(job_id="job-a", status="running", metadata={}, container_id="container-a")
    ]
    actual = JobBindings(
        "sample-addon",
        tmp_path / "jobs.db",
        jobs=SimpleNamespace(list_by_session=lambda _: records),
    )
    monkeypatch.setattr(
        actual,
        "inspect",
        lambda container: {"Id": container, "State": {"Running": True}},
    )
    jobs = Jobs()
    jobs.current = actual.current
    adapter = Adapter()
    service = WorkerService(
        "sample-addon",
        WorkerSettings(
            runtime_dir=str(tmp_path / "resource"),
            worker_id="worker-a",
            token_env="TEST_TOKEN",
            allocations_enabled=True,
            connections={"local": {}},
        ),
        adapter,
        jobs=jobs,
        now=lambda: clock[0],
    )
    lease = Lease(
        lease_id="rs-" + "a" * 32,
        owner=Owner(key="session-a", session_id="session-a", directory="root", valid_until=1100),
        profile_id="test",
        profile=Profile(connection_id="local", worker_id="worker-a"),
        created_at=1000,
        expires_at=8200,
    )
    assert service.ensure(lease)["state"] == "ready"
    assert jobs.bound is not None
    if change == "ended":
        records[0].status = "completed"
    elif change == "expired":
        # The container is still running, but its ownership authority has expired.
        clock[0] = 1101
    elif change == "replaced":
        records[0].job_id, records[0].container_id = "job-b", "container-b"
    else:
        monkeypatch.setattr(actual, "inspect", lambda _: (_ for _ in ()).throw(OSError()))
    provider_attempts = []

    def unavailable(*args):
        provider_attempts.append(True)
        assert jobs.bound is None
        raise OSError("provider offline")

    monkeypatch.setattr(adapter, "provision", unavailable)
    service.store = Store(service.store.root)
    service.tick()
    retained = Store(service.store.root).get(lease.lease_id)
    assert jobs.bound is None and retained.bound_job_id is None and retained.config_path is None
    # No provider I/O is needed merely to discover a missing or expired job.
    assert bool(provider_attempts) == (change == "replaced")


def test_worker_release_cleans_provider_despite_local_cleanup_failure(tmp_path):
    jobs, adapter = Jobs(), Adapter()
    service = WorkerService(
        "sample-addon",
        WorkerSettings(
            runtime_dir=str(tmp_path),
            worker_id="worker-a",
            token_env="TEST_TOKEN",
            allocations_enabled=True,
            connections={"local": {}},
        ),
        adapter,
        jobs=jobs,
        now=lambda: 1000,
    )
    lease = Lease(
        lease_id="rs-" + "b" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(connection_id="local", worker_id="worker-a"),
        created_at=1000,
        expires_at=8200,
    )
    assert service.ensure(lease)["state"] == "ready"
    original = jobs.unbind
    jobs.unbind = lambda *args: (_ for _ in ()).throw(ResourceError("file cleanup failed"))
    lease.operation = {"id": "release-one", "kind": "release", "arguments": {}}
    assert service.ensure(lease)["state"] == "cleanup_failed"
    assert not adapter.resources
    retained = Store(service.store.root).get(lease.lease_id)
    assert retained.operation["id"] == "release-one" and retained.bound_job_id == "job-a"
    jobs.unbind = original
    service.tick()
    final = Store(service.store.root).get(lease.lease_id)
    assert final.state == "released" and final.bound_job_id is None and jobs.bound is None


@pytest.mark.parametrize("failure", ["files", "network", "docker"])
def test_worker_withdrawal_steps_are_independent_and_retryable(tmp_path, monkeypatch, failure):
    import subprocess
    from evander.resources.jobs import Jobs as JobBindings
    from evander.resources.projection import UNPROJECT

    calls, rules = (
        [],
        [["-s", "192.0.2.10/32", "-j", "ACCEPT"], ["-s", "192.0.2.11/32", "-j", "ACCEPT"]],
    )
    existing, broken, attached = list(rules), [True], [True]

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "iptables":
            rule = args[5:]
            if args[3] == "-D":
                existing.remove(rule)
            code = 0 if args[3] == "-D" or rule in existing else 1
        elif args[1:3] == ["network", "disconnect"]:
            code = int(broken[0] and failure == "network")
            if not code:
                attached[0] = False
        else:
            assert args[1] == "exec" and UNPROJECT in args
            code = int(broken[0] and failure == "files")
        return subprocess.CompletedProcess(args, code, stdout=b"", stderr=b"")

    bindings = JobBindings("sample-addon", tmp_path / "jobs.db", run=run)

    def inspect(_):
        if broken[0] and failure == "docker":
            raise ResourceError("Docker unavailable")
        return {
            "State": {"Running": True},
            "NetworkSettings": {"Networks": {"test-net": {}} if attached[0] else {}},
        }

    monkeypatch.setattr(bindings, "inspect", inspect)
    store = Store(tmp_path / "journal")
    lease = Lease(
        lease_id="rs-" + "c" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(),
        created_at=1000,
        expires_at=8200,
        bound_job_id="job-a",
        config_path="/tmp/private",
        job_binding={
            "container_id": "job-container",
            "base": "/tmp/evander-sample-addon",
            "network": "test-net",
            "rules": rules,
        },
    )
    with pytest.raises(ResourceError, match="cleanup is incomplete"):
        bindings.unbind(lease, lambda: store.put(lease))
    retained = Store(store.root).get(lease.lease_id)
    assert not existing and retained.job_binding["rules"] == []
    assert calls[0][0] == "iptables"
    if failure != "docker":
        assert any(UNPROJECT in call for call in calls)
        assert any(call[1:3] == ["network", "disconnect"] for call in calls)
    if failure == "files":
        assert not attached[0]
    broken[0] = False
    bindings.unbind(retained, lambda: store.put(retained))
    final = Store(store.root).get(lease.lease_id)
    assert not attached[0] and final.job_binding == {} and final.bound_job_id is None


def test_stopped_persistent_container_keeps_cleanup_intent_until_removed(tmp_path, monkeypatch):
    import subprocess
    from evander.resources.jobs import Jobs as JobBindings

    commands = []

    def run(args, **kwargs):
        commands.append(args)
        assert args[0] == "iptables"  # A stopped container cannot execute file cleanup.
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")

    bindings = JobBindings("sample-addon", tmp_path / "jobs.db", run=run)
    container = {
        "State": {"Running": False},
        "HostConfig": {"Tmpfs": None},
        "NetworkSettings": {"Networks": {}},
    }
    monkeypatch.setattr(bindings, "inspect", lambda _: container)
    store = Store(tmp_path / "journal")
    lease = Lease(
        lease_id="rs-" + "f" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(),
        created_at=1000,
        expires_at=8200,
        bound_job_id="job-a",
        config_path="/tmp/evander-sample-addon/private/connection.json",
        job_binding={
            "container_id": "retained-container",
            "base": "/tmp/evander-sample-addon",
            "rules": [["-s", "192.0.2.10/32", "-j", "ACCEPT"]],
        },
    )
    with pytest.raises(ResourceError, match="cleanup is incomplete"):
        bindings.unbind(lease, lambda: store.put(lease))
    retained = Store(store.root).get(lease.lease_id)
    assert commands and retained.job_binding["rules"] == []
    assert retained.config_path and retained.bound_job_id == "job-a"
    monkeypatch.setattr(bindings, "inspect", lambda _: None)
    bindings.unbind(retained, lambda: store.put(retained))
    assert Store(store.root).get(lease.lease_id).job_binding == {}


@pytest.mark.parametrize("nested_mount", [False, True])
def test_private_delivery_rejects_persistent_storage_before_writing(tmp_path, nested_mount):
    from evander.resources.jobs import Jobs as JobBindings

    def unexpected(*args, **kwargs):
        pytest.fail("Credentials must not be written to persistent container storage")

    inspected = {
        "HostConfig": {"Tmpfs": {"/run/evander-resources": "rw"} if nested_mount else None},
        "Mounts": [{"Type": "bind", "Destination": "/run/evander-resources/sample-addon"}]
        if nested_mount
        else [],
    }
    lease = Lease(
        lease_id="rs-" + "f" * 32,
        owner=owner(),
        profile_id="p",
        profile=Profile(),
        created_at=1000,
        expires_at=8200,
    )
    bindings = JobBindings("sample-addon", tmp_path / "jobs.db", run=unexpected)
    with pytest.raises(ResourceError, match="private tmpfs"):
        bindings.bind(
            lease,
            (SimpleNamespace(job_id="job-a", container_id="container-a"), inspected),
            {"app": {"password": "private"}},
            lambda: None,
        )
    assert not lease.job_binding and lease.config_path is None


def test_cleanup_ignores_job_executables_python_path_and_working_directory(tmp_path, monkeypatch):
    import os
    import shlex
    import subprocess
    from evander.resources.jobs import Jobs as JobBindings

    home = tmp_path / "agent"
    binary = home / "bin"
    binary.mkdir(parents=True)
    marker = home / "hijacked"
    poisoned = binary / "python3"
    poisoned.write_text("#!/bin/sh\nprintf owned > " + shlex.quote(str(marker)) + "\n")
    poisoned.chmod(0o700)
    (home / "sitecustomize.py").write_text(
        "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('owned')\n"
    )
    base = tmp_path / "private"
    lease_id = "rs-" + "c" * 32
    directory = base / lease_id
    directory.mkdir(parents=True, mode=0o711)
    base.chmod(0o711)
    configuration = directory / "connection.json"
    configuration.write_text("synthetic-private-credential")
    configuration.chmod(0o600)

    def run(args, **kwargs):
        assert args[:2] == ["docker", "exec"] and "0:0" in args
        env = {**os.environ, "PATH": str(binary) + ":/usr/bin:/bin", "PYTHONPATH": str(home)}
        for index, argument in enumerate(args):
            if argument == "--env":
                key, value = args[index + 1].split("=", 1)
                env[key] = value
        # Execute the real helper program in the job's poisoned environment,
        # using this test UID and private tmpdir instead of requiring host root.
        return subprocess.run(args[args.index("job-container") + 1 :], env=env, cwd=home, **kwargs)

    bindings = JobBindings("sample-addon", tmp_path / "jobs.db", run=run)
    monkeypatch.setattr(
        bindings,
        "inspect",
        lambda _: {"State": {"Running": True}, "NetworkSettings": {"Networks": {}}},
    )
    store = Store(tmp_path / "journal")
    lease = Lease(
        lease_id=lease_id,
        owner=owner(),
        profile_id="p",
        profile=Profile(),
        created_at=1000,
        expires_at=8200,
        bound_job_id="job-a",
        config_path=str(configuration),
        job_binding={"container_id": "job-container", "base": str(base), "rules": []},
    )
    bindings.unbind(lease, lambda: store.put(lease))
    assert not marker.exists() and not configuration.exists()
    assert Store(store.root).get(lease_id).job_binding == {}


def test_resource_query_uses_factory_trace_through_real_workflow_executor(domain):
    from evander.events.models import WorkflowNodeEnvelope
    from evander.provider_config import QueryProviderConfig
    from evander.queries.registry import QueryProviderFactory, QueryProviderRegistry
    from evander.resources.providers import register_providers
    from evander.workflow.engine.node_executors import ProviderQueryNodeExecutor

    revision, settings = domain.store.settings("root")
    settings.workflow_operations = ["discover", "inspect", "get"]
    domain.store.save_settings("root", revision, settings, "operator")
    registration = register_providers(lambda _: domain, "sample", {"inspect"}, {"write"})
    executor = ProviderQueryNodeExecutor(
        QueryProviderFactory(
            provider_config_resolver=SimpleNamespace(
                resolve_query_provider_config=lambda _: QueryProviderConfig(
                    runtime_dir=domain.context.runtime_dir
                )
            ),
            registry=QueryProviderRegistry(registration.query_providers),
        )
    )
    discovered = []
    domain.discover = lambda owner: discovered.append(owner) or {"profile_ids": ["test"]}
    query = WorkflowNodeEnvelope(
        id="read", display_name="Read", type="query:sample:read", config={}
    )
    graph = {"graph_id": "graph-one", "directory_path": "root"}
    assert executor.execute(
        query=query, graph_metadata=graph, execution_context={"workflow_run_id": "run-one"}
    ) == {"result": {"profile_ids": ["test"]}}
    assert discovered[0].key == "graph-one:run-one" and discovered[0].directory == "root"
    # Inspect uses the same trusted metadata even though execute receives only config.
    seen = []
    domain.call = lambda owner, *args: seen.append(owner) or {"ok": True}
    assert executor.execute(
        query=query,
        resolved_config={"operation": "inspect", "connection_id": "local"},
        graph_metadata=graph,
        execution_context={"trace_id": "run-two"},
    ) == {"result": {"ok": True}}
    assert seen[0].key == "graph-one:run-two"
    with pytest.raises(ResourceError, match="trusted"):
        executor.execute(
            query=query,
            graph_metadata=graph,
            resolved_config={"arguments": {"trace_id": "untrusted"}},
        )
    # A known lease owned by another run must still be rejected at the domain boundary.
    lease = domain.acquire(
        Owner(provider="workflow", key="other:run", directory="root"), "test", "other"
    )
    with pytest.raises(ResourceError, match="scope"):
        executor.execute(
            query=query,
            resolved_config={"operation": "get", "arguments": {"lease_id": lease["lease_id"]}},
            graph_metadata=graph,
            execution_context={"trace_id": "run-one"},
        )
