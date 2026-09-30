from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import multiprocessing
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from config_helpers import write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.admin.web.credentials import credential_routes
from evander.config import GatewayConfig
from evander.credentials import CredentialError, CredentialStore
from evander.credentials.bindings import connector_binding, teams_binding
from evander.credentials.migration import migrate_teams
from evander.credentials.teams import ManagedTeamsCredentialStore
from evander.directories import DirectoryService
from evander.messaging.teams_auth import (
    JsonTeamsIdentityCredentialStore,
    MicrosoftDeviceCodeAuthConfig,
    TeamsAuthCredentials,
    TeamsAuthenticationRequiredError,
    TeamsDeviceCodeTokenProvider,
)
from evander.connectors.models import ConnectorAuthProfile
from evander.connectors.drivers import ConnectorInvocationError
from evander.connectors.service import ConnectorPolicyError
from test_connectors import _config, _service, _session


@pytest.fixture
def custody(tmp_path, monkeypatch):
    key = tmp_path / "keys" / "master.key"
    monkeypatch.setenv("EVANDER_CREDENTIALS_KEY_FILE", str(key))
    store = CredentialStore(tmp_path / "runtime")
    store.initialize()
    return store


def managed_connector(custody, *, auth=None, values=None, result_mode="json"):
    config = _config(auth_profile="documents", result_mode=result_mode)
    if auth:
        config.auth_profiles["documents"] = ConnectorAuthProfile.model_validate(auth)
    profile = config.auth_profiles["documents"]
    binding = connector_binding("documents", "documents", profile, config.connectors["documents"])
    record = custody.create(
        owner="root/team",
        kind="connector",
        label="Documents",
        bindings=[binding],
        values=values or {"token": "synthetic-document-token"},
    )
    raw = profile.model_dump(mode="json", exclude_none=True)
    for field in (
        "token_env",
        "username_env",
        "password_env",
        "client_id_env",
        "client_secret_env",
    ):
        raw.pop(field, None)
    raw.update(type=profile.type.removesuffix("_env"), credential_ref=record["id"])
    config.auth_profiles["documents"] = ConnectorAuthProfile.model_validate(raw)
    return config, record


def test_managed_connector_use_rotate_disable_and_destination_binding(custody):
    config, record = managed_connector(custody)
    observed = []

    def handler(request):
        observed.append(request.headers["Authorization"])
        return httpx.Response(200, json={"ok": True})

    service = _service(custody.runtime_dir, config, rest_transport=httpx.MockTransport(handler))

    def invoke():
        return asyncio.run(
            service.invoke(
                session=_session(),
                connector_id="documents",
                operation_name="get_document",
                arguments={"id": "one"},
            )
        )

    assert invoke().value == {"ok": True}
    changed = custody.update(
        record["id"], expected_revision=record["revision"], values={"token": "replacement-token"}
    )
    invoke()
    assert observed == ["Bearer synthetic-document-token", "Bearer replacement-token"]
    config.connectors["documents"].config["base_url"] = "https://attacker.test"
    with pytest.raises(ConnectorPolicyError):
        invoke()
    config.connectors["documents"].config["base_url"] = "https://files.example.test"
    custody.update(record["id"], expected_revision=changed["revision"], status="disabled")
    with pytest.raises(ConnectorPolicyError):
        invoke()
    assert len(observed) == 2


@pytest.mark.parametrize("mode", ["json", "text", "artifact"])
def test_managed_connector_rejects_reflection_before_persistence(custody, mode):
    config, _ = managed_connector(custody, result_mode=mode)
    service = _service(
        custody.runtime_dir,
        config,
        rest_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'{"neutral":"synthetic-document-token"}')
        ),
    )
    with pytest.raises(ConnectorInvocationError, match="Managed connector request failed"):
        asyncio.run(
            service.invoke(
                session=_session(),
                connector_id="documents",
                operation_name="get_document",
                arguments={"id": "one"},
            )
        )
    for path in custody.runtime_dir.rglob("*"):
        if path.is_file():
            assert b"synthetic-document-token" not in path.read_bytes()


@pytest.mark.parametrize("protocol", ["basic", "oauth2_client_credentials"])
def test_managed_connector_allows_public_auth_identifiers_in_responses(custody, protocol):
    if protocol == "basic":
        auth = {"type": "basic_env", "username_env": "USER", "password_env": "PASSWORD"}
        values = {"username": "public-user", "password": "synthetic-password"}
    else:
        auth = {
            "type": protocol, "token_url": "https://login.example.test/token",
            "client_id_env": "CLIENT", "client_secret_env": "SECRET",
        }
        values = {"client_id": "public-user", "client_secret": "synthetic-client-secret"}
    config, _ = managed_connector(custody, auth=auth, values=values)
    service = _service(
        custody.runtime_dir, config,
        auth_transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"access_token": "synthetic-access-token", "expires_in": 3600},
        )),
        rest_transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"title": "XML export requested by public-user"},
        )),
    )
    result = asyncio.run(service.invoke(
        session=_session(), connector_id="documents", operation_name="get_document",
        arguments={"id": "one"},
    ))
    assert result.value == {"title": "XML export requested by public-user"}


