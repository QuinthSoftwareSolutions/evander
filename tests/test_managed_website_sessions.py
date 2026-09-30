from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
import json
import ipaddress
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import httpx
import httpcore
import pytest
from pydantic import ValidationError

from evander.config import GatewayConfig
from evander.admin.services.session_helpers import normalize_http_targets_metadata
from evander.http_transport import _PinnedAddressAsyncHTTPTransport
from evander.directory_config import validate_effective_domain_payload
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryStore, DirectoryService
from evander.provider_config import ProviderConfigResolver
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.models import SessionManifest
from evander.relays.http_target import HttpTargetRelay
from evander.session_auth import session_mcp_auth_metadata
from evander.website_sessions import (
    ManagedWebsiteSessionProfile, ManagedWebsiteSessionService, WebsiteSessionError,
    WebsiteStorageBinding,
    _public_transport,
)


def jwt(expiry):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def environment_grant(profile, *, directory_path="root", profile_id="demo"):
    return {
        "directory_path": directory_path,
        "profile_id": profile_id,
        "browser_origin": profile.browser_origin,
        "login_url": profile.login_url,
        "credential_fields": profile.credential_fields,
        "public_credential_fields": sorted(profile.public_credential_fields),
        "static_fields": profile.static_fields,
    }


def environment_grants(profile, *, directory_path="root", profile_id="demo"):
    return json.dumps([environment_grant(
        profile, directory_path=directory_path, profile_id=profile_id,
    )])


@pytest.fixture
def rig(monkeypatch):
    now = [datetime.now(UTC).timestamp()]
    monkeypatch.setenv("WEBSITE_USER", "operator@example.test")
    monkeypatch.setenv("WEBSITE_PASSWORD", 'host-only-"secret\\password')
    profile = ManagedWebsiteSessionProfile(
        browser_origin="https://app.example.test:8443",
        login_url="https://api.example.test/auth",
        credential_fields={"email": "WEBSITE_USER", "password": "WEBSITE_PASSWORD"},
        public_credential_fields={"email"},
        local_storage={"currentUser": {"response_pointer": ""}},
        jwt_response_pointer="/token", max_age_seconds=86400,
    )
    monkeypatch.setenv(
        "EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST", environment_grants(profile),
    )
    config = GatewayConfig(managed_website_sessions={"demo": profile})
    session = SessionManifest(
        session_id="s1", allowed_tools=[], expires_at=datetime.now(UTC) + timedelta(days=3),
        metadata={"mcp_session_auth": session_mcp_auth_metadata("proof"), "http_targets": {"targets": {
            "app": {"base_url": profile.browser_origin, "managed_website_session": "demo"},
            "api": {"base_url": "https://api.example.test"},
        }}},
    )
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"email": "operator@example.test", "token": jwt(now[0] + 8 * 3600)})

    state = SimpleNamespace(now=now, profile=profile, config=config, session=session, calls=calls, handler=handler)
    def get_session(sid):
        if sid != state.session.session_id:
            raise ValueError("unknown")
        return state.session
    state.resolver = SimpleNamespace(
        get_session_manifest=get_session,
        resolve_for_session_manifest=lambda session: state.config,
    )
    state.service = ManagedWebsiteSessionService(
        state.resolver, clock=lambda: now[0],
        transport_factory=lambda url: httpx.MockTransport(lambda request: state.handler(request)),
    )
    return state


def acquire(rig, **kwargs):
    return asyncio.run(rig.service.acquire(kwargs.get("sid", "s1"), kwargs.get("alias", "app"), kwargs.get("proof", "proof")))


def test_eight_hour_expiry_renews_before_next_browser_context(rig):
    first = acquire(rig)
    assert first == acquire(rig)
    assert len(rig.calls) == 1
    rig.now[0] += 8 * 3600 + 1
    second = acquire(rig)
    assert len(rig.calls) == 2
    assert second["expiresAt"] > first["expiresAt"]
    assert second["storageState"] != first["storageState"]
    assert "password" not in json.dumps(second)
    assert rig.calls[0].headers.get("x-evander-mcp-session-token") is None


