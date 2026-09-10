from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from config_helpers import write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.admin.web.app import create_app
from evander.admin.web.website_sessions import website_session_routes
from evander.models import SessionManifest
from evander.session_auth import session_mcp_auth_metadata
from evander.website_sessions import ManagedWebsiteSessionService, WebsiteSessionError


def profile():
    return {"browser_origin": "https://training.example.test", "login_url": "https://training.example.test/auth",
            "credential_source": "stored", "credential_fields": {"username": "username", "password": "password"},
            "public_credential_fields": ["username"], "local_storage": {"access": {"response_pointer": "/token", "encoding": "string"}}}


@pytest.fixture
def setup(tmp_path):
    runtime_dir = write_runtime_config(tmp_path, enable_directory_scoped_config=True)
    service = build_admin_runtime(runtime_dir)
    with TestClient(create_app(service)) as client:
        yield service, client, runtime_dir


def save(client, revision, value=None, credentials=None, scope="root"):
    return client.put(f"/api/managed-website-sessions/training?directory_path={scope}", json={
        "expected_revision": revision, "profile": value or profile(),
        "credentials": credentials if credentials is not None else {"username": "fake-user", "password": "fake-password-unique"},
    })


def test_ui_api_saves_rotates_tests_and_removes_encrypted_credentials(setup):
    service, client, runtime_dir = setup
    url = "/api/managed-website-sessions"
    initial = client.get(url)
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "no-store"
    assert save(client, initial.json()["revision"]).status_code == 403
    assert client.post("/api/admin/privileged/unlock").status_code == 200
    saved = save(client, initial.json()["revision"])
    assert saved.status_code == 200, saved.text
    assert "fake-password-unique" not in saved.text and "fake-user" not in saved.text
    entry = saved.json()["profiles"][0]
    assert entry["credentials"]["configured_fields"] == ["password", "username"]
    db_path = runtime_dir / "website-credentials/credentials.db"
    assert b"fake-password-unique" not in db_path.read_bytes()
    assert db_path.stat().st_mode & 0o777 == 0o600
    assert (runtime_dir / "secrets/website-credentials.key").stat().st_mode & 0o777 == 0o600
    assert save(client, initial.json()["revision"]).status_code == 409
    rotated = save(client, saved.json()["revision"], entry["profile"], {"password": "rotated-password"})
    assert rotated.status_code == 200, rotated.text
    assert "rotated-password" not in rotated.text
    assert rotated.json()["profiles"][0]["profile"]["credential_ref"] != entry["profile"]["credential_ref"]
    seen = []
    def login(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"token": "temporary-website-token"})
    service.website_session_admin.broker.transport_factory = lambda url: httpx.MockTransport(login)
    tested = client.post(url + "/training/test", json={"expected_revision": rotated.json()["revision"]})
    assert tested.status_code == 200, tested.text
    assert seen == [{"username": "fake-user", "password": "rotated-password"}]
    assert "temporary-website-token" not in tested.text
    assert tested.json()["profiles"][0]["last_test"]["status"] == "ok"
    assert client.post(url + "/training/test", json={"expected_revision": rotated.json()["revision"]}).status_code == 400
    removed = client.request(
        "DELETE", url + "/training/credentials", json={"expected_revision": tested.json()["revision"]})
    assert removed.status_code == 200, removed.text
    assert removed.json()["profiles"][0]["credentials"]["configured_fields"] == []
    assert removed.json()["profiles"][0]["profile"]["credential_source"] == "stored"
    with pytest.raises(WebsiteSessionError, match="website_credentials_unavailable"):
        service.website_session_admin.broker.credentials("root", "training", service.runtime.provider_config.resolve_for_directory("root").managed_website_sessions["training"])


def test_destination_change_requires_new_credentials_and_no_caller_selected_vault_reference(setup):
    service, client, _ = setup
    client.post("/api/admin/privileged/unlock")
    original = save(client, client.get("/api/managed-website-sessions").json()["revision"]).json()
    old_profile = original["profiles"][0]["profile"]
    changed = old_profile | {"login_url": "https://other.example.test/auth"}
    response = save(client, original["revision"], changed, {})
    assert response.status_code == 400
    assert "Enter all credentials" in response.text
    assert client.get("/api/managed-website-sessions").json()["revision"] == original["revision"]
    rotated = save(client, original["revision"], changed)
    assert rotated.status_code == 200
    assert rotated.json()["profiles"][0]["profile"]["credential_ref"] != old_profile["credential_ref"]


