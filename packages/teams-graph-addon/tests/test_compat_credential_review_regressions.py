from __future__ import annotations


import asyncio


import json


import httpx


import pytest


from starlette.applications import Starlette


from evander.admin.services.teams_service import TeamsGraphClient


from evander.admin.web.teams_auth import teams_auth_routes


from evander.credentials.bindings import teams_binding


from evander.credentials.migration import _overlay, config_revision, migrate_teams


from evander.credentials.teams import ManagedTeamsCredentialStore


from evander.directories import DirectoryService


from evander.messaging.teams_auth import TeamsAuthenticationRequiredError


from evander.services import GraphTeamsService, TeamsAuthenticationError


from test_compat_managed_credentials_integrations import (
    Auth,
    admin as admin_fixture,
    create_teams,
    custody as custody_fixture,
    legacy_teams_source,
    teams_provider,
    teams_values,
)


admin = admin_fixture


custody = custody_fixture


def fake_microsoft(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/devicecode"):
            return httpx.Response(
                200,
                json={
                    "device_code": "synthetic-device-code",
                    "user_code": "ABCD",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "message": "Sign in",
                    "interval": 1,
                    "expires_in": 600,
                },
            )
        return httpx.Response(200, json={"access_token": "new-teams-token", "expires_in": 3600})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: real_client(*a, **kw, transport=httpx.MockTransport(handle)),
    )
    return requests


def write_providers(admin, directory, **updates):
    overlay = _overlay(admin.credential_admin, directory)
    overlay.update(updates)
    result = admin.operations_admin.write_directory_domain_config(directory, "providers", overlay)
    assert result.ok, "\n".join(error.message for error in result.errors)


@pytest.mark.parametrize("client_kind", ["admin", "runtime"])
@pytest.mark.parametrize(
    "destination",
    [
        "https://unapproved.test/v1.0",
        "http://graph.microsoft.com/v1.0",
        "https://graph.microsoft.com/other",
    ],
)
def test_teams_graph_destination_is_approved_before_any_bearer_request(
    custody, monkeypatch, client_kind, destination
):
    config, record = create_teams(custody)
    custody.update(
        record["id"],
        expected_revision=record["revision"],
        bindings=[teams_binding(config, "root"), teams_binding(config, "root/team")],
    )
    sent = []
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: real_client(
            *a,
            **kw,
            transport=httpx.MockTransport(
                lambda request: (
                    sent.append(request) or httpx.Response(200, json={"value": [], "id": "user-a"})
                )
            ),
        ),
    )

    def request_for(candidate):
        provider = teams_provider(
            candidate, httpx.MockTransport(lambda r: pytest.fail("Unexpected refresh"))
        )
        provider.credential_store = ManagedTeamsCredentialStore(candidate, "root/team")
        if client_kind == "admin":
            TeamsGraphClient(config=candidate, token_provider=provider).list_channels(
                team_id="team-a"
            )
        else:
            service = GraphTeamsService(
                None,
                token_provider=provider.get_bearer_token,
                api_base_url=candidate.teams_graph_api_base_url,
                poll_limit=1,
                timeout_seconds=1,
            )
            with httpx.Client() as client:
                service.resolve_user_id(client, "user-a")

    request_for(config)
    assert len(sent) == 1 and sent[0].headers["authorization"] == "Bearer original-teams-token"
    changed = config.model_copy(update={"teams_graph_api_base_url": destination})
    with pytest.raises((TeamsAuthenticationRequiredError, TeamsAuthenticationError)):
        request_for(changed)
    assert len(sent) == 1
    assert (
        teams_binding(config, "root/team")["fingerprint"]
        != teams_binding(changed, "root/team")["fingerprint"]
    )


@pytest.mark.parametrize(
    "replacement", ["new_record", "legacy", "changed_binding", "old_session", "old_session_legacy"]
)
def test_pending_login_cannot_change_reference_or_policy(admin, monkeypatch, replacement):
    legacy_teams_source(admin)
    migrate_teams(admin, apply=True, maintenance=True)
    requests = fake_microsoft(monkeypatch)
    session = admin.teams_admin.login()
    record = admin.credential_admin.snapshot("root")["credentials"][0]
    if replacement == "new_record":
        admin.credential_admin.update(
            "root", record["id"], {"action": "delete", "expected_revision": record["revision"]}
        )
        admin.credential_admin.prepare_teams(
            "root",
            {"expected_config_revision": config_revision(_overlay(admin.credential_admin, "root"))},
        )
        new_record = admin.credential_admin.snapshot("root")["credentials"][0]
        assert new_record["id"] != record["id"] and new_record["revision"] == record["revision"]
    elif replacement == "legacy":
        write_providers(admin, "root", teams_delegated_credential_ref=None)
    elif replacement.startswith("old_session"):
        session.metadata.clear()
        session.metadata["credential_revision"] = record["revision"]
        if replacement == "old_session_legacy":
            write_providers(admin, "root", teams_delegated_credential_ref=None)
    else:
        write_providers(admin, "root", teams_delegated_authority_base_url="https://unapproved.test")
    with pytest.raises(TeamsAuthenticationRequiredError):
        admin.teams_admin.complete_login(session)
    assert len(requests) == 1  # Reject before sending the pending device code.
    if replacement == "new_record":
        assert admin.teams_admin.status()["authenticated"] is False
    elif replacement == "legacy":
        assert admin.teams_admin.status()["authenticated"] is False