@pytest.mark.parametrize("mode", ["json", "text", "artifact"])
@pytest.mark.parametrize("reflected", ["password", "basic_header", "client_secret", "access_token"])
def test_managed_connector_field_aware_checks_still_reject_secrets(custody, mode, reflected):
    import base64

    if reflected in {"password", "basic_header"}:
        auth = {"type": "basic_env", "username_env": "USER", "password_env": "PASSWORD"}
        values = {"username": "public-user", "password": "synthetic-password"}
        secret = values["password"] if reflected == "password" else base64.b64encode(
            b"public-user:synthetic-password"
        ).decode()
    else:
        auth = {
            "type": "oauth2_client_credentials", "token_url": "https://login.example.test/token",
            "client_id_env": "CLIENT", "client_secret_env": "SECRET",
        }
        values = {"client_id": "public-client", "client_secret": "synthetic-client-secret"}
        secret = values["client_secret"] if reflected == "client_secret" else "synthetic-access-token"
    config, _ = managed_connector(custody, auth=auth, values=values, result_mode=mode)
    service = _service(
        custody.runtime_dir, config,
        auth_transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"access_token": "synthetic-access-token", "expires_in": 3600},
        )),
        rest_transport=httpx.MockTransport(lambda request: httpx.Response(200, text=secret)),
    )
    with pytest.raises(ConnectorInvocationError, match="Managed connector request failed"):
        asyncio.run(service.invoke(
            session=_session(), connector_id="documents", operation_name="get_document",
            arguments={"id": "one"},
        ))
    for path in custody.runtime_dir.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize(
    "protocol", ["basic", "bearer", "api_key_header", "oauth-secret", "oauth-token"]
)
def test_host_auth_material_captures_private_values_and_allows_public_ids(
    custody, managed, protocol
):
    from evander.credentials.sanitize import reject_reflected_credentials

    secret, public = "synthetic-host-credential", "public-designer"
    if protocol == "basic":
        auth = {"type": "basic_env", "username_env": "WP_USER", "password_env": "WP_PASSWORD"}
        values = {"username": public, "password": secret}
        environ = {"WP_USER": public, "WP_PASSWORD": secret}
    elif protocol.startswith("oauth"):
        auth = {
            "type": "oauth2_client_credentials",
            "token_url": "https://login.example.test/token",
            "client_id_env": "WP_CLIENT",
            "client_secret_env": "WP_SECRET",
        }
        values = {"client_id": public, "client_secret": secret}
        environ = {"WP_CLIENT": public, "WP_SECRET": secret}
    else:
        auth = {"type": protocol + "_env", "token_env": "WP_TOKEN"}
        if protocol == "api_key_header":
            auth["header_name"] = "X-API-Key"
        values, environ = {"token": secret}, {"WP_TOKEN": secret}
    if managed:
        config, _ = managed_connector(custody, auth=auth, values=values)
    else:
        config = _config(auth_profile="documents")
        config.auth_profiles["documents"] = ConnectorAuthProfile.model_validate(auth)
    token_requests = []

    def token(request):
        token_requests.append(request)
        return httpx.Response(
            200, json={"access_token": "synthetic-derived-token", "expires_in": 3600}
        )

    service = _service(
        custody.runtime_dir, config, environ=environ, auth_transport=httpx.MockTransport(token)
    )
    response = {"value": "synthetic-derived-token" if protocol == "oauth-token" else secret}

    async def exercise():
        _, headers, sensitive = await service.host_auth_material("root/team", "documents")
        assert headers
        with pytest.raises(CredentialError):
            reject_reflected_credentials(response, sensitive)
        reject_reflected_credentials({"value": public}, sensitive)
        _, _, cached_sensitive = await service.host_auth_material("root/team", "documents")
        with pytest.raises(CredentialError):
            reject_reflected_credentials(response, cached_sensitive)
        if protocol.startswith("oauth"):
            assert len(token_requests) == 1, "Cached OAuth tokens still need their secret snapshot"

    asyncio.run(exercise())


def test_oauth_cache_observes_credential_revision_and_disable(custody):
    config, record = managed_connector(
        custody,
        auth={
            "type": "oauth2_client_credentials",
            "token_url": "https://login.example.test/token",
            "client_id_env": "CLIENT",
            "client_secret_env": "SECRET",
        },
        values={"client_id": "synthetic-client", "client_secret": "synthetic-client-secret"},
    )
    exchanges = []

    def token(request):
        exchanges.append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"access_token": "derived-token", "expires_in": 3600})

    service = _service(
        custody.runtime_dir,
        config,
        auth_transport=httpx.MockTransport(token),
        rest_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True})),
    )

    def invoke():
        return asyncio.run(
            service.invoke(
                session=_session(),
                connector_id="documents",
                operation_name="get_document",
                arguments={"id": "one"},
            )
        )

    invoke()
    invoke()
    assert len(exchanges) == 1
    replacement = custody.update(
        record["id"],
        expected_revision=record["revision"],
        values={"client_id": "synthetic-client", "client_secret": "new-client-secret"},
    )
    invoke()
    assert len(exchanges) == 2
    custody.update(record["id"], expected_revision=replacement["revision"], status="disabled")
    with pytest.raises(ConnectorPolicyError):
        invoke()


def teams_config(custody):
    return GatewayConfig(
        runtime_dir=custody.runtime_dir,
        teams_delegated_identity_id="identity-a",
        teams_delegated_client_id="client-a",
        teams_delegated_tenant_id="tenant-a",
    )