def test_stored_credentials_are_scoped_and_inherited_without_sibling_access(setup):
    service, client, _ = setup
    service.operations_admin.create_directory_child("root", "alice")
    service.operations_admin.create_directory_child("root", "bob")
    service.operations_admin.create_directory_child("root/alice", "child")
    client.post("/api/admin/privileged/unlock")
    start = client.get("/api/managed-website-sessions?directory_path=root/alice").json()
    saved = save(client, start["revision"], scope="root/alice").json()
    inherited = client.get("/api/managed-website-sessions?directory_path=root/alice/child").json()["profiles"][0]
    assert not inherited["local"]
    assert inherited["credentials"]["configured_fields"] == ["password", "username"]
    assert client.get("/api/managed-website-sessions?directory_path=root/bob").json()["profiles"] == []
    bound = service.runtime.provider_config.resolve_for_directory("root/alice").managed_website_sessions["training"]
    assert service.website_session_admin.credentials.read(bound.credential_ref, "root/bob", "training", bound) == {}
    assert service.website_session_admin.credentials.read(bound.credential_ref, "root/alice", "different", bound) == {}
    stolen = save(client, client.get("/api/managed-website-sessions?directory_path=root/bob").json()["revision"], saved["profiles"][0]["profile"], {}, scope="root/bob")
    assert stolen.status_code == 400


def test_relay_uses_rotated_ui_credentials_and_revocation_blocks_cached_state(setup, monkeypatch):
    service, client, _ = setup
    client.post("/api/admin/privileged/unlock")
    saved = save(client, client.get("/api/managed-website-sessions").json()["revision"]).json()
    manifest = SessionManifest(session_id="browser-session", allowed_tools=[], expires_at=datetime.now(UTC) + timedelta(days=1), metadata={
        "mcp_session_auth": session_mcp_auth_metadata("proof"),
        "http_targets": {"targets": {"app": {"base_url": "https://training.example.test", "managed_website_session": "training"}}},
    })
    service.runtime.session_repository.upsert_session(manifest)
    seen = []
    def login(request):
        seen.append(json.loads(request.content)["password"])
        return httpx.Response(200, json={"token": "temporary-state"})
    broker = ManagedWebsiteSessionService(service.runtime.provider_config, transport_factory=lambda url: httpx.MockTransport(login))
    asyncio.run(broker.acquire("browser-session", "app", "proof"))
    rotated = save(client, saved["revision"], saved["profiles"][0]["profile"], {"password": "new-secret"}).json()
    asyncio.run(broker.acquire("browser-session", "app", "proof"))
    assert seen == ["fake-password-unique", "new-secret"]
    client.request("DELETE", "/api/managed-website-sessions/training/credentials", json={"expected_revision": rotated["revision"]})
    monkeypatch.setenv("password", "must-not-fallback-to-env")
    with pytest.raises(WebsiteSessionError, match="website_credentials_unavailable"):
        asyncio.run(broker.acquire("browser-session", "app", "proof"))
    assert len(seen) == 2


def test_login_failures_and_invalid_credential_payloads_are_not_reflected(setup):
    service, client, _ = setup
    client.post("/api/admin/privileged/unlock")
    revision = client.get("/api/managed-website-sessions").json()["revision"]
    invalid = save(client, revision, profile() | {"timeout_seconds": "do-not-echo-this-secret"})
    assert invalid.status_code == 400
    assert "do-not-echo-this-secret" not in invalid.text
    saved = save(client, revision).json()
    service.website_session_admin.broker.transport_factory = lambda url: httpx.MockTransport(lambda r: httpx.Response(401, text="fake-password-unique"))
    result = client.post("/api/managed-website-sessions/training/test", json={"expected_revision": saved["revision"]})
    assert result.json()["profiles"][0]["last_test"]["status"] == "website_login_rejected"
    assert "fake-password-unique" not in result.text
    missing = client.put("/api/managed-website-sessions/training", json={"profile": profile()})
    assert missing.status_code == 428


def test_routes_require_owner_in_requested_directory(setup):
    service, _, _ = setup
    class Auth:
        def require(self, request): return {"login": "limited-owner"}
        def require_permission(self, request, permission, directory_path):
            raise PermissionError("not allowed")
    with TestClient(Starlette(routes=website_session_routes(service, Auth()))) as client:
        assert client.get("/api/managed-website-sessions").status_code == 403
        assert client.put("/api/managed-website-sessions/training", json={}).status_code == 403


def test_environment_profile_rejects_names_not_approved_by_host(setup, monkeypatch):
    _, client, _ = setup
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-secret")
    client.post("/api/admin/privileged/unlock")
    initial = client.get("/api/managed-website-sessions").json()
    environment_profile = profile() | {
        "credential_source": "environment",
        "credential_fields": {"password": "EVANDER_ADMIN_WEB_PASSWORD"},
        "public_credential_fields": [],
    }
    response = save(client, initial["revision"], environment_profile, {})
    assert response.status_code == 400
    assert "not host-approved" in response.text


def test_disabling_profile_needs_no_password_and_retains_existing_encrypted_login(setup):
    _, client, _ = setup
    client.post("/api/admin/privileged/unlock")
    original = save(client, client.get("/api/managed-website-sessions").json()["revision"]).json()
    before = original["profiles"][0]["profile"]
    disabled = save(client, original["revision"], before | {"enabled": False}, {})
    assert disabled.status_code == 200
    assert disabled.json()["profiles"][0]["profile"]["credential_ref"] == before["credential_ref"]
    removed = client.request("DELETE", "/api/managed-website-sessions/training/credentials", json={"expected_revision": disabled.json()["revision"]}).json()
    disabled_again = save(client, removed["revision"], removed["profiles"][0]["profile"], {})
    assert disabled_again.status_code == 200
    assert disabled_again.json()["profiles"][0]["profile"]["credential_ref"] is None


