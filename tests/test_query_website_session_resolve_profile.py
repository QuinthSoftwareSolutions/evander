from __future__ import annotations

import asyncio
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import httpx
import jsonschema
import pytest
from pydantic import ValidationError

from config_helpers import (
    canonical_worker_config, effect_execution_payload, gate_execution_payload, write_runtime_config,
)
from evander.admin.composition import build_admin_runtime
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.events.models import TriggerEvent, materialize_node_definition
from evander.models import SessionManifest
from evander.orchestration.adapters.provider_factories import build_query_provider_factory
from evander.queries.providers.schemas import WebsiteSessionResolveProfileQueryDefinition
from evander.queries.registry import DEFAULT_QUERY_PROVIDER_REGISTRY
from evander.session_auth import session_mcp_auth_metadata
from evander.website_credentials import WebsiteCredentialStore
from evander.website_sessions import ManagedWebsiteSessionService
from evander.workflow.contracts import WorkflowNodeEnvelope, effective_node_output_schema


NODE_TYPE = "query:website-session:resolve-profile"
BROWSER = "https://alpha.example.test:8443"
API = "https://alpha.example.test:5001"


@pytest.fixture
def rig(tmp_path):
    runtime = build_admin_runtime(write_runtime_config(tmp_path, enable_directory_scoped_config=True))
    runtime.operations_admin.create_directory_child("root", "team")
    runtime.operations_admin.create_directory_child("root", "other")
    runtime.operations_admin.create_directory_child("root/team", "child")
    admin = runtime.website_session_admin
    factory = build_query_provider_factory(
        runtime_dir=runtime.config.runtime_dir,
        provider_config_resolver=runtime.runtime.provider_config,
        query_providers=DEFAULT_QUERY_PROVIDER_REGISTRY,
        admin_runtime=runtime,
    )

    def save(profile_id="alpha-login", *, browser=BROWSER, api=API, scope="root/team",
             credentials=None, **overrides):
        credentials = credentials or {"username": "alpha-operator", "password": "alpha-private-secret"}
        profile = {
            "browser_origin": browser, "login_url": api + "/auth",
            "credential_source": "stored",
            "credential_fields": {key: key for key in credentials},
            "public_credential_fields": [key for key in credentials if key != "password"],
            "local_storage": {"access": {"response_pointer": "/token", "encoding": "string"}},
            **overrides,
        }
        return admin.save(scope, profile_id, {
            "expected_revision": admin.snapshot(scope)["revision"],
            "profile": profile,
            "credentials": credentials if profile["credential_source"] == "stored" else {},
        })

    def query(*, browser=BROWSER, api=API, scope="root/team", **config):
        node = WorkflowNodeEnvelope(id="login", display_name="Resolve login", type=NODE_TYPE,
            config={"browser_origin": browser, "login_origin": api, **config})
        return factory.build(node, directory_path=scope)

    return SimpleNamespace(runtime=runtime, admin=admin, factory=factory, save=save, query=query)


def assert_output(output, *, ready, profile_id=None, error_code=None):
    assert output == {"ready": ready, "profile_id": profile_id, "error_code": error_code}
    jsonschema.validate(output, {
        "type": "object", "properties": effective_node_output_schema({"type": NODE_TYPE}),
        "required": ["ready", "profile_id", "error_code"], "additionalProperties": False,
    })


def test_origin_resolution_binds_distinct_accounts_and_login_fields_without_reading_secrets(rig, monkeypatch):
    rig.save()
    rig.save("beta-login", browser="https://beta.example.test:8443", api="https://beta.example.test:5001",
             credentials={"email": "beta-operator@example.test", "password": "beta-private-secret"})
    with monkeypatch.context() as patch:
        patch.setattr(WebsiteCredentialStore, "read", lambda *args: pytest.fail("resolution decrypted credentials"))
        alpha = rig.query().execute()
        beta = rig.query(browser="https://beta.example.test:8443", api="https://beta.example.test:5001").execute()
    assert_output(alpha, ready=True, profile_id="alpha-login")
    assert_output(beta, ready=True, profile_id="beta-login")

    calls = []
    def login(request):
        calls.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"token": "temporary-website-state"})
    broker = ManagedWebsiteSessionService(rig.runtime.runtime.provider_config,
        transport_factory=lambda _: httpx.MockTransport(login))
    for host, profile_id in (("alpha", alpha["profile_id"]), ("beta", beta["profile_id"])):
        manifest = SessionManifest(session_id=host, directory_path="root/team", allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1), metadata={
                "mcp_session_auth": session_mcp_auth_metadata("proof"),
                "http_targets": {"targets": {
                    "app": {"base_url": f"https://{host}.example.test:8443", "managed_website_session": profile_id},
                    "api": {"base_url": f"https://{host}.example.test:5001"},
                }},
            })
        rig.runtime.runtime.session_repository.upsert_session(manifest)
        state = asyncio.run(broker.acquire(host, "app", "proof"))
        assert state["origin"] == f"https://{host}.example.test:8443"
    assert calls == [
        ("https://alpha.example.test:5001/auth", {"username": "alpha-operator", "password": "alpha-private-secret"}),
        ("https://beta.example.test:5001/auth", {"email": "beta-operator@example.test", "password": "beta-private-secret"}),
    ]