def teams_values(*, expired=False):
    return TeamsAuthCredentials(
        client_id="client-a",
        tenant_id="tenant-a",
        access_token="original-teams-token",
        refresh_token="original-refresh-token",
        scopes=["https://graph.microsoft.com/Chat.Read"],
        expires_at=datetime.now(UTC) + timedelta(seconds=-30 if expired else 3600),
    )


def create_teams(custody, *, expired=False):
    config = teams_config(custody)
    values = teams_values(expired=expired)
    record = custody.create(
        owner="root",
        kind="teams_oauth",
        label="Identity A",
        bindings=[teams_binding(config, "root")],
        values=values.model_dump(mode="json"),
    )
    config.teams_delegated_credential_ref = record["id"]
    return config, record


def teams_provider(config, transport):
    return TeamsDeviceCodeTokenProvider(
        MicrosoftDeviceCodeAuthConfig(client_id="client-a", tenant_id="tenant-a"),
        ManagedTeamsCredentialStore(config, "root"),
        client_factory=lambda timeout: httpx.Client(timeout=timeout, transport=transport),
    )


def test_teams_refresh_cannot_overwrite_logout_or_newer_login(custody):
    config, record = create_teams(custody, expired=True)

    def handler(request):
        custody.update(record["id"], expected_revision=record["revision"], values={})
        return httpx.Response(
            200,
            json={
                "access_token": "late-access",
                "refresh_token": "late-refresh",
                "expires_in": 3600,
            },
        )

    provider = teams_provider(config, httpx.MockTransport(handler))
    with pytest.raises(TeamsAuthenticationRequiredError):
        provider.get_bearer_token()
    values, _ = custody.resolve(
        record["id"], directory="root", binding=teams_binding(config, "root"), kind="teams_oauth"
    )
    assert values == {}


def _refresh_process(config_payload, counter, barrier, results):
    config = GatewayConfig.model_validate(config_payload)

    def handler(request):
        with counter.get_lock():
            counter.value += 1
        return httpx.Response(
            200,
            json={
                "access_token": "refreshed-access",
                "refresh_token": "refreshed-refresh",
                "expires_in": 3600,
            },
        )

    provider = teams_provider(config, httpx.MockTransport(handler))
    barrier.wait(timeout=10)
    results.put(provider.get_bearer_token())


def test_teams_refresh_serializes_across_processes(custody):
    config, _ = create_teams(custody, expired=True)
    context = multiprocessing.get_context("fork")
    counter, barrier, results = context.Value("i", 0), context.Barrier(2), context.Queue()
    processes = [
        context.Process(
            target=_refresh_process,
            args=(config.model_dump(mode="json"), counter, barrier, results),
        )
        for _ in range(2)
    ]
    try:
        for p in processes:
            p.start()
        assert [results.get(timeout=15) for _ in processes] == ["refreshed-access"] * 2
        for p in processes:
            p.join(timeout=5)
            assert p.exitcode == 0
        assert counter.value == 1
    finally:
        for p in processes:
            if p.is_alive():
                p.terminate()
            p.join(timeout=5)


class Auth:
    locked = False
    forbidden = False

    def require(self, request):
        return {"login": "owner"}

    def require_permission(self, request, permission, directory_path=None):
        if self.forbidden:
            raise PermissionError()

    def privileged_for_request(self, request):
        return not self.locked


@pytest.fixture
def admin(tmp_path, monkeypatch):
    key = tmp_path / "keys" / "master.key"
    monkeypatch.setenv("EVANDER_CREDENTIALS_KEY_FILE", str(key))
    runtime_dir = write_runtime_config(tmp_path / "runtime", enable_directory_scoped_config=True)
    runtime = build_admin_runtime(runtime_dir)
    runtime.credential_admin.store.initialize()
    config = _config(auth_profile="documents").model_dump(mode="json", exclude_none=True)
    runtime.operations_admin.write_directory_domain_config("root", "connectors", config)
    return runtime


def test_admin_api_creates_replaces_and_checks_revision_without_values(admin):
    auth = Auth()
    with TestClient(Starlette(routes=credential_routes(admin, auth))) as client:
        snapshot = client.get("/api/admin/credentials").json()
        payload = {
            "profile_id": "documents",
            "values": {"token": "ui-synthetic-secret"},
            "expected_config_revision": snapshot["connector_revision"],
        }
        response = client.post("/api/admin/credentials", json=payload)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert "ui-synthetic-secret" not in response.text
        record = response.json()["credentials"][0]
        response = client.put(
            "/api/admin/credentials/" + record["id"],
            json={
                "action": "replace",
                "values": {"token": "replacement-ui-secret"},
                "expected_revision": record["revision"],
            },
        )
        assert response.status_code == 200, response.text
        assert "replacement-ui-secret" not in response.text
        conflict = client.put(
            "/api/admin/credentials/" + record["id"],
            json={"action": "disable", "expected_revision": record["revision"]},
        )
        assert conflict.status_code == 409
        auth.forbidden = True
        assert client.get("/api/admin/credentials").status_code == 403


