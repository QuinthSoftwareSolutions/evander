import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest
from evander.config import GatewayConfig
from evander.models import SessionManifest
from evander.resources.endpoints import Endpoint, Endpoints, ProxyGrants
from evander.resources.models import Owner, ResourceError
from evander.resources.workloads import Workloads, Workload
from evander.resources.jobs import Jobs
from evander.relays.http_target_browser_proxy import HttpTargetBrowserProxy, ProxyRequestError


def test_endpoint_owner_placement_readiness_and_revocation(tmp_path):
    now = [10.0]
    store = Endpoints(tmp_path, now=lambda: now[0])
    endpoint = Endpoint(
        ref="ep-" + "a" * 32,
        resource_id="application-one",
        session_id="agent-one",
        directory="root/example",
        worker_id="worker-one",
        origin="http://application:4200",
        port=4200,
        expires_at=20,
    )
    store.put(endpoint)
    owner = dict(session_id="agent-one", directory="root/example", worker_id="worker-one")
    with pytest.raises(ResourceError, match="unavailable"):
        store.resolve(endpoint.ref, **owner)
    endpoint.ready = True
    store.put(endpoint)
    assert store.resolve(endpoint.ref, **owner).resource_id == "application-one"
    for changed in (
        {"session_id": "agent-two"},
        {"directory": "root/other"},
        {"worker_id": "worker-two"},
    ):
        with pytest.raises(ResourceError):
            store.resolve(endpoint.ref, **{**owner, **changed})
    with pytest.raises(ResourceError, match="identity"):
        store.put(endpoint.model_copy(update={"origin": "http://foreign:4200"}))
    now[0] = 20
    with pytest.raises(ResourceError, match="unavailable"):
        store.resolve(endpoint.ref, **owner)
    now[0] = 11
    store.revoke(endpoint.resource_id)
    with pytest.raises(ResourceError, match="unavailable"):
        store.resolve(endpoint.ref, **owner)


def test_delegated_proxy_never_broadens_parent_grants_and_revokes(tmp_path):
    session = SessionManifest(
        session_id="agent-one",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={
            "http_targets": {
                "targets": {
                    "app": {"base_url": "https://app.example.test"},
                    "other": {"base_url": "https://other.example.test"},
                }
            }
        },
    )
    resolver = SimpleNamespace(
        get_session_manifest=lambda id: (
            session if id == session.session_id else (_ for _ in ()).throw(ValueError())
        )
    )
    grants = ProxyGrants(tmp_path)
    token = grants.issue(
        "browser-one", session.session_id, ["app", "removed"], session.expires_at.timestamp()
    )
    proxy = HttpTargetBrowserProxy(
        GatewayConfig(runtime_dir=tmp_path), provider_config_resolver=resolver
    )
    auth = "Basic " + base64.b64encode(("proxy~browser-one:" + token).encode()).decode()
    scoped = proxy._authenticate(auth)
    assert set(scoped.http_targets_metadata().targets) == {"app"}
    proxy._authorize_origin(scoped, scheme="https", host="app.example.test", port=443)
    with pytest.raises(ProxyRequestError):
        proxy._authorize_origin(scoped, scheme="https", host="other.example.test", port=443)
    grants.revoke("browser-one")
    with pytest.raises(ProxyRequestError):
        proxy._authenticate(auth)
    assert token not in grants.path.read_bytes().decode(errors="ignore")


def test_application_workload_binding_does_not_require_an_agent_job(tmp_path, monkeypatch):
    owner = Owner(
        provider="synthetic-workload-provider",
        reference={"workload_id": "environment-one"},
        key="environment-one",
        session_id="agent-one",
        directory="root/example",
        valid_until=datetime.now(UTC).timestamp() + 100,
    )
    workloads = Workloads(tmp_path / "managed-workloads.db")
    workloads.put(
        Workload(
            id=owner.key,
            owner=owner,
            worker_id="worker-one",
            container_id="container-one",
            expires_at=owner.valid_until,
        )
    )
    jobs = Jobs("synthetic-addon", tmp_path / "worker-jobs.db")
    container = {
        "Id": "container-one",
        "State": {"Running": True},
        "Config": {"Labels": {"evander.resource": owner.key}},
    }
    monkeypatch.setattr(jobs, "inspect", lambda id: container)
    assert jobs.current(owner)[0].job_id == owner.key
    container["Config"]["Labels"] = {}
    with pytest.raises(ResourceError, match="identity"):
        jobs.current(owner)
    workloads.revoke(owner)
    assert jobs.current(owner) is None