@pytest.mark.parametrize("scope,ready", [("root/team", True), ("root/team/child", True), ("root/other", False)])
def test_resolution_follows_profile_inheritance_without_sibling_access(rig, scope, ready):
    rig.save()
    assert_output(rig.query(scope=scope).execute(), ready=ready,
                  profile_id="alpha-login" if ready else None,
                  error_code=None if ready else "website_profile_not_found")


@pytest.mark.parametrize("case,code", [
    ("missing", "website_profile_not_found"),
    ("disabled", "website_profile_not_found"),
    ("different_browser_port", "website_profile_not_found"),
    ("ambiguous", "website_profile_ambiguous"),
    ("api_host", "website_login_origin_mismatch"),
    ("api_port", "website_login_origin_mismatch"),
    ("credentials_removed", "website_credentials_missing"),
    ("vault_error", "website_credentials_unavailable"),
])
def test_resolution_fails_closed_with_only_safe_metadata(rig, monkeypatch, case, code):
    if case != "missing":
        rig.save(enabled=case != "disabled")
    config = {}
    if case == "different_browser_port":
        config["browser"] = "https://alpha.example.test"
    elif case == "ambiguous":
        rig.save("second-account")
    elif case in {"api_host", "api_port"}:
        config["api"] = "https://other.example.test:5001" if case == "api_host" else "https://alpha.example.test"
    elif case == "credentials_removed":
        rig.admin.delete("root/team", "alpha-login", rig.admin.snapshot("root/team")["revision"], credentials_only=True)
    elif case == "vault_error":
        def fail(*args):
            raise OSError("private-password in a storage failure")
        monkeypatch.setattr(rig.admin.credentials, "status", fail)
    assert_output(rig.query(**config).execute(), ready=False, error_code=code)


def test_environment_backed_profile_requires_approved_and_complete_host_credentials(rig, monkeypatch):
    profile = {"browser_origin": BROWSER, "login_url": API + "/auth",
        "credential_fields": {"username": "TEST_SITE_USER", "password": "TEST_SITE_PASSWORD"},
        "public_credential_fields": ["username"], "static_fields": {}}
    grant = {"directory_path": "root/team", "profile_id": "alpha-login", **profile}
    monkeypatch.setenv("EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST", json.dumps([grant]))
    monkeypatch.setenv("TEST_SITE_USER", "private-user")
    monkeypatch.delenv("TEST_SITE_PASSWORD", raising=False)
    rig.save(credential_source="environment", credential_fields=profile["credential_fields"])
    assert_output(rig.query().execute(), ready=False, error_code="website_credentials_missing")
    monkeypatch.setenv("TEST_SITE_PASSWORD", "private-password")
    assert_output(rig.query().execute(), ready=True, profile_id="alpha-login")
    monkeypatch.delenv("EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST")
    assert_output(rig.query().execute(), ready=False, error_code="website_credentials_missing")


@pytest.mark.parametrize("directory", ["root", "root/other", "root/team-other"])
def test_query_cannot_select_an_ancestor_or_sibling_directory(rig, directory):
    rig.save(scope="root/other")
    with pytest.raises(ValueError, match="outside workflow directory scope"):
        rig.query(directory_path=directory).execute()