def test_json_login_preserves_utf8_storage_limit(rig):
    rig.profile.local_storage = {
        name: WebsiteStorageBinding(response_pointer="/payload", encoding="string")
        for name in ("primary", "secondary")
    }
    rig.profile.jwt_response_pointer = None
    payload = "é" * 100_000
    rig.handler = lambda request: httpx.Response(200, json={"payload": payload})
    result = acquire(rig)
    stored = result["storageState"]["origins"][0]["localStorage"]
    assert {item["name"] for item in stored} == {"primary", "secondary"}
    assert all(item["value"] == payload for item in stored)


def test_json_login_does_not_import_unrelated_addons(rig):
    from evander.addons import AddonLoaderConfig

    rig.resolver.config = SimpleNamespace(
        addon_loader=AddonLoaderConfig(modules=["synthetic_missing_optional_addon"]),
        runtime_dir=Path("/tmp/synthetic-runtime"),
    )
    rig.service = ManagedWebsiteSessionService(
        rig.resolver, clock=lambda: rig.now[0],
        transport_factory=lambda url: httpx.MockTransport(lambda request: rig.handler(request)),
    )
    assert acquire(rig)["origin"] == "https://app.example.test:8443"


def test_renewal_margin_and_concurrent_callers_share_login(rig):
    async def run():
        first = await rig.service.acquire("s1", "app", "proof")
        rig.now[0] = first["expiresAt"] - 30
        states = await asyncio.gather(*(rig.service.acquire("s1", "app", "proof") for _ in range(8)))
        assert all(state == states[0] for state in states)
        assert states[0]["expiresAt"] > first["expiresAt"]
    asyncio.run(run())
    assert len(rig.calls) == 2


def test_different_site_payload_storage_and_expiry_format(rig, monkeypatch):
    rig.config.managed_website_sessions["demo"] = ManagedWebsiteSessionProfile(
        browser_origin=rig.profile.browser_origin, login_url=rig.profile.login_url,
        credential_fields={"login": "WEBSITE_USER", "passcode": "WEBSITE_PASSWORD"},
        static_fields={"tenant": "training", "remember": False},
        session_storage={"access": {"response_pointer": "/result/access~1key", "encoding": "string"}},
        expires_in_response_pointer="/lifetime", max_age_seconds=1800,
    )
    monkeypatch.setenv(
        "EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST",
        environment_grants(rig.config.managed_website_sessions["demo"]),
    )
    def handler(request):
        payload = json.loads(request.content)
        assert set(payload) == {"login", "passcode", "tenant", "remember"}
        return httpx.Response(200, json={"result": {"access/key": "temporary-token"}, "lifetime": 900})
    rig.handler = handler
    state = acquire(rig)
    assert state["sessionStorage"] == {"access": "temporary-token"}
    assert state["storageState"]["origins"][0]["localStorage"] == []
    assert state["expiresAt"] == rig.now[0] + 900


@pytest.mark.parametrize("change", ["unbound", "wrong_origin", "login_removed", "profile_removed", "expired", "token_removed"])
def test_current_grants_are_enforced_even_with_cached_authentication(rig, change):
    acquire(rig)
    targets = rig.session.metadata["http_targets"]["targets"]
    if change == "unbound":
        targets["app"].pop("managed_website_session")
    elif change == "wrong_origin":
        targets["app"]["base_url"] = "https://elsewhere.example.test"
    elif change == "login_removed":
        targets.pop("api")
    elif change == "profile_removed":
        rig.config.managed_website_sessions.clear()
    elif change == "expired":
        rig.now[0] = rig.session.expires_at.timestamp() + 1
    else:
        rig.session.metadata.pop("mcp_session_auth")
    with pytest.raises(WebsiteSessionError) as exc:
        acquire(rig)
    assert exc.value.status == 403
    assert len(rig.calls) == 1