def test_managed_workload_hard_deadline_releases_even_when_idle_lease_was_refreshed(tmp_path):
    from evander.resources.models import Lease, Profile, WorkerSettings
    from evander.resources.worker import WorkerService

    calls = []
    owner = Owner(
        provider="synthetic-workload-provider",
        key="environment-one",
        reference={"workload_id": "environment-one"},
        directory="root/example",
        session_id="agent-one",
        valid_until=20,
    )
    lease = Lease(
        lease_id="rs-" + "b" * 32,
        owner=owner,
        profile_id="synthetic",
        profile=Profile(),
        created_at=10,
        expires_at=1000,
        state="ready",
    )
    adapter = SimpleNamespace(release=lambda lease, save, credentials: calls.append(lease.lease_id))
    jobs = SimpleNamespace(current=lambda owner: None, unbind=lambda lease, save: None)
    settings = WorkerSettings(
        runtime_dir=str(tmp_path),
        worker_id="worker-one",
        worker_jobs_db=str(tmp_path / "jobs.db"),
        token_env="SYNTHETIC_RESOURCE_TOKEN",
    )
    service = WorkerService("synthetic-addon", settings, adapter, jobs=jobs, now=lambda: 21)
    service.store.put(lease)
    assert service._reconcile(lease)["state"] == "released"
    assert calls == [lease.lease_id]


def test_delegated_login_is_scoped_revocable_and_uses_parent_identity(tmp_path):
    import asyncio
    from evander.website_sessions import (
        ManagedWebsiteSessionProfile,
        ManagedWebsiteSessionService,
        WebsiteSessionError,
    )

    now = datetime.now(UTC).timestamp()
    profile = ManagedWebsiteSessionProfile(
        adapter="synthetic",
        browser_origin="https://app.example.test",
        login_url="https://app.example.test/login",
    )
    session = SessionManifest(
        session_id="agent-parent",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={
            "http_targets": {
                "targets": {
                    "app": {"base_url": profile.browser_origin, "managed_website_session": "login"},
                    "other": {
                        "base_url": profile.browser_origin,
                        "managed_website_session": "login",
                    },
                }
            }
        },
    )

    def parent(id):
        if id != session.session_id:
            raise ValueError("Unknown owner")
        return session

    resolver = SimpleNamespace(
        config=SimpleNamespace(runtime_dir=tmp_path),
        get_session_manifest=parent,
        resolve_for_session_manifest=lambda session: SimpleNamespace(
            managed_website_sessions={"login": profile}
        ),
    )
    calls = []

    class Adapter:
        def grant_context(self, owner, profile):
            return {"owner": owner.session_id}

        async def login(self, id, profile, grant, expiry):
            assert parent(id).session_id == grant["owner"]
            calls.append(id)
            return {
                "origin": profile.browser_origin,
                "storageState": {"cookies": [], "origins": []},
                "sessionStorage": {},
                "expiresAt": now + 600,
            }

    service = ManagedWebsiteSessionService(resolver, adapters={"synthetic": Adapter()})
    grants = ProxyGrants(tmp_path)
    token = grants.issue("browser-one", session.session_id, ["app"], session.expires_at.timestamp())
    assert (
        asyncio.run(service.acquire("proxy~browser-one", "app", token))["origin"]
        == profile.browser_origin
    )
    assert calls == [session.session_id]
    with pytest.raises(WebsiteSessionError):
        asyncio.run(service.acquire("proxy~browser-one", "other", token))
    with pytest.raises(WebsiteSessionError):
        asyncio.run(service.acquire(session.session_id, "app", token))
    grants.revoke("browser-one")
    with pytest.raises(WebsiteSessionError):
        asyncio.run(service.acquire("proxy~browser-one", "app", token))
    assert calls == [session.session_id]