def test_teams_migration_is_idempotent_and_retires_plaintext(admin):
    from evander.credentials.migration import _overlay

    cfg = teams_config(admin.credential_admin.store)
    overlay = _overlay(admin.credential_admin, "root")
    overlay.update(
        teams_backend="delegated_graph_api",
        teams_delegated_identity_id="identity-a",
        teams_delegated_client_id="client-a",
        teams_delegated_tenant_id="tenant-a",
    )
    admin.operations_admin.write_directory_domain_config("root", "providers", overlay)
    cfg = admin.runtime.provider_config.resolve_for_directory("root")
    source = Path(cfg.teams_delegated_credential_store_path)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({"identity-a": teams_values().model_dump(mode="json")}))
    before = source.read_bytes()
    preview = migrate_teams(admin)
    assert not preview["conflicts"] and not preview["applied"]
    assert source.read_bytes() == before
    applied = migrate_teams(admin, apply=True, maintenance=True)
    assert applied["applied"], applied
    assert not source.exists()
    again = migrate_teams(admin, apply=True, maintenance=True)
    assert again["identities"][0]["migrated"]
    assert len(admin.credential_admin.store.list_metadata()) == 1
    assert list(admin.credential_admin.store.root.glob("teams-migration-recovery-*.db"))


def test_teams_migration_imports_transitional_privacy_envelope(admin):
    source = legacy_teams_source(admin)
    legacy_store = JsonTeamsIdentityCredentialStore(source, "identity-a")
    expected = legacy_store.load()
    assert expected is not None and expected.access_token == "original-teams-token"
    serialized = json.loads(source.read_text())
    assert serialized["format"] == "evander.teams-credentials.fernet.v1"
    key_path = legacy_store.key_path
    before = source.read_bytes()

    preview = migrate_teams(admin)
    assert not preview["conflicts"] and not preview["applied"]
    assert source.read_bytes() == before

    applied = migrate_teams(admin, apply=True, maintenance=True)
    assert applied["applied"] and not source.exists() and not key_path.exists()
    config = admin.runtime.provider_config.resolve_for_directory("root")
    values, _ = admin.credential_admin.store.resolve(
        config.teams_delegated_credential_ref,
        directory="root",
        binding=teams_binding(config, "root"),
        kind="teams_oauth",
    )
    assert TeamsAuthCredentials.model_validate(values) == expected


def test_teams_migration_rejects_transitional_envelope_without_key(admin):
    source = legacy_teams_source(admin)
    legacy_store = JsonTeamsIdentityCredentialStore(source, "identity-a")
    legacy_store.load()
    legacy_store.key_path.unlink()
    before = source.read_bytes()

    with pytest.raises(CredentialError, match="format or identity mapping"):
        migrate_teams(admin)

    assert source.read_bytes() == before
    assert admin.credential_admin.store.list_metadata() == []


@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("operation", ["prepare", "migrate"])
def test_first_time_managed_teams_accepts_empty_store_from_runtime_assembly(tmp_path, monkeypatch, operation, encrypted):
    monkeypatch.setenv("EVANDER_CREDENTIALS_KEY_FILE", str(tmp_path / "keys/master.key"))
    runtime_dir = write_runtime_config(
        tmp_path, enable_directory_scoped_config=True,
        teams_backend="delegated_graph_api", teams_delegated_identity_id="identity-a",
        teams_delegated_client_id="client-a", teams_delegated_tenant_id="tenant-a",
    )
    admin = build_admin_runtime(runtime_dir)
    service = admin.credential_admin
    service.store.initialize()
    config = admin.runtime.provider_config.resolve_for_directory("root")
    source = Path(config.teams_delegated_credential_store_path)
    assert json.loads(source.read_text()) == {}
    if encrypted:
        JsonTeamsIdentityCredentialStore(source, "identity-a").clear()
    before = source.read_bytes()
    if operation == "prepare":
        service.prepare_teams("root", {"expected_config_revision": service.snapshot("root")["teams_revision"]})
        assert source.read_bytes() == before
    else:
        assert not migrate_teams(admin)["conflicts"]
        assert source.read_bytes() == before
        assert migrate_teams(admin, apply=True, maintenance=True)["applied"]
        assert not source.exists()
    reloaded = build_admin_runtime(runtime_dir)
    config = reloaded.runtime.provider_config.resolve_for_directory("root")
    assert config.teams_delegated_credential_ref
    assert ManagedTeamsCredentialStore(config, "root").load() is None
    assert len(service.store.list_metadata()) == 1


def test_empty_teams_setup_does_not_ignore_unmigrated_other_identity(admin):
    source = legacy_teams_source(admin)
    payload = json.loads(source.read_text())
    payload["other-identity"] = payload.pop("identity-a")
    source.write_text(json.dumps(payload))
    service = admin.credential_admin
    with pytest.raises(CredentialError, match="maintenance migration"):
        service.prepare_teams("root", {"expected_config_revision": service.snapshot("root")["teams_revision"]})
    assert migrate_teams(admin, apply=True, maintenance=True)["conflicts"]
    assert service.store.list_metadata() == []
    assert json.loads(source.read_text()) == payload


def create_admin_connector(admin, directory="root"):
    service = admin.credential_admin
    return service.create_connector(
        directory,
        {
            "profile_id": "documents",
            "values": {"token": "synthetic-admin-token"},
            "expected_config_revision": service.snapshot(directory)["connector_revision"],
        },
    )["credentials"][-1]


