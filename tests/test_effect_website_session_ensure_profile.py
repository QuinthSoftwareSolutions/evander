from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import httpx
import jsonschema
import pytest
from pydantic import ValidationError

from config_helpers import canonical_worker_config, effect_execution_payload, write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.effects.registry import DEFAULT_EFFECT_PROVIDER_REGISTRY
from evander.events.models import TriggerEvent, materialize_node_definition
from evander.models import SessionManifest
from evander.orchestration.adapters.provider_factories import build_effect_provider_factory
from evander.session_auth import session_mcp_auth_metadata
from evander.website_credentials import WebsiteCredentialStore
from evander.website_profile_templates import WEBSITE_PROFILE_TEMPLATES_ENV
from evander.website_sessions import ManagedWebsiteSessionService
from evander.workflow.contracts import WorkflowNodeEnvelope, effective_node_output_schema


NODE_TYPE = "effect:website-session:ensure-profile"
BROWSER = "https://env-alpha.example.test:8443"
API = "https://env-alpha.example.test:5001"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    runtime = build_admin_runtime(write_runtime_config(tmp_path, enable_directory_scoped_config=True))
    for parent, name in (("root", "team"), ("root", "other"), ("root/team", "child")):
        runtime.operations_admin.create_directory_child(parent, name)
    admin = runtime.website_session_admin
    profile = {
        "browser_origin": "https://template.example.test:8443",
        "login_url": "https://template.example.test:5001/auth/login",
        "credential_source": "stored",
        "credential_fields": {"email": "email", "password": "password"},
        "public_credential_fields": ["email"],
        "local_storage": {"access": {"response_pointer": "/token", "encoding": "string"}},
    }
    admin.save("root/team", "seed-login", {
        "expected_revision": admin.snapshot("root/team")["revision"],
        "profile": profile,
        "credentials": {"email": "operator@example.test", "password": "private-test-password"},
    })
    policy = {"environments": {
        "directory_path": "root/team", "source_profile_id": "seed-login",
        "browser_origins": ["https://env-*.example.test:8443"],
        "login_origins": ["https://env-*.example.test:5001"],
    }}
    monkeypatch.setenv(WEBSITE_PROFILE_TEMPLATES_ENV, json.dumps(policy))
    factory = build_effect_provider_factory(
        runtime_dir=runtime.config.runtime_dir,
        provider_config_resolver=runtime.runtime.provider_config,
        effect_providers=DEFAULT_EFFECT_PROVIDER_REGISTRY, admin_runtime=runtime,
    )

    def effect(*, scope="root/team", **config):
        node = WorkflowNodeEnvelope(id="login", display_name="Ensure login", type=NODE_TYPE,
            config={"template_id": "environments", "browser_origin": BROWSER,
                    "login_origin": API, **config})
        return factory.build(node, directory_path=scope)

    return SimpleNamespace(runtime=runtime, admin=admin, effect=effect, factory=factory,
                           policy=policy, profile=profile)


