from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from evander.admin.services import ado_user_connections as module
from evander.admin.services.ado_oauth import (
    AdoOAuthConfig,
    AdoOAuthError,
    CALLBACK_PATH,
    SCOPES,
    exchange_token,
)
from evander.admin.services.ado_user_connections import AdoConnectionError, AdoUserConnectionService
from evander.admin.services.managed_git_service import AzurePrCreationUncertain, AzurePrRequestError
from test_ado_user_connections import _context, _identity, _resolve, _save
from test_managed_git_service import _service


@pytest.fixture
def oauth(monkeypatch):
    for name, value in {
        "ENABLED": "true",
        "TENANT_ID": "tenant.example",
        "CLIENT_ID": "client-id",
        "CLIENT_SECRET": "client-secret",
        "REDIRECT_URI": "https://testserver" + CALLBACK_PATH,
    }.items():
        monkeypatch.setenv("EVANDER_ADO_OAUTH_" + name, value)
    monkeypatch.setattr(AdoUserConnectionService, "validate_pat", staticmethod(_identity))
    monkeypatch.setattr(
        AdoUserConnectionService,
        "validate_identity",
        staticmethod(lambda *a, **k: _identity("org", "alice-secret")),
    )
    return AdoOAuthConfig.from_env()


def token(config, *, expired=False, access="access-secret", refresh="refresh-secret"):
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": time.time() + (-1 if expired else 3600),
        "client_binding": config.binding,
        "scope": SCOPES,
    }


def begin(service, **overrides):
    arguments = dict(
        login="alice", organization="org", directory_path="root/app", session_hash="session"
    )
    arguments.update(overrides)
    url, browser = service.begin_oauth(**arguments)
    state = parse_qs(urlsplit(url).query)["state"][0]
    return arguments, url, browser, state


def connect(service, config, monkeypatch, *, expired=False, **overrides):
    arguments, _, browser, state = begin(service, **overrides)
    pending = service.consume_oauth(
        login=arguments["login"],
        session_hash=arguments["session_hash"],
        browser=browser,
        state=state,
    )
    monkeypatch.setattr(module, "exchange_token", lambda *a, **k: token(config, expired=expired))
    service.complete_oauth(pending, "code")


def test_oauth_config_reuses_sso_but_requires_explicit_opt_in(oauth, monkeypatch):
    assert "client-secret" not in repr(oauth)
    for field in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"):
        monkeypatch.setenv(
            "EVANDER_ADMIN_WEB_AZURE_" + field,
            os.environ["EVANDER_ADO_OAUTH_" + field],
        )
        monkeypatch.delenv("EVANDER_ADO_OAUTH_" + field)
    assert AdoOAuthConfig.from_env() == oauth
    monkeypatch.setenv("EVANDER_ADO_OAUTH_ENABLED", "false")
    assert not AdoUserConnectionService.oauth_available()
    monkeypatch.setenv("EVANDER_ADO_OAUTH_ENABLED", "true")
    monkeypatch.setenv("EVANDER_ADO_OAUTH_TENANT_ID", "common")
    with pytest.raises(AdoOAuthError):
        AdoOAuthConfig.from_env()


@pytest.mark.parametrize("mismatch", ["login", "session_hash", "browser", "state", "expired"])
def test_oauth_state_is_browser_session_user_bound_and_one_use(tmp_path, oauth, mismatch):
    service = AdoUserConnectionService(tmp_path)
    _, url, browser, state = begin(service)
    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == [SCOPES]
    assert "secret" not in url
    args = dict(login="alice", session_hash="session", browser=browser, state=state)
    if mismatch == "expired":
        with service._database() as db:
            db.execute("UPDATE oauth_pending SET expires_at=0")
    else:
        args[mismatch] = "wrong"
    with pytest.raises(AdoConnectionError):
        service.consume_oauth(**args)
    if mismatch != "expired":
        service.consume_oauth(login="alice", session_hash="session", browser=browser, state=state)
        with pytest.raises(AdoConnectionError):
            service.consume_oauth(
                login="alice", session_hash="session", browser=browser, state=state
            )


def test_pat_and_oauth_coexist_are_encrypted_and_disconnect_independently(
    tmp_path, oauth, monkeypatch
):
    service = AdoUserConnectionService(tmp_path)
    _save(service, directory="root/app")
    connect(service, oauth, monkeypatch)
    context = _context(service)
    assert _resolve(service, context)[1] == "Bearer access-secret"
    assert _resolve(service, context)[0]["credential_source"] == "personal_oauth"
    metadata = service.list_for_user("alice")[0]
    assert metadata["has_pat"] and metadata["has_oauth"]
    assert not any(
        secret in repr(metadata) for secret in ("access-secret", "refresh-secret", "alice-secret")
    )
    for file in service.root.iterdir():
        assert file.stat().st_mode & 0o077 == 0
        assert b"access-secret" not in file.read_bytes()
        assert b"refresh-secret" not in file.read_bytes()
    with pytest.raises(AdoConnectionError, match="same ADO account"):
        _save(service, pat="bob-secret", directory="root/app")
    service.remove(login="alice", organization="org", directory_path="root/app", method="pat")
    assert _resolve(service, context)[1] == "Bearer access-secret"
    _save(service, directory="root/app")
    service.remove(login="alice", organization="org", directory_path="root/app", method="oauth")
    assert _resolve(service, context)[0]["credential_source"] == "personal_pat"