@pytest.mark.parametrize("kwargs", [{"sid": "other"}, {"alias": "api"}, {"proof": None}, {"proof": "wrong"}])
def test_no_implicit_auth_grant_or_legacy_tokenless_access(rig, kwargs):
    with pytest.raises(WebsiteSessionError) as exc:
        acquire(rig, **kwargs)
    assert exc.value.status == 403
    assert not rig.calls


def test_revocation_during_login_cannot_deliver_state(rig):
    original = rig.handler
    def handler(request):
        response = original(request)
        rig.session.metadata["http_targets"]["targets"]["app"].pop("managed_website_session")
        return response
    rig.handler = handler
    with pytest.raises(WebsiteSessionError):
        acquire(rig)
    assert not rig.service._cache


def test_credentials_are_required_and_rotation_invalidates_cache(rig, monkeypatch):
    acquire(rig)
    monkeypatch.setenv("WEBSITE_PASSWORD", "rotated-host-secret")
    acquire(rig)
    assert len(rig.calls) == 2
    assert json.loads(rig.calls[-1].content)["password"] == "rotated-host-secret"
    monkeypatch.delenv("WEBSITE_PASSWORD")
    with pytest.raises(WebsiteSessionError, match="website_credentials_unavailable"):
        acquire(rig)
    assert len(rig.calls) == 2


def test_environment_credentials_require_explicit_host_grant(rig, monkeypatch):
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-secret")
    rig.profile.credential_fields = {"password": "EVANDER_ADMIN_WEB_PASSWORD"}
    with pytest.raises(WebsiteSessionError, match="website_credentials_unavailable"):
        acquire(rig)
    assert not rig.calls


def test_environment_credential_grant_is_bound_to_scope_profile_and_destination(rig, monkeypatch):
    monkeypatch.setenv(
        "EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST",
        environment_grants(rig.profile, directory_path="root/alice"),
    )
    assert rig.service.credentials("root/alice/child", "demo", rig.profile)["password"]

    attempts = [
        ("root/bob", "demo", rig.profile),
        ("root/alice", "other", rig.profile),
        ("root/alice", "demo", rig.profile.model_copy(
            update={"login_url": "https://attacker.example.test/capture"},
        )),
        ("root/alice", "demo", rig.profile.model_copy(
            update={"browser_origin": "https://attacker.example.test"},
        )),
        ("root/alice", "demo", rig.profile.model_copy(
            update={"public_credential_fields": {"email", "password"}},
        )),
        ("root/alice", "demo", rig.profile.model_copy(
            update={"static_fields": {"tenant": "attacker"}},
        )),
    ]
    for scope, profile_id, candidate in attempts:
        with pytest.raises(WebsiteSessionError, match="website_credentials_unavailable"):
            rig.service.credentials(scope, profile_id, candidate)


@pytest.mark.parametrize(
    "value", ["WEBSITE_USER,WEBSITE_PASSWORD", "{}", "[null]"],
)
def test_malformed_environment_credential_grants_fail_closed(rig, monkeypatch, value):
    monkeypatch.setenv("EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST", value)
    with pytest.raises(WebsiteSessionError, match="website_credentials_unavailable"):
        rig.service.credentials("root", "demo", rig.profile)


def test_environment_credential_grant_rejects_non_string_directory(rig, monkeypatch):
    grant = environment_grant(rig.profile)
    grant["directory_path"] = 1
    monkeypatch.setenv(
        "EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST", json.dumps([grant]),
    )
    with pytest.raises(WebsiteSessionError, match="website_credentials_unavailable"):
        rig.service.credentials("root", "demo", rig.profile)