def test_workflow_profile_supports_broker_login_and_reuses_without_secret_output(rig):
    result = rig.effect().execute()
    assert result["success"] is True and result["created"] is True
    jsonschema.validate(result, {"type": "object", "properties": effective_node_output_schema({"type": NODE_TYPE}),
                               "required": ["success", "profile_id", "created"], "additionalProperties": False})
    profile_id = result["profile_id"]
    assert rig.admin.resolve_profile(directory_path="root/team", browser_origin=BROWSER,
                                     login_origin=API) == {"ready": True, "profile_id": profile_id, "error_code": None}
    before = rig.admin.snapshot("root/team")["revision"]
    assert rig.effect().execute() == {"success": True, "profile_id": profile_id, "created": False}
    assert rig.admin.snapshot("root/team")["revision"] == before
    session = SessionManifest(session_id="environment", directory_path="root/team", allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1), metadata={
            "mcp_session_auth": session_mcp_auth_metadata("proof"),
            "http_targets": {"targets": {
                "app": {"base_url": BROWSER, "managed_website_session": profile_id},
                "api": {"base_url": API},
            }},
        })
    rig.runtime.runtime.session_repository.upsert_session(session)
    calls = []

    def login(request):
        calls.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"token": "temporary-browser-state"})

    broker = ManagedWebsiteSessionService(rig.runtime.runtime.provider_config,
        transport_factory=lambda _: httpx.MockTransport(login))
    state = asyncio.run(broker.acquire("environment", "app", "proof"))
    assert calls == [(API + "/auth/login", {"email": "operator@example.test", "password": "private-test-password"})]
    assert state["origin"] == BROWSER
    assert state["storageState"]["origins"][0]["localStorage"] == [{"name": "access", "value": "temporary-browser-state"}]
    assert "private-test-password" not in json.dumps(result)
    assert b"private-test-password" not in (rig.runtime.runtime_dir / "website-credentials/credentials.db").read_bytes()
    overlay = rig.runtime.operations_admin.read_directory_domain_config("root/team", "providers").data.config
    assert "private-test-password" not in json.dumps(overlay)


@pytest.mark.parametrize("config", [
    {"template_id": "unknown"},
    {"browser_origin": "https://unapproved.example.test:8443"},
    {"browser_origin": "https://env-alpha.example.test"},
    {"login_origin": "https://env-alpha.example.test:8443"},
    {"login_origin": "https://env-alpha.attacker.example.test:5001"},
    {"directory_path": "root/team/missing"},
])
def test_denied_provisioning_does_not_read_secrets_or_write_configuration(rig, monkeypatch, config):
    before = rig.admin.snapshot("root/team")["revision"]
    monkeypatch.setattr(WebsiteCredentialStore, "read", lambda *a: pytest.fail("denied provisioning read secrets"))
    with pytest.raises(ValueError):
        rig.effect().execute(resolved_config=config)
    assert rig.admin.snapshot("root/team")["revision"] == before


@pytest.mark.parametrize("scope,config", [
    ("root/team", {"directory_path": "root"}),
    ("root/team", {"directory_path": "root/other"}),
    ("root/other", {}),
])
def test_workflow_scope_and_host_template_scope_are_both_required(rig, scope, config):
    with pytest.raises(ValueError):
        rig.effect(scope=scope).execute(resolved_config=config)


def test_child_overrides_cannot_change_host_template_source(rig):
    replacement = dict(rig.profile, login_url="https://template.example.test:5001/wrong-login")
    rig.admin.save("root/team/child", "seed-login", {
        "expected_revision": rig.admin.snapshot("root/team/child")["revision"],
        "profile": replacement, "credentials": {"email": "other@example.test", "password": "other-password"},
    })
    result = rig.effect(scope="root/team/child").execute()
    profile = rig.runtime.runtime.provider_config.resolve_for_directory("root/team/child").managed_website_sessions[result["profile_id"]]
    assert profile.login_url == API + "/auth/login"
    assert rig.admin.credentials.read(profile.credential_ref, "root/team/child", result["profile_id"], profile) == {
        "email": "operator@example.test", "password": "private-test-password"}


def test_concurrent_retries_create_only_one_profile(rig):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: rig.effect().execute(), range(2)))
    assert len({r["profile_id"] for r in results}) == 1
    assert sum(r["created"] for r in results) == 1


def test_exact_host_policy_and_canonical_origins_share_one_profile(rig, monkeypatch):
    rig.policy["environments"].update(browser_origins=[BROWSER], login_origins=[API])
    monkeypatch.setenv(WEBSITE_PROFILE_TEMPLATES_ENV, json.dumps(rig.policy))
    first = rig.effect(browser_origin="https://ENV-ALPHA.example.test:8443/").execute()
    assert rig.effect().execute() == {"success": True, "profile_id": first["profile_id"], "created": False}