def shared_teams(admin, *, root_identity=False):
    source = legacy_teams_source(admin)
    if not root_identity:
        write_providers(admin, "root", teams_backend="stub")
    for name in ["a", "b"]:
        DirectoryService(admin.directory_store).create(parent="root", name=name)
        write_providers(
            admin,
            f"root/{name}",
            teams_backend="delegated_graph_api",
            teams_delegated_identity_id="shared",
            teams_delegated_client_id="client-a",
            teams_delegated_tenant_id="tenant-a",
        )
    source.write_text(
        json.dumps(
            {
                **({"identity-a": teams_values().model_dump(mode="json")} if root_identity else {}),
                "shared": teams_values().model_dump(mode="json"),
            }
        )
    )
    assert migrate_teams(admin, apply=True, maintenance=True)["applied"]
    return next(m for m in admin.credential_admin.store.list_metadata() if m["label"] == "shared")


@pytest.mark.parametrize("root_identity", [False, True])
def test_shared_teams_owner_manages_specific_reference_without_root_consumer(
    admin, monkeypatch, root_identity
):
    record = shared_teams(admin, root_identity=root_identity)
    before = _overlay(admin.credential_admin, "root")
    requests = fake_microsoft(monkeypatch)
    assert {b["directory"] for b in record["bindings"]} == {"root/a", "root/b"}
    assert admin.teams_admin.status(credential_ref=record["id"])["identity_id"] == "shared"
    admin.teams_admin.logout(credential_ref=record["id"])
    assert not admin.teams_admin.status(directory="root/a")["authenticated"]
    session = admin.teams_admin.login(credential_ref=record["id"])
    result = admin.teams_admin.complete_login(session, credential_ref=record["id"])
    assert result["authenticated"] and result["identity_id"] == "shared"
    assert admin.teams_admin.status(directory="root/b")["authenticated"]
    assert len(requests) == 2
    assert _overlay(admin.credential_admin, "root") == before
    assert admin.credential_admin.store.metadata(record["id"])["bindings"] == record["bindings"]
    for action in ["status", "login", "logout"]:
        with pytest.raises(TeamsAuthenticationRequiredError):
            getattr(admin.teams_admin, action)(directory="root/a", credential_ref=record["id"])
    if root_identity:
        assert admin.teams_admin.status()["identity_id"] == "identity-a"
        assert admin.teams_admin.status()["authenticated"]


@pytest.mark.parametrize("status", ["disabled", "deleted"])
def test_owner_cannot_sign_into_inactive_reference(admin, monkeypatch, status):
    record = shared_teams(admin)
    requests = fake_microsoft(monkeypatch)
    admin.credential_admin.store.update(
        record["id"], expected_revision=record["revision"], status=status
    )
    with pytest.raises(TeamsAuthenticationRequiredError):
        admin.teams_admin.login(credential_ref=record["id"])
    assert requests == []


@pytest.mark.parametrize("status", ["disabled", "deleted"])
def test_teams_identity_listing_preserves_other_entries_when_credential_unavailable(
    admin, monkeypatch, status
):
    from starlette.testclient import TestClient
    from typer.testing import CliRunner
    from evander.cli import app

    record = shared_teams(admin, root_identity=True)
    requests = fake_microsoft(monkeypatch)
    admin.credential_admin.store.update(
        record["id"], expected_revision=record["revision"], status=status
    )
    entries = {entry["directory_path"]: entry for entry in admin.teams_admin.list()}
    assert entries["root"]["authenticated"]
    for directory in ("root/a", "root/b"):
        assert not entries[directory]["authenticated"]
        assert entries[directory]["status"] == "unavailable"
        assert entries[directory]["expires_at"] is None
        with pytest.raises(TeamsAuthenticationRequiredError):
            admin.teams_admin._teams_auth_provider_for_directory(directory)[3].get_bearer_token()
    with TestClient(Starlette(routes=teams_auth_routes(admin, Auth()))) as client:
        response = client.get("/api/teams-auth/identities")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 3
    assert "original-teams-token" not in response.text
    result = CliRunner().invoke(
        app, ["teams-auth", "list", "--config", str(admin.config.runtime_dir)]
    )
    assert result.exit_code == 0, result.output
    assert "unavailable" in result.output and "authenticated" in result.output
    assert requests == []