def test_inherited_connector_reference_cannot_be_redeclared_or_retargeted(admin):
    DirectoryService(admin.directory_store).create(parent="root", name="team")
    record = create_admin_connector(admin)
    service = admin.credential_admin.connectors._deps.connector_service
    config = admin.runtime.provider_config.resolve_connectors_for_directory("root/team")
    assert service.auth_status(config, config.connectors["documents"], directory_path="root/team")[
        "available"
    ]
    inherited = admin.credential_admin.snapshot("root/team")["credentials"][0]
    assert not inherited["local"] and inherited["bindings"] == []
    with pytest.raises(CredentialError, match="owning directory"):
        admin.credential_admin.update(
            "root/team",
            record["id"],
            {
                "action": "disable",
                "expected_revision": record["revision"],
            },
        )
    snapshot = admin.credential_admin.connectors.snapshot("root/team")
    with pytest.raises(ValueError, match="owning directory"):
        admin.credential_admin.connectors.save(
            "root/team",
            expected_revision=snapshot["revision"],
            config_payload={"auth_profiles": {"documents": {"credential_ref": record["id"]}}},
        )
    admin.credential_admin.connectors.save(
        "root/team",
        expected_revision=snapshot["revision"],
        config_payload={
            "connectors": {"documents": {"config": {"base_url": "https://unapproved.test"}}}
        },
    )
    config = admin.runtime.provider_config.resolve_connectors_for_directory("root/team")
    assert not service.auth_status(
        config, config.connectors["documents"], directory_path="root/team"
    )["available"]
    # The child can supply its own credential and approve its own destination.
    local = create_admin_connector(admin, "root/team")
    assert local["owner_directory"] == "root/team"
    config = admin.runtime.provider_config.resolve_connectors_for_directory("root/team")
    assert service.auth_status(config, config.connectors["documents"], directory_path="root/team")[
        "available"
    ]


def test_child_can_migrate_inherited_environment_profile_without_changing_parent(admin):
    DirectoryService(admin.directory_store).create(parent="root", name="team")
    create_admin_connector(admin, "root/team")
    parent = admin.runtime.provider_config.resolve_connectors_for_directory("root")
    child = admin.runtime.provider_config.resolve_connectors_for_directory("root/team")
    assert parent.auth_profiles["documents"].type == "bearer_env"
    assert child.auth_profiles["documents"].type == "bearer"
    assert child.auth_profiles["documents"].token_env is None


@pytest.mark.parametrize("entrypoint", ["api", "cli", "preview"])
def test_parent_connector_conversion_rejects_incompatible_child_without_changes(admin, monkeypatch, entrypoint):
    from typer.testing import CliRunner
    from evander.cli import app
    from evander.credentials.migration import migrate_connector

    DirectoryService(admin.directory_store).create(parent="root", name="team")
    admin.credential_admin.connectors.save(
        "root/team", expected_revision=admin.credential_admin.connectors.snapshot("root/team")["revision"],
        config_payload={"auth_profiles": {"documents": {"token_env": "CHILD_TOKEN"}}},
    )
    parent = admin.credential_admin.connectors.snapshot("root")
    child = admin.credential_admin.connectors.snapshot("root/team")
    profile = admin.runtime.provider_config.resolve_connectors_for_directory("root").auth_profiles["documents"]
    monkeypatch.setenv(profile.token_env, "synthetic-parent-token")
    monkeypatch.setenv("CHILD_TOKEN", "synthetic-child-token")
    if entrypoint == "api":
        with TestClient(Starlette(routes=credential_routes(admin, Auth()))) as client:
            response = client.post("/api/admin/credentials", json={
                "profile_id": "documents", "values": {"token": "synthetic-parent-token"},
                "expected_config_revision": parent["revision"],
            })
        assert response.status_code == 409, response.text
        message = response.text
    elif entrypoint == "cli":
        result = CliRunner().invoke(app, [
            "credentials", "migrate", "--family", "connector", "--directory", "root",
            "--profile-id", "documents", "--runtime-dir", str(admin.config.runtime_dir),
            "--apply", "--maintenance",
        ])
        assert result.exit_code == 1, result.output
        message = result.output
    else:
        result = migrate_connector(admin, directory="root", profile_id="documents")
        assert result["conflicts"] and not result["applied"]
        message = json.dumps(result)
    assert "root/team" in message and "documents" in message
    assert "credential_ref" in message and "null" in message
    assert "synthetic-parent-token" not in message and "synthetic-child-token" not in message
    assert admin.credential_admin.store.list_metadata() == []
    assert admin.credential_admin.connectors.snapshot("root")["config"] == parent["config"]
    assert admin.credential_admin.connectors.snapshot("root/team")["config"] == child["config"]
    assert admin.runtime.provider_config.resolve_connectors_for_directory("root/team").auth_profiles["documents"].token_env == "CHILD_TOKEN"


def test_parent_conversion_preserves_child_migrated_first(admin):
    DirectoryService(admin.directory_store).create(parent="root", name="team")
    child = create_admin_connector(admin, "root/team")
    parent = create_admin_connector(admin)
    assert parent["id"] != child["id"]
    assert admin.runtime.provider_config.resolve_connectors_for_directory("root/team").auth_profiles["documents"].credential_ref == child["id"]