def test_existing_operator_profile_is_preserved_without_reading_template_credentials(rig, monkeypatch):
    rig.admin.save("root/team", "operator-login", {
        "expected_revision": rig.admin.snapshot("root/team")["revision"],
        "profile": dict(rig.profile, browser_origin=BROWSER, login_url=API + "/auth/login"),
        "credentials": {"email": "other@example.test", "password": "other-password"},
    })
    before = rig.admin.snapshot("root/team")["revision"]
    monkeypatch.setattr(WebsiteCredentialStore, "read", lambda *a: pytest.fail("conflict read source credentials"))
    with pytest.raises(ValueError, match="already exists"):
        rig.effect().execute()
    assert rig.admin.snapshot("root/team")["revision"] == before


@pytest.mark.parametrize("change", ["disabled", "credentials_removed"])
def test_unavailable_source_cannot_create_a_profile(rig, change):
    snapshot = rig.admin.snapshot("root/team")
    if change == "disabled":
        value = snapshot["profiles"][0]["profile"]
        value["enabled"] = False
        rig.admin.save("root/team", "seed-login", {"expected_revision": snapshot["revision"], "profile": value})
    else:
        rig.admin.delete("root/team", "seed-login", snapshot["revision"], credentials_only=True)
    before = rig.admin.snapshot("root/team")["revision"]
    with pytest.raises(ValueError) as error:
        rig.effect().execute()
    assert "private-test-password" not in str(error.value)
    assert rig.admin.snapshot("root/team")["revision"] == before


@pytest.mark.parametrize("configured", [True, False])
def test_orchestrator_binds_profile_only_after_success_without_recording_credentials(rig, monkeypatch, configured):
    if not configured:
        monkeypatch.delenv(WEBSITE_PROFILE_TEMPLATES_ENV)
    worker_config = canonical_worker_config({
        "runtime_dir": rig.runtime.runtime_dir, "poll_interval_seconds": 1, "jitter_seconds": 0,
        "triggers": [{"id": "request", "display_name": "Request", "type": "trigger:event", "event_type": "test.environment.ready"}],
        "effects": [
            {"id": "login", "display_name": "Ensure login", "type": NODE_TYPE,
             "template_id": "environments", "browser_origin": "${{request.browser}}",
             "login_origin": "${{request.api}}"},
            {"id": "bind", "display_name": "Bind login", "type": "effect:session:update-metadata",
             "target": "session", "metadata": {"http_targets": {"targets": {
                 "app": {"base_url": BROWSER, "managed_website_session": "${{login.profile_id}}"},
                 "api": {"base_url": API},
             }}}},
        ],
        "edges": [{"source_id": "request", "target_id": "login"},
                  {"source_id": "login", "target_id": "bind"}],
        "workflow_graphs": [{"id": "configure", "name": "Configure login", "directory_path": "root/team",
                             "node_ids": ["request", "login", "bind"]}],
    })
    calls = []

    class Factory:
        def build(self, effect, **kwargs):
            if effect.id != "bind":
                return rig.factory.build(effect, **kwargs)

            def execute(*, resolved_config=None):
                calls.append(resolved_config)
                return {"success": True}

            return SimpleNamespace(execute=execute)

    class ImmediateExecutor:
        def submit(self, fn, *args, **kwargs):
            future = Future()
            try:
                future.set_result(fn(*args, **kwargs))
            except Exception as error:
                future.set_exception(error)
            return future

        def shutdown(self, **kwargs):
            pass

    worker = EffectOrchestratorWorker(config=rig.runtime.config, worker_config=worker_config, runtime=None)
    worker._executor.shutdown(wait=True)
    worker._executor = ImmediateExecutor()
    worker.provider_factory = Factory()
    worker.event_queue.publish(TriggerEvent(event_id="configure-1", trigger_id="request",
        trigger_type="trigger:event", occurred_at=datetime.now(UTC), payload={"browser": BROWSER, "api": API}))
    for _ in range(5):
        worker.run_once()
    records = {r["effect_id"]: r for r in effect_execution_payload(worker_config).values()}
    assert records["login"]["status"] == ("succeeded" if configured else "failed"), records["login"]
    assert "private-test-password" not in json.dumps(records)
    if configured:
        assert calls[0]["metadata"]["http_targets"]["targets"]["app"]["managed_website_session"] == records["login"]["outputs"]["profile_id"]
    else:
        assert calls == []