@pytest.mark.parametrize("case", ["401", "302", "oversized", "invalid_json", "expired", "nan", "timeout", "secret_reflected"])
def test_failures_are_sanitized_and_never_return_stale_state(rig, case, monkeypatch):
    acquire(rig)
    rig.now[0] += 8 * 3600
    secret = 'host-only-"secret\\password'
    def handler(request):
        rig.calls.append(request)
        if case in {"401", "302"}:
            return httpx.Response(int(case), text=secret, headers={"location": "https://evil.test"})
        if case == "oversized":
            return httpx.Response(200, content=b"x" * 300000)
        if case == "invalid_json":
            return httpx.Response(200, text=secret)
        if case == "timeout":
            raise httpx.ReadTimeout(secret)
        return httpx.Response(200, json={"token": jwt("NaN" if case == "nan" else
                                                     rig.now[0] + (3600 if case == "secret_reflected" else -1)),
                                         "nested": [{"password": secret}] if case == "secret_reflected" else []})
    rig.handler = handler
    with pytest.raises(WebsiteSessionError) as exc:
        acquire(rig)
    assert secret not in str(exc.value)
    assert "http" not in str(exc.value)
    assert not rig.service._cache
    with pytest.raises(WebsiteSessionError, match="website_login_retry_later"):
        acquire(rig)
    assert len(rig.calls) == 2
    rig.now[0] += 61
    with pytest.raises(WebsiteSessionError):
        acquire(rig)
    assert len(rig.calls) == 3


@pytest.mark.parametrize("secret", ['host-"password', "host-\\password", "host-\npassword"])
@pytest.mark.parametrize("location", ["value", "key", "nested"])
def test_cookie_export_rejects_decoded_credentials(rig, monkeypatch, secret, location):
    monkeypatch.setenv("WEBSITE_PASSWORD", secret)
    rig.profile.local_storage = {}
    rig.profile.cookies_response_pointer = "/cookies"
    cookie = {"name": "temporary", "value": "session-token", "domain": "app.example.test",
              "path": "/", "secure": True, "sameSite": "Lax"}
    if location == "value":
        cookie["value"] = "prefix:" + secret + ":suffix"
    elif location == "key":
        cookie[secret] = "attribute"
    else:
        cookie["extra"] = [{"attribute": secret}]
    rig.handler = lambda request: httpx.Response(
        200, json={"token": jwt(rig.now[0] + 3600), "cookies": [cookie]})
    with pytest.raises(WebsiteSessionError, match="website_login_unsafe_response"):
        acquire(rig)
    assert not rig.service._cache


def test_cookie_export_accepts_unrelated_temporary_authentication(rig):
    rig.profile.cookies_response_pointer = "/cookies"
    cookie = {"name": "temporary", "value": "session-token", "domain": "app.example.test",
              "path": "/", "secure": True, "sameSite": "Lax"}
    rig.handler = lambda request: httpx.Response(
        200, json={"token": jwt(rig.now[0] + 3600), "cookies": [cookie]})
    state = acquire(rig)
    assert state["storageState"]["cookies"][0]["value"] == "session-token"


@pytest.mark.parametrize("encoding", ["base64", "url_base64", "percent", "form", "json", "basic", "percent_basic", "form_basic", "jwt", "nested_basic", "base64_boundary"])
@pytest.mark.parametrize("location", ["cookie", "local_storage", "session_storage"])
def test_broker_rejects_encoded_host_credentials(rig, monkeypatch, encoding, location):
    secret = 'private-host-"password\\with spaces\nü'
    monkeypatch.setenv("WEBSITE_PASSWORD", secret)
    reflected = {
        "base64": base64.b64encode(secret.encode()).decode(),
        "url_base64": base64.urlsafe_b64encode(secret.encode()).decode().rstrip("="),
        "percent": quote(secret, safe=""),
        "form": quote_plus(secret, safe=""),
        "json": json.dumps(secret)[1:-1],
        "basic": "Basic " + base64.b64encode(("unrelated-user:" + secret).encode()).decode(),
        "percent_basic": quote("Basic " + base64.b64encode(("u:" + secret).encode()).decode(), safe=""),
        "form_basic": quote_plus("Basic " + base64.b64encode(("u:" + secret).encode()).decode(), safe=""),
        "jwt": "header." + base64.urlsafe_b64encode(json.dumps({"persistent": secret}).encode()).decode().rstrip("=") + ".signature",
        "nested_basic": base64.b64encode(json.dumps({"header": "Basic " + base64.b64encode(("u:" + secret).encode()).decode()}).encode()).decode(),
        "base64_boundary": base64.b64encode(("x" * 49140 + secret).encode()).decode(),
    }[encoding]
    rig.profile.local_storage = {}
    rig.profile.cookies_response_pointer = "/cookies"
    cookie = {"name": "temporary", "value": "temporary-session-token",
              "domain": "app.example.test", "path": "/", "secure": True, "sameSite": "Lax"}
    if location == "cookie":
        cookie["value"] = "prefix:" + reflected
    else:
        getattr(rig.profile, location)["private"] = WebsiteStorageBinding(
            response_pointer="/reflected", encoding="string")
    rig.handler = lambda request: httpx.Response(200, json={
        "token": jwt(rig.now[0] + 3600), "cookies": [cookie], "reflected": reflected})
    app = HttpTargetRelay(rig.config, provider_config_resolver=rig.resolver,
                          website_sessions=rig.service).create_app()

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://relay.test") as client:
            response = await client.post("/session/s1/website-session/app",
                                        headers={"x-evander-mcp-session-token": "proof"})
            assert response.status_code == 503
            assert response.json()["error"] == "website_login_unsafe_response"
            assert reflected not in response.text
            assert not rig.service._cache

    asyncio.run(exercise())