def test_pat_migration_and_oauth_ownership_cannot_be_rebound(tmp_path, oauth, monkeypatch):
    service = AdoUserConnectionService(tmp_path)
    _save(service, directory="root/app")
    with service._database() as db:
        db.execute("ALTER TABLE connections DROP COLUMN encrypted_oauth")
        db.execute("ALTER TABLE connections DROP COLUMN oauth_status")
    assert service.list_for_user("alice")[0]["has_pat"]
    with pytest.raises(AdoConnectionError, match="another user"):
        connect(service, oauth, monkeypatch, login="mallory")
    monkeypatch.setattr(
        service, "validate_identity", lambda *a, **k: _identity("org", "bob-secret")
    )
    with pytest.raises(AdoConnectionError, match="same ADO account"):
        connect(service, oauth, monkeypatch)
    assert service.list_for_user("alice")[0]["identity_id"] == "alice"


def test_refresh_rotates_once_across_service_instances(tmp_path, oauth, monkeypatch):
    service = AdoUserConnectionService(tmp_path)
    connect(service, oauth, monkeypatch, expired=True)
    context = _context(service)
    calls = []

    def refresh(config, **kwargs):
        calls.append(kwargs["previous"]["refresh_token"])
        time.sleep(0.05)
        return token(config, access="new-access", refresh="rotated-refresh")

    monkeypatch.setattr(module, "exchange_token", refresh)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(
            pool.map(lambda _: _resolve(AdoUserConnectionService(tmp_path), context)[1], range(2))
        )
    assert values == ["Bearer new-access"] * 2
    assert calls == ["refresh-secret"]
    with service._database() as db:
        encrypted = db.execute("SELECT encrypted_oauth FROM connections").fetchone()[0]
    assert (
        json.loads(service._cipher().decrypt(encrypted.encode()))["refresh_token"]
        == "rotated-refresh"
    )


@pytest.mark.parametrize(
    "failure", ["revoked", "temporary", "identity_changed", "deleted", "all_deleted"]
)
def test_refresh_failure_falls_back_without_resurrecting_connections(
    tmp_path, oauth, monkeypatch, failure
):
    service = AdoUserConnectionService(tmp_path)
    _save(service, directory="root/app")
    connect(service, oauth, monkeypatch, expired=True)

    def refresh(config, **kwargs):
        if failure in {"revoked", "temporary"}:
            raise AdoOAuthError(reconnect=failure == "revoked")
        if failure in {"deleted", "all_deleted"}:
            service.remove(
                login="alice",
                organization="org",
                directory_path="root/app",
                method="oauth" if failure == "deleted" else None,
            )
        if failure == "identity_changed":
            monkeypatch.setattr(
                service, "validate_identity", lambda *a, **k: _identity("org", "bob-secret")
            )
        return token(config)

    monkeypatch.setattr(module, "exchange_token", refresh)
    result, auth = _resolve(service, _context(service))
    if failure == "all_deleted":
        assert auth is None
        assert not service.list_for_user("alice")
        return
    assert result["credential_source"] == "personal_pat"
    assert auth.startswith("Basic ")
    row = service.list_for_user("alice")[0]
    if failure == "deleted":
        assert not row["has_oauth"]
    if failure in {"revoked", "identity_changed"}:
        assert row["oauth_status"] == "reconnect_required"


def test_disconnect_cancels_inflight_consent(tmp_path, oauth, monkeypatch):
    service = AdoUserConnectionService(tmp_path)
    _, _, browser, state = begin(service)
    pending = service.consume_oauth(
        login="alice", session_hash="session", browser=browser, state=state
    )
    service.remove_user("alice")
    monkeypatch.setattr(module, "exchange_token", lambda *a, **k: token(oauth))
    with pytest.raises(AdoConnectionError, match="cancelled"):
        service.complete_oauth(pending, "code")
    assert not service.list_for_user("alice")


