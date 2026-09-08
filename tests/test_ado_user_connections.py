from __future__ import annotations

import asyncio
import base64
import sqlite3

import httpx
import pytest

from evander.admin.services.ado_user_connections import AdoConnectionError, AdoUserConnectionService
from evander.admin.services.managed_git_service import AzurePrCreationUncertain, AzurePrRequestError
from test_managed_git_service import _service


def _identity(organization, pat):
    return {
        "identity_id": pat.split("-")[0],
        "display_name": "User " + pat.split("-")[0],
        "email": None,
    }


def _save(service, login="alice", pat="alice-secret", directory="root"):
    return service.save(login=login, organization="org", directory_path=directory, pat=pat)


def _context(service, identity="alice", **overrides):
    arguments = dict(
        managed_repo_id="repo",
        commit_sha="1" * 40,
        directory_path="root/app",
        organization="org",
        identity_id=identity,
        source_ref="release",
    )
    arguments.update(overrides)
    return service.issue_publication_context(**arguments)


def _resolve(service, context, **overrides):
    arguments = dict(
        managed_repo_id="repo", commit_sha="1" * 40, directory_path="root/app", organization="org"
    )
    arguments.update(overrides)
    return service.resolve_publication(context, **arguments)


def test_credentials_are_encrypted_private_and_bound_to_verified_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(AdoUserConnectionService, "validate_pat", staticmethod(_identity))
    service = AdoUserConnectionService(tmp_path)
    _save(service)
    _save(service, login="bob", pat="bob-secret")
    context = _context(service)
    assert _context(service) == context
    with pytest.raises(AdoConnectionError, match="immutable"):
        _context(service, identity="bob")
    metadata, auth = _resolve(AdoUserConnectionService(tmp_path), context)
    assert metadata["identity_id"] == "alice"
    assert auth == "Basic " + base64.b64encode(b":alice-secret").decode()
    assert service.list_for_user("bob")[0]["identity_id"] == "bob"
    assert "secret" not in repr(service.list_for_user("alice"))
    for path in service.root.iterdir():
        assert b"alice-secret" not in path.read_bytes()
        assert path.stat().st_mode & 0o077 == 0
    assert service.key_path.stat().st_mode & 0o077 == 0
    with pytest.raises(AdoConnectionError, match="another user"):
        _save(service, login="mallory")
    for changes in (
        {"organization": "other"},
        {"directory_path": "root"},
        {"managed_repo_id": "other"},
        {"commit_sha": "2" * 40},
    ):
        with pytest.raises(AdoConnectionError):
            _resolve(service, context, **changes)
    _save(service, pat="alice-replacement")
    assert _resolve(service, context)[1].endswith(base64.b64encode(b":alice-replacement").decode())
    service.remove(login="alice", organization="org", directory_path="root")
    assert _resolve(service, context)[0]["fallback_reason"] == "personal_pat_missing"
    assert _resolve(service, context)[1] is None
    service.remove_user("BOB")
    assert service.list_for_user("bob") == []


def test_pat_scope_does_not_escape_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(AdoUserConnectionService, "validate_pat", staticmethod(_identity))
    service = AdoUserConnectionService(tmp_path)
    _save(service, directory="root/other")
    assert _resolve(service, _context(service))[1] is None


def test_pat_validation_uses_authenticated_identity_and_sanitizes_failures(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "authenticatedUser": {
                    "id": "USER-ID",
                    "providerDisplayName": "Alice",
                    "properties": {"Account": {"$value": "alice@example.test"}},
                }
            },
        )

    monkeypatch.setattr(httpx, "get", get)
    result = AdoUserConnectionService.validate_pat("https://dev.azure.com/Org", "personal-secret")
    assert result == {
        "identity_id": "user-id",
        "display_name": "Alice",
        "email": "alice@example.test",
    }
    assert calls[0][0] == "https://dev.azure.com/org/_apis/connectionData"

    def fail(*args, **kwargs):
        raise httpx.ConnectError("personal-secret")

    monkeypatch.setattr(httpx, "get", fail)
    with pytest.raises(AdoConnectionError) as error:
        AdoUserConnectionService.validate_pat("org", "personal-secret")
    assert "personal-secret" not in str(error.value)
    with pytest.raises(AdoConnectionError):
        AdoUserConnectionService.validate_pat("evil.example/path", "personal-secret")