def test_reference_auth_routes_check_owner_lock_and_session_target(admin, monkeypatch):
    record = shared_teams(admin, root_identity=True)
    requests = fake_microsoft(monkeypatch)
    auth = Auth()
    app = Starlette(routes=teams_auth_routes(admin, auth))

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            params = {"directory_path": "root", "credential_ref": record["id"]}
            auth.forbidden = True
            assert (await client.get("/api/teams-auth/status", params=params)).status_code == 403
            auth.forbidden = False
            auth.locked = True
            assert (await client.post("/api/teams-auth/login", params=params)).status_code == 403
            auth.locked = False
            login = await client.post("/api/teams-auth/login", params=params)
            assert login.status_code == 200
            assert "device_code" not in login.json()
            session = {"login_session_id": login.json()["login_session_id"]}
            other = admin.runtime.provider_config.resolve_for_directory(
                "root"
            ).teams_delegated_credential_ref
            wrong = await client.post(
                "/api/teams-auth/complete", params={**params, "credential_ref": other}, json=session
            )
            assert wrong.status_code == 400 and len(requests) == 1
            complete = await client.post("/api/teams-auth/complete", params=params, json=session)
            assert complete.status_code == 200 and complete.json()["authenticated"]
            assert "new-teams-token" not in complete.text
            logout = await client.post("/api/teams-auth/logout", params=params)
            assert logout.status_code == 200 and not logout.json()["authenticated"]

    asyncio.run(exercise())


@pytest.mark.parametrize("local_ref", ["absent", "null"])
def test_migration_resumes_after_parent_switch_with_distinct_child_identity(
    admin, monkeypatch, local_ref
):
    import evander.credentials.migration as migration

    source = legacy_teams_source(admin)
    DirectoryService(admin.directory_store).create(parent="root", name="child")
    write_providers(
        admin,
        "root/child",
        teams_delegated_identity_id="identity-b",
        **({"teams_delegated_credential_ref": None} if local_ref == "null" else {}),
    )
    source.write_text(
        json.dumps(
            {
                identity: teams_values().model_dump(mode="json")
                for identity in ["identity-a", "identity-b"]
            }
        )
    )
    write = migration._write

    def interrupt(admin, directory, payload):
        if directory == "root/child":
            raise RuntimeError("interrupted after parent switch")
        return write(admin, directory, payload)

    monkeypatch.setattr(migration, "_write", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        migrate_teams(admin, apply=True, maintenance=True)
    assert source.exists() and len(admin.credential_admin.store.list_metadata()) == 2
    monkeypatch.setattr(migration, "_write", write)
    result = migrate_teams(admin, apply=True, maintenance=True)
    assert result["applied"] and not result["conflicts"]
    root = admin.runtime.provider_config.resolve_for_directory("root")
    child = admin.runtime.provider_config.resolve_for_directory("root/child")
    assert root.teams_delegated_credential_ref != child.teams_delegated_credential_ref
    assert not source.exists() and len(admin.credential_admin.store.list_metadata()) == 2
    assert migrate_teams(admin, apply=True, maintenance=True)["unchanged"]


@pytest.mark.parametrize(
    "drift", ["explicit_reference", "binding", "source", "unrelated_inherited_reference"]
)
def test_migration_retry_still_rejects_actual_drift(admin, monkeypatch, drift):
    import evander.credentials.migration as migration

    source = legacy_teams_source(admin)
    DirectoryService(admin.directory_store).create(parent="root", name="child")
    write_providers(admin, "root/child", teams_delegated_identity_id="identity-b")
    source.write_text(
        json.dumps(
            {
                identity: teams_values().model_dump(mode="json")
                for identity in ["identity-a", "identity-b"]
            }
        )
    )
    write = migration._write
    monkeypatch.setattr(
        migration, "_write", lambda *a: (_ for _ in ()).throw(RuntimeError("interrupt"))
    )
    with pytest.raises(RuntimeError):
        migrate_teams(admin, apply=True, maintenance=True)
    monkeypatch.setattr(migration, "_write", write)
    if drift == "explicit_reference":
        parent_record = next(
            r for r in admin.credential_admin.store.list_metadata() if r["label"] == "identity-a"
        )
        write_providers(admin, "root/child", teams_delegated_credential_ref=parent_record["id"])
    elif drift == "unrelated_inherited_reference":
        write_providers(admin, "root", teams_delegated_credential_ref="cred_" + "f" * 32)
    elif drift == "binding":
        write_providers(admin, "root/child", teams_graph_api_base_url="https://changed.test")
    else:
        source.write_text(source.read_text() + "\n")
    result = migrate_teams(admin, apply=True, maintenance=True)
    assert result["conflicts"] and not result["applied"] and source.exists()