def test_parent_conversion_preserves_explicit_child_environment_profile(admin, monkeypatch):
    DirectoryService(admin.directory_store).create(parent="root", name="team")
    overlay = {"auth_profiles": {"documents": {
        "type": "bearer_env", "token_env": "CHILD_TOKEN", "credential_ref": None,
    }}}
    admin.credential_admin.connectors.save(
        "root/team", expected_revision=admin.credential_admin.connectors.snapshot("root/team")["revision"],
        config_payload=overlay,
    )
    create_admin_connector(admin)
    child = admin.runtime.provider_config.resolve_connectors_for_directory("root/team")
    assert child.auth_profiles["documents"].credential_ref is None
    assert child.auth_profiles["documents"].token_env == "CHILD_TOKEN"
    monkeypatch.setenv("CHILD_TOKEN", "synthetic-child-token")
    service = admin.credential_admin.connectors._deps.connector_service
    assert service.auth_status(child, child.connectors["documents"], directory_path="root/team")["available"]
    assert admin.credential_admin.connectors.snapshot("root/team")["config"] == overlay


@pytest.mark.parametrize("change", ["auth_override", "destination"])
def test_parent_conversion_rechecks_descendants_before_publishing(admin, monkeypatch, change):
    from evander.admin.services.credential_admin_service import CredentialConversionConflict

    DirectoryService(admin.directory_store).create(parent="root", name="team")
    before = admin.credential_admin.connectors.snapshot("root")["config"]
    create = admin.credential_admin.store.create

    def concurrent_child_edit(**kwargs):
        record = create(**kwargs)
        overlay = (
            {"auth_profiles": {"documents": {"token_env": "CHILD_TOKEN"}}}
            if change == "auth_override" else
            {"connectors": {"documents": {"config": {"base_url": "https://child.example.test"}}}}
        )
        admin.credential_admin.connectors.save(
            "root/team", expected_revision=admin.credential_admin.connectors.snapshot("root/team")["revision"],
            config_payload=overlay,
        )
        return record

    monkeypatch.setattr(admin.credential_admin.store, "create", concurrent_child_edit)
    with pytest.raises(CredentialConversionConflict, match="root/team"):
        create_admin_connector(admin)
    assert admin.credential_admin.connectors.snapshot("root")["config"] == before
    assert all(row["status"] == "deleted" for row in admin.credential_admin.store.list_metadata())
    assert admin.credential_admin.connectors.snapshot("root/team")["config"]


def test_conversion_validates_grandchild_overlays_when_parent_profile_is_inherited(admin):
    from evander.admin.services.credential_admin_service import CredentialConversionConflict

    directories = DirectoryService(admin.directory_store)
    directories.create(parent="root", name="team")
    directories.create(parent="root/team", name="child")
    admin.credential_admin.connectors.save(
        "root/team/child", expected_revision=admin.credential_admin.connectors.snapshot("root/team/child")["revision"],
        config_payload={"auth_profiles": {"documents": {"token_env": "CHILD_TOKEN"}}},
    )
    with pytest.raises(CredentialConversionConflict, match="root/team/child"):
        create_admin_connector(admin, "root/team")
    assert admin.credential_admin.store.list_metadata() == []


def test_api_lock_keyloss_and_malformed_inputs_never_return_values(admin):
    auth = Auth()
    with TestClient(Starlette(routes=credential_routes(admin, auth))) as client:
        snapshot = client.get("/api/admin/credentials").json()
        payload = {
            "profile_id": "documents",
            "values": {"token": "synthetic\nunsafe-value"},
            "expected_config_revision": snapshot["connector_revision"],
        }
        response = client.post("/api/admin/credentials", json=payload)
        assert response.status_code == 400 and "unsafe-value" not in response.text
        auth.locked = True
        assert client.post("/api/admin/credentials", json=payload).status_code == 403
        auth.locked = False
        assert client.post("/api/admin/credentials", content=b"x" * 131073).status_code == 413
        assert client.post("/api/admin/credentials", json={}).status_code == 428
        admin.credential_admin.store.resolved_key_file().unlink()
        response = client.get("/api/admin/credentials")
        assert response.status_code == 200 and not response.json()["store"]["available"]


def test_late_device_login_does_not_replace_newer_credentials(custody):
    config, record = create_teams(custody)

    def handler(request):
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
        return httpx.Response(200, json={"access_token": "late-device-token", "expires_in": 3600})

    provider = teams_provider(config, httpx.MockTransport(handler))
    session = provider.begin_device_code_login()
    custody.update(record["id"], expected_revision=record["revision"], values={})
    with pytest.raises(TeamsAuthenticationRequiredError):
        provider.poll_device_code_login_once(session)
    assert ManagedTeamsCredentialStore(config, "root").load() is None


@pytest.mark.parametrize("invalid_success", [False, True])
def test_managed_teams_errors_discard_reflected_token_payloads(custody, invalid_success):
    from evander.messaging.teams_auth import TeamsAuthError

    config, _ = create_teams(custody, expired=True)
    payload = (
        {"access_token": "reflected-token-canary", "expires_in": "bad"}
        if invalid_success
        else {
            "error": "invalid_grant",
            "error_description": "original-refresh-token",
            "value": "reflected-token-canary",
        }
    )
    provider = teams_provider(
        config,
        httpx.MockTransport(
            lambda request: httpx.Response(200 if invalid_success else 400, json=payload)
        ),
    )
    with pytest.raises(TeamsAuthError) as caught:
        provider.get_bearer_token()
    assert "token-canary" not in str(caught.value) and "original-refresh-token" not in str(
        caught.value
    )
    assert caught.value.payload == {} and caught.value.error_description is None