def test_exchange_uses_confidential_pkce_and_sanitizes_provider_errors(oauth, monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "access_token": "opaque-access",
                "refresh_token": "opaque-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": SCOPES,
            },
        )

    monkeypatch.setattr(httpx, "post", post)
    first = exchange_token(oauth, code="one-use-code", verifier="pkce-secret")
    first["scope"] = "499b84ac-1321-427f-aa17-267ca6975798/vso.code_write"
    exchange_token(oauth, previous=first)
    assert calls[0][1]["data"]["code_verifier"] == "pkce-secret"
    assert calls[0][1]["data"]["client_secret"] == "client-secret"
    assert calls[1][1]["data"]["refresh_token"] == "opaque-refresh"
    assert calls[1][1]["data"]["scope"] == first["scope"]
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **k: httpx.Response(
            400,
            request=httpx.Request("POST", url),
            json={"error": "invalid_grant", "error_description": "sensitive-refresh-secret"},
        ),
    )
    with pytest.raises(AdoOAuthError) as error:
        exchange_token(oauth, previous=first)
    assert error.value.reconnect
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("failure", [None, 401, 403, "pat_fallback", "unknown"])
def test_pr_oauth_pat_bot_fallback_keeps_uncertain_post_guard(
    tmp_path, oauth, monkeypatch, failure
):
    service = _service(tmp_path)
    _save(service.ado_user_connections, directory="root/app")
    connect(service.ado_user_connections, oauth, monkeypatch)
    context = _context(service.ado_user_connections)
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "bot-secret")
    calls = []

    def request(url, *, auth, payload=None, method=None, **kwargs):
        calls.append((auth, payload))
        if failure == "pat_fallback" and auth.startswith("Bearer "):
            raise AzurePrRequestError(401)
        if failure in {401, 403} and auth != "Basic " + base64.b64encode(b":bot-secret").decode():
            raise AzurePrRequestError(failure)
        if payload is None:
            return {"value": []}
        if failure == "unknown":
            raise AzurePrRequestError()
        return {
            "pullRequestId": 42,
            "lastMergeSourceCommit": {"commitId": "1" * 40},
            "createdBy": {"id": "bot" if failure in {401, 403} else "alice"},
        }

    monkeypatch.setattr(service, "_azure_request_json", request)
    record = dict(
        managed_repo_id="repo",
        directory_path="root/app",
        canonical_repository_url="https://dev.azure.com/org/project/_git/repo",
        provider="azure_devops",
    )

    def publish():
        return service._create_or_reuse_azure_pr(
            record,
            destination_branch="feature",
            base_branch="main",
            expected_commit_sha="1" * 40,
            title="Title",
            body="Body",
            draft=True,
            publication_context_id=context,
        )

    if failure == "unknown":
        for _ in range(2):
            with pytest.raises(AzurePrCreationUncertain):
                publish()
        assert sum(payload is not None for _, payload in calls) == 1
    else:
        result = publish()
        expected = (
            "bot"
            if failure in {401, 403}
            else "personal_pat"
            if failure == "pat_fallback"
            else "personal_oauth"
        )
        assert result["credential_source"] == expected
        if failure is not None:
            assert "personal_oauth" in result["fallback_reason"]
        else:
            assert result["fallback_reason"] is None
        if expected == "bot":
            assert "personal_pat" in result["fallback_reason"]
        assert calls[0][0].startswith("Bearer ")


def test_profile_oauth_callback_and_cancel_flow(tmp_path, oauth, monkeypatch):
    from test_admin_web import build_admin_runtime, create_app, write_config

    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    runtime = build_admin_runtime(write_config(tmp_path))
    app = create_app(runtime)
    monkeypatch.setattr(module, "exchange_token", lambda *a, **k: token(oauth))

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            endpoint = "/api/admin/me/ado-connections"
            assert (await client.post(endpoint + "/oauth/start", json={})).status_code == 401
            await client.post(
                "/api/auth/login", json={"username": "admin", "password": "swordfish"}
            )
            assert (await client.get(endpoint)).json()["oauth_available"]
            for cancelled in (True, False):
                response = await client.post(
                    endpoint + "/oauth/start",
                    json={"organization": "org", "directory_path": "root"},
                )
                assert response.status_code == 200, response.text
                assert "HttpOnly" in response.headers["set-cookie"]
                state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
                callback = await client.get(
                    CALLBACK_PATH,
                    params={
                        "state": state,
                        **(
                            {"error": "access_denied", "error_description": "sensitive"}
                            if cancelled
                            else {"code": "code"}
                        ),
                    },
                )
                assert callback.status_code == 303
                assert callback.headers["location"].endswith(
                    "cancelled" if cancelled else "connected"
                )
                assert "sensitive" not in callback.text
            listing = (await client.get(endpoint)).json()["connections"]
            assert listing[0]["has_oauth"] and not listing[0]["has_pat"]
            assert "access-secret" not in repr(listing)

    asyncio.run(exercise())