@pytest.mark.parametrize("failure", [None, 401, 403, "timeout", "unknown", "missing"])
def test_personal_pr_creation_fallback_and_uncertain_reconciliation(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(AdoUserConnectionService, "validate_pat", staticmethod(_identity))
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "bot-secret")
    service = _service(tmp_path)
    connections = service.ado_user_connections
    if failure != "missing":
        _save(connections)
    context = _context(connections)
    record = dict(
        managed_repo_id="repo",
        canonical_repository_url="https://dev.azure.com/Org/Project/_git/Repo",
        provider="azure_devops",
        directory_path="root",
    )
    calls = []
    existing = []

    def request(url, *, auth, payload=None, method=None, **kwargs):
        caller = base64.b64decode(auth.removeprefix("Basic ")).decode().split(":", 1)[1]
        calls.append((method or ("POST" if payload is not None else "GET"), caller))
        if failure in (401, 403) and caller == "alice-secret":
            raise AzurePrRequestError(failure)
        if payload is None:
            return {"value": existing}
        if method == "PATCH":
            return dict(existing[0])
        creator = "alice" if caller == "alice-secret" else "bot"
        pr = {
            "pullRequestId": 42,
            "lastMergeSourceCommit": {"commitId": "1" * 40},
            "createdBy": {"id": creator, "displayName": creator},
        }
        if failure != "unknown":
            existing.append(pr)
        if failure in {"timeout", "unknown"}:
            raise AzurePrRequestError()
        return pr

    monkeypatch.setattr(service, "_azure_request_json", request)

    def publish():
        return service._create_or_reuse_azure_pr(
            record,
            destination_branch="feature",
            base_branch="main",
            expected_commit_sha="1" * 40,
            title="Feature",
            body="Body",
            draft=True,
            publication_context_id=context,
            publication_directory_path="root/app",
        )

    if failure == "unknown":
        for _ in range(2):
            with pytest.raises(AzurePrCreationUncertain):
                publish()
        assert sum(method == "POST" for method, _ in calls) == 1
        return
    result = publish()
    expected = "bot" if failure in {401, 403, "missing"} else "alice"
    assert result["actual_creator"]["id"] == expected
    assert result["intended_creator_id"] == "alice"
    assert sum(method == "POST" for method, _ in calls) == 1
    assert bool(result["fallback_reason"]) == (failure in {401, 403, "missing", "timeout"})
    again = publish()
    assert again["actual_creator"]["id"] == expected
    assert sum(method == "POST" for method, _ in calls) == 1
    with sqlite3.connect(connections.root / "connections.db") as db:
        assert (
            db.execute("SELECT actual_creator_id FROM publication_results LIMIT 1").fetchone()[0]
            == expected
        )


def test_profile_api_uses_signed_in_user_and_never_returns_pat(tmp_path, monkeypatch):
    from test_admin_web import build_admin_runtime, create_app, write_config

    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setattr(AdoUserConnectionService, "validate_pat", staticmethod(_identity))
    runtime = build_admin_runtime(write_config(tmp_path))
    app = create_app(runtime)
    _save(runtime.ado_user_connections, login="other", pat="other-secret")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            endpoint = "/api/admin/me/ado-connections"
            assert (await client.get(endpoint)).status_code == 401
            assert (
                await client.post(
                    "/api/auth/login", json={"username": "admin", "password": "swordfish"}
                )
            ).status_code == 200
            arguments = {"organization": "org", "directory_path": "root", "pat": "alice-secret"}
            result = await client.put(endpoint, json=arguments)
            assert result.status_code == 200, result.text
            assert "secret" not in result.text
            listed = await client.get(endpoint)
            assert [item["identity_id"] for item in listed.json()["connections"]] == ["alice"]
            assert (
                await client.put(endpoint, json={**arguments, "login": "other"})
            ).status_code == 400
            assert (
                await client.request(
                    "DELETE", endpoint, json={"organization": "org", "directory_path": "root"}
                )
            ).status_code == 200
            assert runtime.ado_user_connections.list_for_user("other")

    asyncio.run(exercise())


def test_forged_publication_context_is_rejected_before_git_push(tmp_path, monkeypatch):
    service = _service(tmp_path)
    context = _context(service.ado_user_connections)
    monkeypatch.setattr(
        service,
        "_require_scoped_record",
        lambda *a, **k: {
            "provider": "azure_devops",
            "default_branch": "main",
            "directory_path": "root/app",
            "canonical_repository_url": "https://dev.azure.com/org/project/_git/repo",
        },
    )
    pushes = []
    monkeypatch.setattr(service, "push_branch", lambda *a, **k: pushes.append(a))
    with pytest.raises(AdoConnectionError):
        service.pr_create(
            managed_repo_id="repo",
            source_ref="refs/heads/feature",
            expected_commit_sha="2" * 40,
            destination_branch="feature",
            directory_path="root/app",
            publication_context_id=context,
        )
    assert not pushes


def test_personal_connections_require_real_sign_in(tmp_path):
    from types import SimpleNamespace
    from starlette.applications import Starlette
    from evander.admin.web.ado_connections import ado_connection_routes

    app = Starlette(
        routes=ado_connection_routes(
            None, SimpleNamespace(config=SimpleNamespace(auth_mode="none")), None
        )
    )

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/admin/me/ado-connections")
            assert response.status_code == 403

    asyncio.run(exercise())


def test_publication_uses_request_scope_for_inherited_repository(tmp_path, monkeypatch):
    service = _service(tmp_path)
    context = _context(service.ado_user_connections)
    monkeypatch.setattr(
        service,
        "_require_scoped_record",
        lambda *a, **k: {
            "provider": "azure_devops",
            "default_branch": "main",
            "directory_path": "root",
            "managed_repo_id": "repo",
            "canonical_repository_url": "https://dev.azure.com/org/project/_git/repo",
        },
    )
    monkeypatch.setattr(service, "push_branch", lambda *a, **k: {"success": True})
    monkeypatch.setattr(service, "_persist_pr_tracking", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(service, "_create_or_reuse_azure_pr", lambda *a, **k: calls.append(k) or {})
    service.pr_create(
        managed_repo_id="repo",
        source_ref="refs/heads/feature",
        expected_commit_sha="1" * 40,
        destination_branch="feature",
        directory_path="root/app",
        publication_context_id=context,
    )
    assert calls[0]["publication_directory_path"] == "root/app"
