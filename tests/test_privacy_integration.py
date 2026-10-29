"""Privacy integration through actual configuration, composition, CLI and web auth."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json

import httpx
import pytest
from typer.testing import CliRunner

from config_helpers import write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.admin.services.privacy_service import PrivacyAdminService
from evander.admin.web.app import create_app
from evander.bootstrap import load_runtime_gateway_config
from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.directory_config import DirectoryConfigStore
from evander.privacy.cli import app as privacy_cli
from evander.privacy.models import PrivacyError
from evander.privacy.providers import ProviderApproval
from evander.provider_config import ProviderConfigResolver


@pytest.fixture(autouse=True)
def isolated_host_identity(monkeypatch):
    monkeypatch.setenv("EVANDER_GIT_USER", "evander-privacy-tests-no-such-user")


def approval(label="one") -> dict:
    return ProviderApproval(
        approval_id=label, provider="openai", connection_id=f"connection-{label}",
        endpoint="https://api.openai.com/v1", allowed_categories=["code"],
        capabilities=["model_relay"], legal_review_reference="legal/reference",
        approved_by="owner", status="approved",
    ).model_dump(mode="json")


def request_fields() -> dict:
    return {
        "request_type": "access", "controller_ref": "customer/reference",
        "instruction_ref": "controller/instruction", "subject_keys": {"session_id": "one"},
        "deadline_at": (datetime.now(UTC) + timedelta(days=20)).isoformat(),
    }


def test_privacy_domain_roundtrip_and_child_inheritance(tmp_path):
    resolver = ProviderConfigResolver(GatewayConfig(runtime_dir=tmp_path))
    resolver.directory_service.create(parent="root", name="team")
    payload = {"privacy": {
        "enabled_categories": ["code"], "export_ttl_days": 10,
        "provider_policy": {"approvals": [approval()]},
        "retention": {"communication_artifacts": {
            "approved": True, "approval_reference": "legal/retention", "scheduled": False,
        }},
    }}
    assert resolver.config_store.write("root", domain="privacy", payload=payload) == payload
    resolver.config_store.write("root/team", domain="privacy", payload={"privacy": {
        "export_ttl_days": 3, "provider_policy": {"approvals": []},
    }})
    effective = resolver.resolve_for_directory("root/team").privacy
    assert effective.export_ttl_days == 3
    assert effective.enabled_categories == ["code"]
    assert effective.retention["communication_artifacts"].approval_reference == "legal/retention"
    assert effective.provider_policy.approvals == []
    assert resolver.resolve_for_directory("root").privacy.provider_policy.approvals[0].approval_id == "one"
    path = tmp_path / "directory-config/root/team/privacy.json"
    assert json.loads(path.read_text()) == {"privacy": {
        "export_ttl_days": 3, "provider_policy": {"approvals": []},
    }}


@pytest.mark.parametrize("replacement", [{}, {"privacy": {"provider_policy": {"approvals": []}}}])
def test_removed_privacy_approvals_are_not_resurrected_from_cached_gateway(tmp_path, replacement):
    resolver = ProviderConfigResolver(GatewayConfig(runtime_dir=tmp_path))
    resolver.directory_service.create(parent="root", name="team")
    resolver.config_store.write("root", domain="privacy", payload={"privacy": {
        "enabled_categories": ["code"], "provider_policy": {"approvals": [approval()]},
    }})
    previously_resolved = resolver.resolve_for_directory("root")
    assert previously_resolved.privacy.provider_policy.approvals
    cached = ProviderConfigResolver(previously_resolved)
    resolver.config_store.write("root", domain="privacy", payload=replacement)
    for instance in (resolver, cached, ProviderConfigResolver(previously_resolved)):
        for scope in ("root", "root/team"):
            assert instance.resolve_for_directory(scope).privacy.provider_policy.approvals == []


def test_root_bootstrap_loads_privacy_domain_and_fresh_updates(tmp_path):
    runtime_dir = write_runtime_config(tmp_path, privacy={
        "enabled_categories": ["code"], "export_ttl_days": 5,
        "provider_policy": {"approvals": [approval()]},
    })
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    assert config.privacy.export_ttl_days == 5
    assert config.privacy.enabled_categories == ["code"]
    resolver = ProviderConfigResolver(config)
    resolver.config_store.write("root", domain="privacy", payload={"privacy": {
        "export_ttl_days": 2, "provider_policy": {"approvals": [approval("two")]},
    }})
    fresh = load_runtime_gateway_config(runtime_dir=runtime_dir)
    assert fresh.privacy.export_ttl_days == 2
    assert fresh.privacy.provider_policy.approvals[0].approval_id == "two"
    assert resolver.resolve_for_directory("root").privacy.provider_policy.approvals[0].approval_id == "two"


@pytest.mark.parametrize("payload", [
    {"unknown_domain_field": True},
    {"privacy": {"unexpected_setting": True}},
    {"privacy": {"provider_policy": {"approvals": [approval(), approval()]}}},
    {"privacy": {"retention": {"communication_artifacts": {"scheduled": True}}}},
])
def test_privacy_domain_rejects_invalid_grants_and_settings(payload):
    with pytest.raises(ValueError):
        DirectoryConfigStore.validate_domain_payload("privacy", payload)


def test_real_composition_privacy_configuration_persists_and_reloads(tmp_path):
    runtime_dir = write_runtime_config(tmp_path, enable_directory_scoped_config=True)
    components = build_admin_runtime(runtime_dir)
    service = components.privacy_admin
    assert isinstance(service, PrivacyAdminService)
    assert service.operations is components.operations_admin
    assert service.resolver is components.runtime.provider_config
    before = service.snapshot("root")
    result = service.mutate("configure", "root", {"config": {
        "enabled_categories": ["code"], "export_ttl_days": 4,
        "provider_policy": {"approvals": [approval()]},
    }}, expected_revision=before["revision"], actor="integration-owner")
    assert result["result"]["status"] == "published"
    assert result["revision"] != before["revision"]
    assert result["effective_config"]["export_ttl_days"] == 4
    reloaded = build_admin_runtime(runtime_dir).privacy_admin.snapshot("root")
    assert reloaded["effective_config"] == result["effective_config"]
    assert reloaded["coverage_complete"] is False
    assert reloaded["readiness"]["ready"] is False


def test_privacy_publication_preserves_existing_directory_write_feature_gate(tmp_path):
    runtime_dir = write_runtime_config(tmp_path, enable_directory_scoped_config=False)
    components = build_admin_runtime(runtime_dir)
    before = components.privacy_admin.snapshot("root")
    with pytest.raises(PrivacyError):
        components.privacy_admin.mutate("configure", "root", {"config": {
            "export_ttl_days": 4,
        }}, expected_revision=before["revision"], actor="integration-owner")
    after = components.privacy_admin.snapshot("root")
    assert after["revision"] == before["revision"]
    assert after["effective_config"] == before["effective_config"]


def test_real_cli_persists_scoped_case_and_rejects_stale_revision(tmp_path, monkeypatch):
    runtime_dir = write_runtime_config(tmp_path)
    components = build_admin_runtime(runtime_dir)
    DirectoryService(components.runtime.directories).create(parent="root", name="team")
    runner = CliRunner()
    args = ["--config", str(runtime_dir), "--directory-path", "root/team"]
    before = runner.invoke(privacy_cli, ["status", *args])
    assert before.exit_code == 0, before.output
    snapshot = json.loads(before.output)
    payload_file = tmp_path / "case.json"
    payload_file.write_text(json.dumps(request_fields()))
    monkeypatch.setenv("USER", "forged-actor")
    command = ["action", "cases.create", *args, "--payload-file", str(payload_file),
               "--expected-revision", snapshot["revision"]]
    created = runner.invoke(privacy_cli, command)
    assert created.exit_code == 0, created.output
    case_id = json.loads(created.output)["result"]["id"]
    stored = components.privacy_admin.repository.get("case", case_id, "root/team")
    assert stored["created_by"].startswith("host:")
    assert "forged-actor" not in stored["created_by"]
    assert components.privacy_admin.repository.get("case", case_id, "root") is None
    assert "subject_keys" not in json.dumps(json.loads(created.output)["cases"])
    stale = runner.invoke(privacy_cli, command)
    assert stale.exit_code != 0
    assert "Privacy state changed" in stale.output
    assert len(components.privacy_admin.repository.list("case", "root/team")) == 1


def test_real_web_auth_scopes_case_actions_and_preserves_actor(tmp_path, monkeypatch):
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "owner")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "owner-fixture-password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "privacy-integration-fixture-secret")
    components = build_admin_runtime(write_runtime_config(tmp_path))
    directories = DirectoryService(components.runtime.directories)
    directories.create(parent="root", name="team")
    directories.create(parent="root", name="other")
    app = create_app(components)
    users = app.state.admin_user_store
    users.upsert_user(login="case-worker", display_name="Case Worker")
    users.set_local_password(login="case-worker", password="worker-fixture-password")
    users.upsert_group(group_id="privacy-case-workers", display_name="Privacy case workers")
    users.set_group_permissions("privacy-case-workers", ["privacy.read", "privacy.cases"])
    users.assign_user_group(login="case-worker", group_id="privacy-case-workers", directory_path="root/team")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            anonymous = await client.get("/api/privacy?directory_path=root/team")
            assert anonymous.status_code in {401, 403}
            login = await client.post("/api/auth/login", json={
                "username": "case-worker", "password": "worker-fixture-password",
            })
            assert login.status_code == 200, login.text
            before = await client.get("/api/privacy?directory_path=root/team")
            assert before.status_code == 200, before.text
            assert before.headers["cache-control"] == "no-store"
            for scope in ("root", "root/other"):
                assert (await client.get(f"/api/privacy?directory_path={scope}")).status_code == 403
            body = {"expected_revision": before.json()["revision"], "payload": request_fields(),
                    "actor": "forged-owner", "directory_path": "root/other"}
            route = "/api/privacy/actions/cases.create?directory_path=root/team"
            assert (await client.post(route, json=body)).status_code == 403
            assert (await client.post("/api/admin/privileged/unlock")).status_code == 200
            created = await client.post(route, json=body)
            assert created.status_code == 200, created.text
            assert (await client.post(route, json=body)).status_code == 409
            assert (await client.post("/api/privacy/actions/configure?directory_path=root/team", json={
                "expected_revision": created.json()["revision"], "payload": {"config": {}},
            })).status_code == 403
            return created.json()["result"]["id"]

    case_id = asyncio.run(scenario())
    stored = components.privacy_admin.repository.get("case", case_id, "root/team")
    assert stored["created_by"] == "case-worker"
    assert components.privacy_admin.repository.get("case", case_id, "root/other") is None