@pytest.mark.parametrize("secret, reflected", [
    ("123456789", 123456789), ("true", True), ("null", None),
    ("123456789", "header." + base64.urlsafe_b64encode(b'{"credential":1.23456789e8}').decode().rstrip("=") + ".signature"),
])
def test_cookie_export_preserves_serialized_credential_rejection(rig, monkeypatch, secret, reflected):
    monkeypatch.setenv("WEBSITE_PASSWORD", secret)
    rig.profile.local_storage = {}
    rig.profile.cookies_response_pointer = "/cookies"
    cookie = {"name": "temporary", "value": "session-token", "domain": "app.example.test",
              "path": "/", "secure": True, "sameSite": "Lax", "extra": [{"reflected": reflected}]}
    rig.handler = lambda request: httpx.Response(
        200, json={"token": jwt(rig.now[0] + 3600), "cookies": [cookie]})
    with pytest.raises(WebsiteSessionError, match="website_login_unsafe_response"):
        acquire(rig)
    assert not rig.service._cache


def test_cookie_export_preserves_rejection_of_public_login_identifiers(rig):
    rig.profile.local_storage = {}
    rig.profile.cookies_response_pointer = "/cookies"
    cookie = {"name": "temporary", "value": "operator@example.test", "domain": "app.example.test",
              "path": "/", "secure": True, "sameSite": "Lax"}
    rig.handler = lambda request: httpx.Response(
        200, json={"token": jwt(rig.now[0] + 3600), "cookies": [cookie]})
    with pytest.raises(WebsiteSessionError, match="website_login_unsafe_response"):
        acquire(rig)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.2.0.4", "169.254.169.254", "100.100.100.200", "::ffff:127.0.0.1", "::ffff:224.0.0.1"])