@pytest.mark.parametrize("change", ["disable", "remove_credentials"])
def test_retry_does_not_overwrite_operator_changes_or_restore_revoked_credentials(rig, change):
    result = rig.effect().execute()
    profile_id = result["profile_id"]
    snapshot = rig.admin.snapshot("root/team")
    if change == "remove_credentials":
        rig.admin.delete("root/team", profile_id, snapshot["revision"], credentials_only=True)
    else:
        value = next(p["profile"] for p in snapshot["profiles"] if p["id"] == profile_id)
        value["enabled"] = False
        rig.admin.save("root/team", profile_id, {"expected_revision": snapshot["revision"], "profile": value})
    before = rig.admin.snapshot("root/team")["revision"]
    with pytest.raises(ValueError):
        rig.effect().execute()
    assert rig.admin.snapshot("root/team")["revision"] == before


def test_write_failure_removes_only_the_new_credential_record(rig, monkeypatch):
    before = rig.admin.snapshot("root/team")["revision"]
    refs = []
    put = rig.admin.credentials.put

    def capture(*args):
        ref = put(*args)
        refs.append(ref)
        return ref

    monkeypatch.setattr(rig.admin.credentials, "put", capture)
    monkeypatch.setattr(rig.admin, "_write", lambda *a: (_ for _ in ()).throw(RuntimeError("write failed")))
    with pytest.raises(RuntimeError, match="write failed"):
        rig.effect().execute()
    with rig.admin.credentials._database() as database:
        assert database.execute("SELECT count(*) FROM credentials WHERE id=?", (refs[0],)).fetchone()[0] == 0
        assert database.execute("SELECT count(*) FROM credentials").fetchone()[0] == 1
    assert rig.admin.snapshot("root/team")["revision"] == before


@pytest.mark.parametrize("value", ["not json", "[]", '{"template":{"unexpected":true}}'])
def test_invalid_host_policy_fails_closed(rig, monkeypatch, value):
    monkeypatch.setenv(WEBSITE_PROFILE_TEMPLATES_ENV, value)
    with pytest.raises(ValueError, match="template configuration is invalid"):
        rig.effect().execute()


@pytest.mark.parametrize("origin", ["https://*.test", "https://env.example.*", "https://*.*.example.test:8443"])
def test_host_policy_rejects_broad_or_multiple_wildcards(rig, monkeypatch, origin):
    rig.policy["environments"]["browser_origins"] = [origin]
    monkeypatch.setenv(WEBSITE_PROFILE_TEMPLATES_ENV, json.dumps(rig.policy))
    with pytest.raises(ValueError, match="template configuration is invalid"):
        rig.effect().execute()


def test_schema_accepts_bindings_and_runtime_requires_resolved_inputs(rig):
    node = WorkflowNodeEnvelope(id="login", display_name="Ensure login", type=NODE_TYPE, config={
        "template_id": "environments", "browser_origin": "${{request.browser}}", "login_origin": "${{request.api}}"})
    definition = materialize_node_definition(node)
    assert definition.directory_path is None
    assert definition.browser_origin == "${{request.browser}}"
    provider = rig.effect(browser_origin="${{request.browser}}", login_origin="${{request.api}}")
    with pytest.raises(ValueError, match="unresolved bindings"):
        provider.execute()
    assert provider.execute(resolved_config={"browser_origin": BROWSER, "login_origin": API})["success"]
    for origin in ("http://example.test", "https://user:secret@example.test", BROWSER + "/path"):
        with pytest.raises(ValidationError):
            rig.effect(browser_origin=origin)