def test_query_resolves_bindings_and_canonical_origins_for_a_descendant(rig):
    rig.save()
    provider = rig.query(browser="${{request.browser_origin}}", api="${{request.api_origin}}")
    with pytest.raises(ValueError, match="unresolved bindings"):
        provider.execute()
    assert_output(provider.execute(resolved_config={
        "browser_origin": "https://ALPHA.example.test:8443/", "login_origin": API,
        "directory_path": "root/team/child",
    }), ready=True, profile_id="alpha-login")


@pytest.mark.parametrize("field,value", [
    ("browser_origin", ""), ("browser_origin", "http://alpha.example.test"),
    ("login_origin", 123), ("login_origin", "https://user:secret@example.test"),
    ("directory_path", {"path": "root"}),
])
def test_schema_and_resolved_config_validate_public_inputs(rig, field, value):
    argument = {"browser_origin": "browser", "login_origin": "api"}.get(field, field)
    with pytest.raises(ValidationError):
        rig.query(**{argument: value})
    with pytest.raises(ValidationError):
        rig.query().execute(resolved_config={field: value})


def test_schema_preserves_bindable_origins_and_defaults_to_workflow_scope():
    definition = materialize_node_definition(WorkflowNodeEnvelope(
        id="login", display_name="Resolve login", type=NODE_TYPE,
        config={"browser_origin": "${{request.website}}", "login_origin": "${{request.api}}"}))
    assert isinstance(definition, WebsiteSessionResolveProfileQueryDefinition)
    assert definition.directory_path is None
    assert definition.browser_origin == "${{request.website}}"


@pytest.mark.parametrize("configured", [True, False])
def test_workflow_dispatches_only_after_login_resolution_and_validation(rig, configured):
    if configured:
        rig.save()
    worker_config = canonical_worker_config({
        "runtime_dir": rig.runtime.config.runtime_dir, "poll_interval_seconds": 1, "jitter_seconds": 0,
        "triggers": [{"id": "request", "display_name": "Request", "type": "trigger:cron", "expression": "* * * * *"}],
        "queries": [{"id": "login", "display_name": "Resolve login", "type": NODE_TYPE,
                     "browser_origin": BROWSER, "login_origin": API}],
        "gates": [{"id": "access-ready", "display_name": "Check login", "type": "gate:validate",
                   "checks": [{"code": "website_access_not_ready", "message": "Configure and test one matching website login.",
                               "when": {"left": "${{login.ready}}", "equals": True}}]}],
        "effects": [
            {"id": "store-access", "display_name": "Bind login", "type": "effect:session:update-metadata",
             "target": "demo", "metadata": {"profile_id": "${{login.profile_id}}"}},
            {"id": "enqueue", "display_name": "Continue agent", "type": "effect:agent:enqueue-prompt", "target": "demo", "prompt": "Perform the task."},
        ],
        "edges": [{"source_id": "request", "target_id": "login"},
                  {"source_id": "login", "target_id": "access-ready"},
                  {"source_id": "access-ready", "target_id": "store-access", "source_port": "valid"},
                  {"source_id": "store-access", "target_id": "enqueue"}],
        "workflow_graphs": [{"id": "configure", "name": "Configure environment", "directory_path": "root/team",
                             "node_ids": ["request", "login", "access-ready", "store-access", "enqueue"]}],
    })
    calls = []
    class Factory:
        def build(self, effect, **kwargs):
            def execute(*, resolved_config=None, **kwargs):
                calls.append((effect.id, resolved_config))
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
    worker.query_provider_factory = rig.factory
    worker.provider_factory = Factory()
    worker.event_queue.publish(TriggerEvent(event_id="configure-1", trigger_id="request",
        trigger_type="trigger:cron", occurred_at=datetime.now(UTC), payload={}))
    for _ in range(8):
        worker.run_once()
    records = {r["effect_id"]: r for r in effect_execution_payload(worker_config).values()}
    assert_output(records["login"]["outputs"], ready=configured,
                  profile_id="alpha-login" if configured else None,
                  error_code=None if configured else "website_profile_not_found")
    validation = next(r for r in gate_execution_payload(worker_config).values() if r["gate_id"] == "access-ready")
    assert validation["outputs"]["valid"] is configured
    if configured:
        assert [node for node, _ in calls] == ["store-access", "enqueue"]
        assert calls[0][1]["metadata"]["profile_id"] == "alpha-login"
    else:
        assert calls == []
        assert validation["outputs"]["failed_codes"] == ["website_access_not_ready"]