def test_login_destination_cannot_resolve_to_private_or_metadata_hosts(monkeypatch, address):
    monkeypatch.setattr("evander.website_sessions.socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, (address, 443))])
    with pytest.raises(WebsiteSessionError, match="website_destination_blocked"):
        asyncio.run(_public_transport("https://app.example.test/login"))


@pytest.mark.parametrize("patch", [
    {"login_url": "http://example.test/auth"}, {"login_url": "https://user:password@example.test/auth"},
    {"login_url": "https://example.test/auth?password=secret"}, {"browser_origin": "https://example.test/path"},
    {"credential_fields": {"password": "literal secret"}}, {"static_fields": {"password": "override"}},
    {"jwt_response_pointer": "token"}, {"expires_in_response_pointer": "/expiry"},
    {"renew_before_seconds": 3600, "max_age_seconds": 3600},
])
def test_profile_rejects_ambiguous_or_unsafe_configuration(rig, patch):
    payload = rig.profile.model_dump(mode="json") | patch
    with pytest.raises(ValidationError):
        ManagedWebsiteSessionProfile.model_validate(payload)


def test_provider_config_and_target_binding_roundtrip(rig):
    payload = {"managed_website_sessions": {"demo": rig.profile.model_dump(mode="json")}}
    validated = validate_effective_domain_payload("providers", payload)
    assert "managed_website_sessions" in validated
    restored = SessionManifest.model_validate(rig.session.model_dump(mode="json"))
    assert restored.http_target("app").managed_website_session == "demo"


def test_profile_visibility_follows_directory_inheritance_not_sibling_scopes(rig, tmp_path):
    config = GatewayConfig(runtime_dir=tmp_path, session_store_path=tmp_path / "sessions.json")
    directories = DirectoryStore(config.directories_store_path)
    service = DirectoryService(directories)
    service.create(parent="root", name="alice")
    service.create(parent="root", name="bob")
    overlays = DirectoryConfigStore(directories, runtime_dir=tmp_path)
    overlays.write("root/alice", domain="providers", payload={
        "managed_website_sessions": {"demo": rig.profile.model_dump(mode="json")},
    })
    sessions = JsonSessionManifestRepository(config.session_store_path)
    rig.session.directory_path = "root/alice"
    sessions.upsert_session(rig.session)
    resolver = ProviderConfigResolver(config, directory_store=directories, session_store=sessions)
    assert "demo" in resolver.resolve_for_session_manifest(rig.session).managed_website_sessions
    rig.session.directory_path = "root/bob"
    sessions.upsert_session(rig.session)
    broker = ManagedWebsiteSessionService(resolver, transport_factory=lambda url: httpx.MockTransport(rig.handler))
    with pytest.raises(WebsiteSessionError, match="website_session_not_granted"):
        asyncio.run(broker.acquire("s1", "app", "proof"))
    assert not rig.calls


def test_broker_route_requires_header_proof_and_disables_caching(rig):
    app = HttpTargetRelay(rig.config, provider_config_resolver=rig.resolver, website_sessions=rig.service).create_app()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay.test") as client:
            path = "/session/s1/website-session/app"
            response = await client.post(path + "?mcp_session_token=proof")
            assert response.status_code == 403
            assert response.headers["cache-control"] == "no-store"
            response = await client.post(path, headers={"x-evander-mcp-session-token": "proof"})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert "access-control-allow-origin" not in response.headers
            assert "password" not in response.text
    asyncio.run(run())


def test_unbound_target_serialization_omits_grant_and_preserves_metadata():
    targets = {"targets": {
        "plain": {"base_url": "https://app.example.test", "metadata": {"nullable": None}},
        "bound": {"base_url": "https://app.example.test", "managed_website_session": "demo", "metadata": {}},
    }}
    assert normalize_http_targets_metadata(targets) == targets
    targets["targets"]["bound"]["managed_website_session"] = None
    assert "managed_website_session" not in normalize_http_targets_metadata(targets)["targets"]["bound"]


def test_trickled_response_hits_total_deadline_and_closes_stream(rig):
    class Trickle(httpx.AsyncByteStream):
        closed = False
        chunks = 0
        async def __aiter__(self):
            for _ in range(200):
                await asyncio.sleep(0.01)
                self.chunks += 1
                yield b" "
        async def aclose(self):
            self.closed = True
    stream = Trickle()
    rig.profile.timeout_seconds = 0.08
    rig.handler = lambda request: httpx.Response(200, stream=stream)
    async def run():
        start = asyncio.get_running_loop().time()
        with pytest.raises(WebsiteSessionError, match="website_login_timeout"):
            await rig.service.acquire("s1", "app", "proof")
        assert asyncio.get_running_loop().time() - start < 0.8
        assert stream.closed
        assert 0 < stream.chunks < 200
        assert not rig.service._cache
        with pytest.raises(WebsiteSessionError, match="website_login_retry_later"):
            await rig.service.acquire("s1", "app", "proof")
    asyncio.run(run())


@pytest.mark.parametrize("stage", ["resolve", "headers", "body", "combined"])
def test_deadline_covers_resolution_headers_body_and_combined_duration(rig, stage):
    completed = []
    closed = []
    async def delay(name):
        if stage == name or stage == "combined":
            await asyncio.sleep(0.06 if stage == "combined" else 1)
        completed.append(name)
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            await delay("body")
            yield json.dumps({"token": jwt(rig.now[0] + 3600)}).encode()
        async def aclose(self):
            closed.append(True)
    async def handler(request):
        await delay("headers")
        return httpx.Response(200, stream=Body())
    async def factory(url):
        await delay("resolve")
        return httpx.MockTransport(handler)
    rig.service.transport_factory = factory
    rig.profile.timeout_seconds = 0.15 if stage == "combined" else 0.05
    async def run():
        start = asyncio.get_running_loop().time()
        with pytest.raises(WebsiteSessionError, match="website_login_timeout"):
            await rig.service.acquire("s1", "app", "proof")
        assert asyncio.get_running_loop().time() - start < 0.8
        assert "body" not in completed
        if stage in {"body", "combined"}:
            assert closed
    asyncio.run(run())


def test_slow_login_does_not_block_unrelated_cached_or_new_sessions(rig, monkeypatch):
    slow_profile = rig.profile.model_copy(update={
        "login_url": "https://api.example.test/slow", "timeout_seconds": 1,
    })
    rig.config.managed_website_sessions["slow"] = slow_profile
    monkeypatch.setenv(
        "EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST",
        json.dumps([
            environment_grant(rig.profile),
            environment_grant(slow_profile, profile_id="slow"),
        ]),
    )
    targets = rig.session.metadata["http_targets"]["targets"]
    targets["slow"] = {"base_url": rig.profile.browser_origin, "managed_website_session": "slow"}
    targets["fresh"] = dict(targets["app"])
    async def run():
        cached = await rig.service.acquire("s1", "app", "proof")
        started = asyncio.Event()
        cancelled = asyncio.Event()
        original = rig.handler
        async def handler(request):
            if request.url.path == "/slow":
                started.set()
                try:
                    await asyncio.sleep(2)
                finally:
                    cancelled.set()
            return original(request)
        rig.handler = handler
        slow = asyncio.create_task(rig.service.acquire("s1", "slow", "proof"))
        try:
            await asyncio.wait_for(started.wait(), timeout=0.5)
            states = await asyncio.wait_for(asyncio.gather(
                rig.service.acquire("s1", "app", "proof"),
                rig.service.acquire("s1", "fresh", "proof"),
            ), timeout=0.3)
            assert states == [cached, cached]
            assert not slow.done()
        finally:
            slow.cancel()
            with pytest.raises(asyncio.CancelledError):
                await slow
        assert cancelled.is_set()
        rig.handler = original
        # Cancelling a login releases its slot, so a later request can retry normally.
        assert await asyncio.wait_for(rig.service.acquire("s1", "slow", "proof"), timeout=0.3) == cached
    asyncio.run(run())


def test_async_pinned_transport_uses_approved_ip_with_original_tls_host(monkeypatch):
    connections = []
    tls_hosts = []
    closed = []
    class Stream(httpcore.AsyncNetworkStream):
        async def read(self, max_bytes, timeout=None):
            return b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'
        async def write(self, buffer, timeout=None):
            pass
        async def aclose(self):
            closed.append(True)
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            tls_hosts.append(server_hostname)
            return self
        def get_extra_info(self, info):
            return None
    async def connect(self, host, port, **kwargs):
        connections.append((host, port))
        if host == "93.184.216.34":
            raise httpcore.ConnectError("first address unavailable")
        return Stream()
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect)
    async def run():
        transport = _PinnedAddressAsyncHTTPTransport(allowed_host="app.example.test", approved_addresses=[
            ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("93.184.216.35"),
        ])
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post("https://app.example.test/auth", json={})
            assert response.json() == {}
            with pytest.raises(httpx.ConnectError, match="unapproved host"):
                await client.get("https://other.example.test/")
    asyncio.run(run())
    assert connections == [("93.184.216.34", 443), ("93.184.216.35", 443)]
    assert tls_hosts == ["app.example.test"]
    assert closed
