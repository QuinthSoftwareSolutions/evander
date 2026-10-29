from __future__ import annotations


from datetime import UTC, datetime, timedelta


import json


import multiprocessing


from pathlib import Path


import httpx


import pytest


from config_helpers import write_runtime_config


from evander.admin.composition import build_admin_runtime


from evander.config import GatewayConfig


from evander.credentials import CredentialError, CredentialStore


from evander.credentials.bindings import teams_binding


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


from connector_fixture import _config


@pytest.fixture
def custody(tmp_path, monkeypatch):
    key = tmp_path / "keys" / "master.key"
    monkeypatch.setenv("EVANDER_CREDENTIALS_KEY_FILE", str(key))
    store = CredentialStore(tmp_path / "runtime")
    store.initialize()
    return store


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
def test_first_time_managed_teams_accepts_empty_store_from_runtime_assembly(
    tmp_path, monkeypatch, operation, encrypted
):
    monkeypatch.setenv("EVANDER_CREDENTIALS_KEY_FILE", str(tmp_path / "keys/master.key"))
    runtime_dir = write_runtime_config(
        tmp_path,
        enable_directory_scoped_config=True,
        teams_backend="delegated_graph_api",
        teams_delegated_identity_id="identity-a",
        teams_delegated_client_id="client-a",
        teams_delegated_tenant_id="tenant-a",
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
        service.prepare_teams(
            "root", {"expected_config_revision": service.snapshot("root")["teams_revision"]}
        )
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
        service.prepare_teams(
            "root", {"expected_config_revision": service.snapshot("root")["teams_revision"]}
        )
    assert migrate_teams(admin, apply=True, maintenance=True)["conflicts"]
    assert service.store.list_metadata() == []
    assert json.loads(source.read_text()) == payload


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