@pytest.mark.parametrize("scope", ["root", "root/child"])
@pytest.mark.parametrize("removal", ["delete_profile", "remove_catalog"])
def test_deleting_bootstrapped_root_profile_revokes_warm_and_cold_brokers(tmp_path, monkeypatch, scope, removal):
    value = profile() | {"credential_source": "environment"}
    monkeypatch.setenv("username", "fake-user")
    monkeypatch.setenv("password", "host-only-password")
    monkeypatch.setenv("EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST", "username,password")
    runtime = write_runtime_config(tmp_path, enable_directory_scoped_config=True,
                                   managed_website_sessions={"training": value})
    service = build_admin_runtime(runtime)
    service.operations_admin.create_directory_child("root", "child")
    # This is the startup snapshot that used to resurrect deleted root profiles.
    assert "training" in service.runtime.provider_config.config.managed_website_sessions
    manifest = SessionManifest(session_id="browser", directory_path=scope, allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(days=1), metadata={
            "mcp_session_auth": session_mcp_auth_metadata("proof"),
            "http_targets": {"targets": {"app": {"base_url": value["browser_origin"], "managed_website_session": "training"}}},
        })
    service.runtime.session_repository.upsert_session(manifest)
    calls = []
    def login(request):
        calls.append(request)
        return httpx.Response(200, json={"token": "temporary-state"})
    brokers = [ManagedWebsiteSessionService(service.runtime.provider_config,
        transport_factory=lambda _: httpx.MockTransport(login)) for _ in range(2)]
    asyncio.run(brokers[0].acquire("browser", "app", "proof"))
    with TestClient(create_app(service)) as client:
        client.post("/api/admin/privileged/unlock")
        before = client.get("/api/managed-website-sessions").json()
        if removal == "delete_profile":
            deleted = client.request("DELETE", "/api/managed-website-sessions/training",
                                     json={"expected_revision": before["revision"]})
            assert deleted.status_code == 200, deleted.text
        else:
            overlay = service.operations_admin.read_directory_domain_config("root", "providers").data.config
            overlay.pop("managed_website_sessions")
            assert service.operations_admin.write_directory_domain_config("root", "providers", overlay).ok
        assert client.get("/api/managed-website-sessions").json()["profiles"] == []
        for broker in brokers:
            with pytest.raises(WebsiteSessionError, match="website_session_not_granted"):
                asyncio.run(broker.acquire("browser", "app", "proof"))
    assert len(calls) == 1


def test_child_profile_replaces_parent_fields_and_retains_parent_credentials(setup):
    service, client, _ = setup
    service.operations_admin.create_directory_child("root", "child")
    client.post("/api/admin/privileged/unlock")
    parent_value = profile() | {
        "local_storage": {"obsolete": {"response_pointer": "/old"}, **profile()["local_storage"]},
        "session_storage": {"old-session": {"response_pointer": "/old"}},
        "static_fields": {"parent_only": True},
    }
    parent = save(client, client.get("/api/managed-website-sessions").json()["revision"], parent_value).json()
    inherited = client.get("/api/managed-website-sessions?directory_path=root/child").json()
    replacement = profile() | {
        "credential_fields": {"email": "email", "password": "password"},
        "public_credential_fields": ["email"],
    }
    response = save(client, inherited["revision"], replacement,
                    {"email": "child-user", "password": "child-password"}, scope="root/child")
    assert response.status_code == 200, response.text
    effective = service.runtime.provider_config.resolve_for_directory("root/child").managed_website_sessions["training"]
    requested = type(effective).model_validate(replacement)
    assert effective.model_dump(exclude={"credential_ref"}) == requested.model_dump(exclude={"credential_ref"})
    assert response.json()["profiles"][0]["credentials"]["configured_fields"] == ["email", "password"]
    seen = []
    def login(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"token": "temporary-state"})
    service.website_session_admin.broker.transport_factory = lambda _: httpx.MockTransport(login)
    tested = client.post("/api/managed-website-sessions/training/test?directory_path=root/child",
                         json={"expected_revision": response.json()["revision"]})
    assert tested.status_code == 200, tested.text
    assert tested.json()["profiles"][0]["last_test"]["status"] == "ok"
    assert seen == [{"email": "child-user", "password": "child-password"}]
    # Removing the local override must restore the intact parent profile and vault binding.
    removed = client.request("DELETE", "/api/managed-website-sessions/training?directory_path=root/child",
                             json={"expected_revision": tested.json()["revision"]})
    assert removed.status_code == 200, removed.text
    restored = removed.json()["profiles"][0]
    assert restored["profile"] == parent["profiles"][0]["profile"]
    assert restored["credentials"]["configured_fields"] == ["password", "username"]
    assert not restored["local"]