def legacy_teams_source(admin):
    from evander.credentials.migration import _overlay

    overlay = _overlay(admin.credential_admin, "root")
    overlay.update(
        teams_backend="delegated_graph_api",
        teams_delegated_identity_id="identity-a",
        teams_delegated_client_id="client-a",
        teams_delegated_tenant_id="tenant-a",
    )
    admin.operations_admin.write_directory_domain_config("root", "providers", overlay)
    config = admin.runtime.provider_config.resolve_for_directory("root")
    source = Path(config.teams_delegated_credential_store_path)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({"identity-a": teams_values().model_dump(mode="json")}))
    return source


def test_teams_migration_backup_failure_preserves_source_and_references_for_retry(
    admin, monkeypatch
):
    import evander.credentials.migration as migration

    source = legacy_teams_source(admin)
    before = source.read_bytes()

    def fail_backup(*args):
        raise OSError("synthetic backup failure")

    with monkeypatch.context() as patch:
        patch.setattr(migration, "encrypted_backup", fail_backup)
        with pytest.raises(OSError, match="synthetic backup failure"):
            migrate_teams(admin, apply=True, maintenance=True)

    config = admin.runtime.provider_config.resolve_for_directory("root")
    assert not config.teams_delegated_credential_ref
    assert source.read_bytes() == before
    imported = admin.credential_admin.store.list_metadata()
    assert len(imported) == 1

    result = migrate_teams(admin, apply=True, maintenance=True)
    config = admin.runtime.provider_config.resolve_for_directory("root")
    assert result["applied"] and not source.exists()
    assert config.teams_delegated_credential_ref == imported[0]["id"]
    assert len(admin.credential_admin.store.list_metadata()) == 1


def test_teams_migration_resumes_after_interrupted_config_switch(admin, monkeypatch):
    import evander.credentials.migration as migration

    source = legacy_teams_source(admin)
    write = migration._write

    def interrupt(*args):
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(migration, "_write", interrupt)
    with pytest.raises(RuntimeError):
        migrate_teams(admin, apply=True, maintenance=True)
    assert source.exists() and len(admin.credential_admin.store.list_metadata()) == 1
    assert list(admin.credential_admin.store.root.glob("teams-migration-recovery-*.db"))
    monkeypatch.setattr(migration, "_write", write)
    assert migrate_teams(admin, apply=True, maintenance=True)["applied"]
    assert not source.exists() and len(admin.credential_admin.store.list_metadata()) == 1


def test_teams_migration_conflict_keeps_source_and_configuration(admin):
    source = legacy_teams_source(admin)
    DirectoryService(admin.directory_store).create(parent="root", name="team")
    admin.operations_admin.write_directory_domain_config(
        "root/team", "providers", {"teams_delegated_client_id": "different-client"}
    )
    before = source.read_bytes()
    report = migrate_teams(admin, apply=True, maintenance=True)
    assert report["conflicts"] and not report["applied"]
    assert source.read_bytes() == before
    assert admin.credential_admin.store.list_metadata() == []


def test_connector_cli_migration_is_dry_by_default_and_idempotent(admin, monkeypatch):
    from evander.credentials.migration import migrate_connector

    profile = admin.runtime.provider_config.resolve_connectors_for_directory("root").auth_profiles[
        "documents"
    ]
    monkeypatch.setenv(profile.token_env, "synthetic-imported-token")
    assert not migrate_connector(admin, directory="root", profile_id="documents")["applied"]
    assert admin.credential_admin.store.list_metadata() == []
    report = migrate_connector(admin, directory="root", profile_id="documents", apply=True)
    assert report["applied"] and Path(report["recovery_snapshot"]).exists()
    assert "synthetic-imported-token" not in json.dumps(report)
    assert migrate_connector(admin, directory="root", profile_id="documents", apply=True)[
        "migrated"
    ]
    assert len(admin.credential_admin.store.list_metadata()) == 1


def test_connector_consumer_approval_checks_preview_revision(admin):
    record = create_admin_connector(admin)
    before = admin.credential_admin.snapshot("root")["credentials"][0]
    connectors = admin.credential_admin.connectors
    snapshot = connectors.snapshot("root")
    payload = snapshot["config"]
    payload["connectors"]["documents"]["config"]["base_url"] = "https://changed.test"
    connectors.save("root", expected_revision=snapshot["revision"], config_payload=payload)
    request = {
        "action": "approve_bindings",
        "expected_revision": record["revision"],
        "expected_bindings_revision": before["bindings_revision"],
    }
    from evander.credentials import CredentialConflict

    with pytest.raises(CredentialConflict):
        admin.credential_admin.update("root", record["id"], request)
    current = admin.credential_admin.snapshot("root")["credentials"][0]
    assert current["current_bindings"][0]["destination"] == "https://changed.test"
    request["expected_bindings_revision"] = current["bindings_revision"]
    admin.credential_admin.update("root", record["id"], request)
    config = admin.runtime.provider_config.resolve_connectors_for_directory("root")
    service = connectors._deps.connector_service
    assert service.auth_status(config, config.connectors["documents"])["available"]


def test_teams_binding_approval_requires_new_signin(admin):
    legacy_teams_source(admin)
    migrate_teams(admin, apply=True, maintenance=True)
    record = admin.credential_admin.snapshot("root")["credentials"][0]
    from evander.credentials.migration import _overlay

    overlay = _overlay(admin.credential_admin, "root")
    overlay["teams_delegated_scopes"] = ["https://graph.microsoft.com/Chat.Read"]
    admin.operations_admin.write_directory_domain_config("root", "providers", overlay)
    current = admin.credential_admin.snapshot("root")["credentials"][0]
    admin.credential_admin.update(
        "root",
        record["id"],
        {
            "action": "approve_bindings",
            "expected_revision": record["revision"],
            "expected_bindings_revision": current["bindings_revision"],
        },
    )
    config = admin.runtime.provider_config.resolve_for_directory("root")
    assert ManagedTeamsCredentialStore(config, "root").load() is None


def test_teams_descendant_can_refresh_but_cannot_signout_shared_identity(custody):
    config, record = create_teams(custody)
    custody.update(
        record["id"],
        expected_revision=record["revision"],
        bindings=[teams_binding(config, "root"), teams_binding(config, "root/team")],
    )
    child = ManagedTeamsCredentialStore(config, "root/team")
    assert child.load().access_token == "original-teams-token"
    with pytest.raises(TeamsAuthenticationRequiredError):
        child.clear()
    with pytest.raises(TeamsAuthenticationRequiredError):
        child.require_owner()
    with pytest.raises(TeamsAuthenticationRequiredError):
        child.save(teams_values())
    child.save(teams_values(), refresh=True)
    assert ManagedTeamsCredentialStore(config, "root").load().access_token == "original-teams-token"


def test_teams_ingest_resolves_managed_credential_in_consumer_directory(custody):
    from evander.messaging.teams_ingest import build_teams_ingest_worker

    config = teams_config(custody)
    config.teams_backend = "delegated_graph_api"
    record = custody.create(
        owner="root/team",
        kind="teams_oauth",
        label="Teams",
        values=teams_values().model_dump(mode="json"),
        bindings=[teams_binding(config, "root/team")],
    )
    config.teams_delegated_credential_ref = record["id"]
    worker = build_teams_ingest_worker(config, project_dir=custody.runtime_dir.parent)
    service = worker._build_graph_service(
        provider_config=config, channel_targets=[], directory_path="root/team"
    )
    assert service.token_provider() == "original-teams-token"


def test_teams_reference_separates_subscription_and_polling_identity(custody):
    from evander.messaging.teams_ingest import _teams_graph_poll_scope, _teams_user_cache_namespace
    from evander.messaging.teams_subscriptions import teams_subscription_provider_identity_key

    config = teams_config(custody)
    config.teams_backend = "delegated_graph_api"
    config.teams_delegated_credential_ref = "cred_" + "1" * 32
    other = config.model_copy(update={"teams_delegated_credential_ref": "cred_" + "2" * 32})
    assert teams_subscription_provider_identity_key(
        config
    ) != teams_subscription_provider_identity_key(other)
    for resolve in (_teams_graph_poll_scope, _teams_user_cache_namespace):
        assert resolve(config, directory_path="root") != resolve(other, directory_path="root")


@pytest.mark.parametrize(
    ("auth", "values", "header", "expected"),
    [
        (
            {
                "type": "api_key_header_env",
                "token_env": "DOCUMENTS_TOKEN",
                "header_name": "X-Documents-Key",
            },
            {"token": "synthetic-api-key"},
            "X-Documents-Key",
            "synthetic-api-key",
        ),
        (
            {
                "type": "basic_env",
                "username_env": "DOCUMENTS_USER",
                "password_env": "DOCUMENTS_PASSWORD",
            },
            {"username": "synthetic-user", "password": "synthetic-password"},
            "Authorization",
            "Basic c3ludGhldGljLXVzZXI6c3ludGhldGljLXBhc3N3b3Jk",
        ),
    ],
)
def test_managed_basic_and_api_key_headers_never_fall_back_to_environment(
    custody, auth, values, header, expected
):
    config, _ = managed_connector(custody, auth=auth, values=values)
    observed = []

    def handler(request):
        observed.append(request.headers[header])
        return httpx.Response(200, json={"ok": True})

    service = _service(
        custody.runtime_dir,
        config,
        rest_transport=httpx.MockTransport(handler),
        environ={
            "DOCUMENTS_TOKEN": "ambient-token",
            "DOCUMENTS_USER": "ambient-user",
            "DOCUMENTS_PASSWORD": "ambient-password",
        },
    )

    def invoke():
        return asyncio.run(
            service.invoke(
                session=_session(),
                connector_id="documents",
                operation_name="get_document",
                arguments={"id": "one"},
            )
        )

    assert invoke().value == {"ok": True}
    assert observed == [expected]
    custody.resolved_key_file().unlink()
    with pytest.raises(ConnectorPolicyError):
        invoke()
    assert observed == [expected]
