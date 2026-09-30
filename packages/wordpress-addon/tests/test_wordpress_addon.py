from __future__ import annotations

import asyncio
import base64
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, quote_plus
import zipfile

import httpx
import pytest
from starlette.applications import Starlette

from evander.addons import AddonContext
from evander.connectors.models import ConnectorDefinition, ConnectorDirectoryConfig
from evander.website_sessions import validate_browser_state, WebsiteSessionError
from evander.workflow.binding import resolve_workflow_bindings
from evander.workflow.packs import WorkflowPack
from evander_wordpress_addon import register_addon
from evander_wordpress_addon.api import admin_api_routes, plugin_zip
from evander_wordpress_addon.deployment import SSHAdapter
from evander_wordpress_addon.models import (
    Conflict,
    Settings,
    Site,
    WordPressError,
    UnknownOutcome,
    digest,
    operation_definitions,
)
from evander_wordpress_addon.providers import (
    ReadyDefinition,
    WordPressEffectProvider,
    WordPressReadyProvider,
    background_service,
    register_providers,
)
from evander_wordpress_addon.service import Actor, Service
from evander_wordpress_addon.ssh_helper import Helper
from evander_wordpress_addon.state import Store


def run(coro):
    return asyncio.run(coro)


class Remote:
    def __init__(self):
        self.value = {"post_title": "Before", "post_content": "Welcome"}
        self.other_language = "Välkommen"
        self.state = "prepared"
        self.calls = []
        self.writes = 0
        self.lost_reply = False

    def handle(self, request):
        route = parse_qs(request.url.query.decode()).get("rest_route", [request.url.path])[0]
        operation = route.split("/")[-1]
        args = json.loads(request.content) if request.content else {}
        self.calls.append((operation, args))
        if operation == "discover":
            value = {"protocol_version": 1, "transactional": True, "browser_ready": True}
        elif operation == "inspect":
            value = {"key": "post:7", "value": self.value, "version": digest(self.value)}
        elif operation == "prepare":
            row = args["resources"][0]
            if row["expected_version"] != digest(self.value):
                return httpx.Response(409)
            self.before = dict(self.value)
            self.desired = row["value"]
            value = {
                "snapshot": {
                    "post:7": {
                        "before": self.before,
                        "before_version": digest(self.before),
                        "value": self.desired,
                    }
                }
            }
        elif operation == "arm":
            self.state = "armed"
            value = {"state": self.state}
        elif operation == "apply":
            if self.state != "applied":
                if self.value != self.before:
                    return httpx.Response(409)
                self.value = self.desired
                self.state = "applied"
                self.writes += 1
            if self.lost_reply:
                self.lost_reply = False
                return httpx.Response(500)
            value = {"state": self.state}
        elif operation == "restore":
            if self.value not in [self.before, self.desired]:
                return httpx.Response(409)
            self.value = self.before
            self.state = "restored"
            value = {"state": self.state}
        else:
            value = {"state": self.state}
        return httpx.Response(200, json=value)


class Probe:
    def __init__(self):
        self.results = []

    async def run(self, site, checks, label):
        return (
            self.results.pop(0)
            if self.results
            else {"failures": [], "screenshots": [], "uncertain": False}
        )


@pytest.fixture
def rig(tmp_path):
    remote, probe = Remote(), Probe()
    site = Site(origin="https://design.example.test", connector_id="website", enabled=True)
    site.recovery_verified = site.binding()
    store = Store(tmp_path)
    store.save_settings("root/design", 0, Settings(sites={"main": site}))
    definition = ConnectorDefinition(
        display_name="Website",
        description="Website editing",
        driver="wordpress",
        config={"base_url": site.origin},
        operations=operation_definitions(),
    )
    config = ConnectorDirectoryConfig(connectors={"website": definition})
    provider = SimpleNamespace(resolve_connectors_for_directory=lambda directory: config)

    async def auth(directory, connector):
        return definition, {"Authorization": "Bearer host-only-secret"}

    host = SimpleNamespace(provider_config=provider, host_auth=auth, host_services={})
    revoked = [False]

    def authorize():
        if revoked[0]:
            raise PermissionError("revoked")

    async def wait(seconds):
        pass

    service = Service(
        tmp_path,
        Actor("root/design", "session-a"),
        host,
        authorize=authorize,
        checks=probe,
        wait=wait,
        transport_factory=lambda origin: httpx.MockTransport(remote.handle),
    )
    return SimpleNamespace(
        service=service,
        remote=remote,
        probe=probe,
        store=store,
        site=site,
        revoked=revoked,
        host=host,
    )


def prepare(rig, **extra):
    return run(
        rig.service.prepare(
            {
                "site_id": "main",
                "idempotency_key": "edit-first",
                "summary": "Change heading",
                "resources": [
                    {
                        "key": "post:7",
                        "expected_version": digest(rig.remote.value),
                        "value": {"post_title": "After", "post_content": "Welcome"},
                    }
                ],
                **extra,
            }
        )
    )


def queue(rig, change, restore=False, **extra):
    return run(
        rig.service.queue(
            {
                "site_id": "main",
                "change_id": change["change_id"],
                "expected_revision": change["revision"],
                "idempotency_key": "restore-first" if restore else "publish-first",
                **extra,
            },
            restore=restore,
        )
    )


@pytest.fixture
def browser_change(rig):
    revision, settings = rig.store.settings("root/design")
    site = settings.sites["main"]
    site.capabilities.add("browser")
    site.browser_target = "editor"
    site.recovery_verified = site.binding()
    rig.store.save_settings("root/design", revision, settings)
    prepared = prepare(rig, browser=True)
    # Browser requests have already published an edit covered by the original snapshot.
    rig.remote.value = dict(rig.remote.desired)
    rig.remote.state = "browser_open"
    return rig, prepared


@pytest.fixture
def pending_browser_extension(browser_change):
    rig, prepared = browser_change

    def handle(request):
        if request.url.path.endswith("/prepare-extend"):
            return httpx.Response(
                200,
                json={
                    "snapshot": {
                        "meta:7:builder": {
                            "before": [],
                            "before_version": digest([]),
                            "value": ["New builder data"],
                        }
                    }
                },
            )
        if request.url.path.endswith("/arm-extend"):
            rig.remote.calls.append(("arm-extend", json.loads(request.content)))
            return httpx.Response(503)
        return rig.remote.handle(request)

    rig.service.transport_factory = lambda origin: httpx.MockTransport(handle)
    pending = run(
        rig.service.prepare(
            {
                "site_id": "main",
                "summary": "Cover builder metadata",
                "idempotency_key": "extend-builder",
                "browser": True,
                "change_id": prepared["change_id"],
                "expected_revision": prepared["revision"],
                "resources": [
                    {
                        "key": "meta:7:builder",
                        "expected_version": digest([]),
                        "value": ["New builder data"],
                    }
                ],
            }
        )
    )
    return rig, pending


def resumed_service(rig, *, operator=False):
    return Service(
        rig.store.runtime_dir,
        Actor("root/design", "operator:reviewer", True) if operator else rig.service.actor,
        rig.host,
        authorize=None if operator else rig.service.authorize,
        checks=rig.probe,
        wait=rig.service.wait,
        transport_factory=rig.service.transport_factory,
    )


def pending_events(rig):
    trigger = WordPressReadyProvider()
    trigger.initialize(
        ReadyDefinition(id="ready", display_name="Ready", type="trigger:wordpress:change-ready"),
        SimpleNamespace(
            metadata={"directory_path": "root/design"},
            resolved_config=SimpleNamespace(runtime_dir=rig.store.runtime_dir),
        ),
    )
    return trigger.poll()


def test_publish_then_conditional_undo_has_private_durable_snapshots(rig):
    prepared = prepare(rig)
    assert rig.remote.writes == 0
    assert prepared["ready"]
    stored = rig.store.get(prepared["change_id"], "root/design")
    assert (
        json.loads(rig.store.read_artifact(stored["snapshot"]))["post:7"]["before"]["post_title"]
        == "Before"
    )
    assert "snapshot" not in prepared and "host-only-secret" not in json.dumps(prepared)
    queued = queue(rig, prepared)
    published = run(rig.service.execute("main", queued["change_id"]))
    assert published["state"] == "published" and rig.remote.value["post_title"] == "After"
    assert rig.remote.other_language == "Välkommen"
    restored = queue(rig, published, restore=True)
    assert run(rig.service.execute("main", restored["change_id"]))["state"] == "restored"
    assert rig.remote.value["post_title"] == "Before"
    assert rig.store.path.stat().st_mode & 0o077 == 0


def test_disabling_new_edits_does_not_disable_undo(rig):
    prepared = prepare(rig)
    published = run(rig.service.execute("main", queue(rig, prepared)["change_id"]))
    revision, settings = rig.store.settings("root/design")
    settings.sites["main"].enabled = False
    rig.store.save_settings("root/design", revision, settings)
    with pytest.raises(RuntimeError, match="unavailable"):
        prepare(rig, idempotency_key="another-edit")
    restored = queue(rig, published, restore=True)
    assert run(rig.service.execute("main", restored["change_id"]))["state"] == "restored"
    assert rig.remote.value["post_title"] == "Before"


def test_recovery_binding_survives_serialization_in_another_process():
    import os
    import sys

    program = """from evander_wordpress_addon.models import Site
s=Site(origin="https://design.example.test",connector_id="website",capabilities={"cms","browser"},browser_target="editor")
s.recovery_verified=s.binding()
r=Site.model_validate_json(s.model_dump_json())
assert r.recovery_verified==r.binding(), "Reloading settings revoked an unchanged site"
print(r.binding())
"""
    values = []
    for seed in [6, 7]:
        result = subprocess.run(
            [sys.executable, "-c", program],
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        values.append(result.stdout.strip())
    assert values[0] == values[1]


@pytest.fixture
def browser_grant(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta
    import time
    from runtime_support import write_runtime_config
    from evander.addons import AddonLoaderConfig
    from evander.models import SessionManifest
    from evander.admin.composition import build_admin_runtime
    from evander.session_auth import session_mcp_auth_metadata
    from evander.website_sessions import ManagedWebsiteSessionService

    runtime_dir = write_runtime_config(
        tmp_path,
        enable_directory_scoped_config=True,
        addon_loader=AddonLoaderConfig(
            modules=["evander_wordpress_addon"], enabled_addon_ids=["wordpress-addon"]
        ),
        managed_website_sessions={
            "website": {
                "adapter": "wordpress",
                "adapter_config": {"site_id": "main"},
                "browser_origin": "https://design.example.test",
                "login_url": "https://design.example.test/wordpress/index.php?rest_route=/evander/v1/browser/ticket",
                "max_age_seconds": 900,
            }
        },
    )
    admin = build_admin_runtime(runtime_dir)
    runtime = admin.runtime
    session = SessionManifest(
        session_id="browser-agent",
        allowed_tools=["call_connector"],
        connector_grants={"website": ["prepare_change", "apply_change"]},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={
            "mcp_session_auth": session_mcp_auth_metadata("fixture-proof"),
            "http_targets": {
                "targets": {
                    "editor": {
                        "base_url": "https://design.example.test/wordpress/",
                        "managed_website_session": "website",
                    }
                }
            },
        },
    )
    runtime.session_repository.upsert_session(session)
    site = Site(
        origin="https://design.example.test",
        rest_path="/wordpress/index.php?rest_route=/",
        connector_id="website",
        capabilities={"cms", "browser"},
        browser_target="editor",
        enabled=True,
    )
    site.recovery_verified = site.binding()
    store = Store(runtime_dir)
    store.save_settings("root", 0, Settings(sites={"main": site}))
    config = ConnectorDirectoryConfig(
        connectors={
            "website": ConnectorDefinition(
                display_name="Website",
                description="Design edits",
                driver="wordpress",
                config={"base_url": site.origin},
                operations=operation_definitions(),
            )
        }
    )
    monkeypatch.setattr(
        runtime.provider_config, "resolve_connectors_for_directory", lambda _: config
    )
    change = store.create(
        "root",
        "main",
        site.origin,
        {
            "actor": session.session_id,
            "browser_ready": True,
            "ready": True,
            "binding": Service.config_binding(site, config),
            "expires_at": time.time() + 3600,
            "request": {
                "site_id": "main",
                "summary": "Prepared browser edit",
                "idempotency_key": "fixture-prepare",
                "browser": True,
                "resources": [
                    {
                        "key": "post:7",
                        "expected_version": digest({}),
                        "value": {"post_title": "After"},
                    }
                ],
            },
        },
    )
    broker = ManagedWebsiteSessionService(runtime.provider_config)
    calls = []

    async def temporary_login(*args):
        calls.append(True)
        return {
            "origin": site.origin,
            "expiresAt": time.time() + 600,
            "storageState": {
                "cookies": [
                    {
                        "name": "wordpress_logged_in_fixture",
                        "value": "temporary-cookie",
                        "domain": "design.example.test",
                        "path": "/",
                        "expires": time.time() + 600,
                        "secure": True,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                ],
                "origins": [],
            },
            "sessionStorage": {},
        }

    monkeypatch.setattr(broker.adapters["wordpress"], "login", temporary_login)
    return SimpleNamespace(
        admin=admin,
        runtime=runtime,
        broker=broker,
        calls=calls,
        session=session,
        config=config,
        store=store,
        site=site,
        change=change,
    )


def test_managed_browser_cache_rechecks_connector_and_tool_grants(browser_grant, monkeypatch):
    fixture = browser_grant
    runtime, broker, calls, session, store, change = (
        fixture.runtime,
        fixture.broker,
        fixture.calls,
        fixture.session,
        fixture.store,
        fixture.change,
    )
    first = run(broker.acquire(session.session_id, "editor", "fixture-proof"))
    assert (
        first == run(broker.acquire(session.session_id, "editor", "fixture-proof"))
        and len(calls) == 1
    )
    # A warm cookie cache must still honor revocation of the host credential binding.
    adapter = broker.adapters["wordpress"]
    with monkeypatch.context() as current:
        current.setattr(adapter.host, "auth_status", lambda *args, **kwargs: {"available": False})
        with pytest.raises(WebsiteSessionError):
            run(broker.acquire(session.session_id, "editor", "fixture-proof"))
    session.connector_grants["website"].remove("apply_change")
    runtime.session_repository.upsert_session(session)
    with pytest.raises(WebsiteSessionError):
        run(broker.acquire(session.session_id, "editor", "fixture-proof"))
    session.connector_grants["website"].append("apply_change")
    session.allowed_tools = []
    runtime.session_repository.upsert_session(session)
    with pytest.raises(WebsiteSessionError):
        run(broker.acquire(session.session_id, "editor", "fixture-proof"))
    session.allowed_tools = ["call_connector"]
    runtime.session_repository.upsert_session(session)
    store.update(change, state="published", browser_ready=False)
    with pytest.raises(WebsiteSessionError):
        run(broker.acquire(session.session_id, "editor", "fixture-proof"))
    assert len(calls) == 1


def configure_real_browser_login(
    fixture,
    monkeypatch,
    secret,
    *,
    protocol="basic",
    environment=False,
    on_redeem=None,
    wire_encode=None,
):
    from evander.connectors.models import ConnectorAuthProfile
    from evander.credentials.bindings import connector_binding
    from evander_wordpress_addon.browser import WordPressBrowserAdapter

    monkeypatch.setenv(
        "EVANDER_CREDENTIALS_KEY_FILE", str(fixture.store.runtime_dir.parent / "credential-key")
    )
    connector = fixture.config.connectors["website"]
    connector.auth_profile = "website-login"
    if protocol == "bearer":
        profile = ConnectorAuthProfile(type="bearer_env", token_env="WP_FIXTURE_TOKEN")
        values = {"token": secret}
        monkeypatch.setenv("WP_FIXTURE_TOKEN", secret)
    else:
        profile = ConnectorAuthProfile(
            type="basic_env", username_env="WP_FIXTURE_USER", password_env="WP_FIXTURE_PASSWORD"
        )
        values = {
            "username": secret if protocol == "basic-key" else "public-designer",
            "password": "X" if protocol == "basic-key" else secret,
        }
        monkeypatch.setenv("WP_FIXTURE_USER", values["username"])
        monkeypatch.setenv("WP_FIXTURE_PASSWORD", values["password"])
    adapter = fixture.broker.adapters["wordpress"]
    record = None
    if not environment:
        adapter.host.credentials.initialize()
        record = adapter.host.credentials.create(
            owner="root",
            kind="connector",
            label="Website",
            bindings=[connector_binding("website-login", "website", profile, connector)],
            values=values,
        )
        profile = ConnectorAuthProfile(
            type="bearer" if protocol == "bearer" else "basic", credential_ref=record["id"]
        )
    fixture.config.auth_profiles["website-login"] = profile
    fixture.store.update(
        fixture.change, binding=Service.config_binding(fixture.site, fixture.config)
    )
    state = {
        "origin": fixture.site.origin,
        "expiresAt": time.time() + 600,
        "storageState": {
            "cookies": [
                {
                    "name": "wordpress_logged_in_fixture",
                    "value": "temporary-cookie",
                    "domain": "design.example.test",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                    "sameSite": "Lax",
                    "expires": time.time() + 600,
                }
            ],
            "origins": [],
        },
        "sessionStorage": {},
    }
    calls = []

    def handle(request):
        route = parse_qs(request.url.query.decode())["rest_route"][0]
        calls.append(route)
        assert request.headers["authorization"].startswith(
            "Bearer " if protocol == "bearer" else "Basic "
        )
        if route.endswith("/redeem") and on_redeem:
            on_redeem(adapter.host, record)
        if route.endswith("/redeem") and wire_encode:
            return httpx.Response(
                200, content=wire_encode(state), headers={"Content-Type": "application/json"}
            )
        return httpx.Response(
            200,
            json={"ticket": "single-use-fixture-ticket"} if route.endswith("/ticket") else state,
        )

    class BrowserService(Service):
        def __init__(self, *args, **kwargs):
            super().__init__(
                *args, **kwargs, transport_factory=lambda _: httpx.MockTransport(handle)
            )

    monkeypatch.setattr("evander_wordpress_addon.browser.Service", BrowserService)
    monkeypatch.setattr(adapter, "login", WordPressBrowserAdapter.login.__get__(adapter))
    return state, calls


@pytest.mark.parametrize(
    "encoding",
    [
        "raw",
        "percent",
        "percent-lower",
        "percent-unreserved",
        "percent-all",
        "form",
        "base64",
        "base64-unpadded",
        "base64url",
        "base64url-unpadded",
        "json",
        "numeric",
        "numeric-exponent",
        "boolean",
        "null",
    ],
)
def test_real_browser_login_rejects_managed_secret_reflection(browser_grant, monkeypatch, encoding):
    secret = {
        "numeric": "735246815",
        "numeric-exponent": "735246815",
        "boolean": "true",
        "null": "null",
    }.get(encoding, 'host-"secret\\ with?/ÿ')
    state, calls = configure_real_browser_login(
        browser_grant,
        monkeypatch,
        secret,
        wire_encode=(lambda value: json.dumps(value).replace("735246815.0", "7.35246815e8"))
        if encoding == "numeric-exponent"
        else None,
    )
    reflected = {
        "raw": lambda: secret,
        "percent": lambda: quote(secret, safe=""),
        "percent-lower": lambda: quote(secret, safe="").replace("%3F", "%3f").replace("%2F", "%2f"),
        "percent-unreserved": lambda: "%68" + secret[1:],
        "percent-all": lambda: "".join(f"%{byte:02x}" for byte in secret.encode()),
        "form": lambda: quote_plus(secret, safe=""),
        "json": lambda: json.dumps(secret)[1:-1],
        "base64": lambda: base64.b64encode(secret.encode()).decode(),
        "base64-unpadded": lambda: base64.b64encode(secret.encode()).decode().rstrip("="),
        "base64url": lambda: base64.urlsafe_b64encode(secret.encode()).decode(),
        "base64url-unpadded": lambda: (
            base64.urlsafe_b64encode(secret.encode()).decode().rstrip("=")
        ),
        "numeric": lambda: int(secret),
        "numeric-exponent": lambda: float(secret),
        "boolean": lambda: True,
        "null": lambda: None,
    }[encoding]()
    cookie = state["storageState"]["cookies"][0]
    if isinstance(reflected, str):
        cookie["value"] = reflected
    else:
        cookie["extra"] = [{"reflected": reflected}]
    with pytest.raises(WebsiteSessionError, match="wordpress_browser_login_failed") as error:
        run(
            browser_grant.broker.acquire(
                browser_grant.session.session_id, "editor", "fixture-proof"
            )
        )
    assert secret not in str(error.value)
    assert not browser_grant.broker._cache
    assert len(calls) == 2


@pytest.mark.parametrize(
    "protocol, environment",
    [("basic", True), ("basic-key", False), ("bearer", False), ("bearer", True)],
)
def test_real_browser_login_protects_raw_authentication_variants(
    browser_grant, monkeypatch, protocol, environment
):
    secret = "synthetic-persistent-secret"
    state, _ = configure_real_browser_login(
        browser_grant, monkeypatch, secret, protocol=protocol, environment=environment
    )
    state["storageState"]["cookies"][0]["value"] = secret
    with pytest.raises(WebsiteSessionError, match="wordpress_browser_login_failed"):
        run(
            browser_grant.broker.acquire(
                browser_grant.session.session_id, "editor", "fixture-proof"
            )
        )
    assert not browser_grant.broker._cache


@pytest.mark.parametrize("protocol", ["basic", "basic-key", "bearer"])
def test_real_browser_login_preserves_temporary_authentication_and_public_identifiers(
    browser_grant, monkeypatch, protocol
):
    state, _ = configure_real_browser_login(
        browser_grant, monkeypatch, "synthetic-persistent-secret", protocol=protocol
    )
    state["sessionStorage"] = {"currentUser": "public-designer", "sentinel": "X"}
    acquired = run(
        browser_grant.broker.acquire(browser_grant.session.session_id, "editor", "fixture-proof")
    )
    assert acquired["storageState"]["cookies"][0]["value"] == "temporary-cookie"
    assert acquired["sessionStorage"]["currentUser"] == "public-designer"


def test_real_browser_login_checks_request_secret_after_rotation(browser_grant, monkeypatch):
    old = "synthetic-original-password"

    def rotate(host, record):
        host.credentials.update(
            record["id"],
            expected_revision=record["revision"],
            values={"username": "public-designer", "password": "synthetic-rotated-password"},
        )

    state, _ = configure_real_browser_login(browser_grant, monkeypatch, old, on_redeem=rotate)
    state["storageState"]["cookies"][0]["value"] = old
    with pytest.raises(WebsiteSessionError, match="wordpress_browser_login_failed"):
        run(
            browser_grant.broker.acquire(
                browser_grant.session.session_id, "editor", "fixture-proof"
            )
        )
    assert not browser_grant.broker._cache


@pytest.mark.parametrize("operation", ["prepare_change", "apply_change", "call_connector"])
@pytest.mark.parametrize("cached", [False, True])
def test_browser_cookie_issuance_requires_unapproved_writes(browser_grant, operation, cached):
    fixture = browser_grant
    if cached:
        run(fixture.broker.acquire(fixture.session.session_id, "editor", "fixture-proof"))
    if operation == "call_connector":
        fixture.session.approval_required_tools = ["call_connector"]
        fixture.runtime.session_repository.upsert_session(fixture.session)
    else:
        fixture.config.connectors["website"].operations[operation].requires_approval = True
        fixture.store.update(
            fixture.change, binding=Service.config_binding(fixture.site, fixture.config)
        )
    with pytest.raises(WebsiteSessionError):
        run(fixture.broker.acquire(fixture.session.session_id, "editor", "fixture-proof"))
    assert len(fixture.calls) == int(cached)


def restrict_connector_arguments(fixture, origin, path, value, *, operator="fixed"):
    from evander.directory_config import DirectoryConfigStore
    from evander.models import SessionManifest

    policies = {
        "call_connector": [
            {
                "id": "website-restriction",
                "name": "Website restriction",
                "constraints": [
                    {
                        "id": "allowed-value",
                        "path": path,
                        "operator": operator,
                        "value" if operator == "fixed" else "values": value,
                    }
                ],
            }
        ]
    }
    if origin == "session":
        fixture.session = SessionManifest.model_validate(
            fixture.session.model_dump() | {"tool_argument_policies": policies}
        )
        fixture.runtime.session_repository.upsert_session(fixture.session)
    else:
        DirectoryConfigStore(
            fixture.runtime.directories, runtime_dir=fixture.store.runtime_dir
        ).write("root", domain="policy", payload={"tool_argument_policies": policies})


@pytest.mark.parametrize("origin", ["session", "directory"])
@pytest.mark.parametrize(
    "path, value",
    [("arguments.site_id", "other"), ("operation", "inspect"), ("connector_id", "other")],
)
def test_durable_authorization_rechecks_complete_argument_policy(
    browser_grant, origin, path, value
):
    from evander.policy import ToolArgumentPolicyError

    fixture = browser_grant
    arguments = {
        "site_id": "main",
        "change_id": fixture.change["change_id"],
        "expected_revision": fixture.change["revision"],
        "idempotency_key": "approved-publication",
    }
    fixture.admin.connector_service.authorize_current(
        fixture.session.session_id, "website", "apply_change", arguments
    )
    restrict_connector_arguments(fixture, origin, path, value)
    with pytest.raises(ToolArgumentPolicyError):
        fixture.admin.connector_service.authorize_current(
            fixture.session.session_id, "website", "apply_change", arguments
        )


def test_durable_authorization_fails_closed_without_argument_policy_capability(browser_grant):
    from evander.connectors.service import ConnectorPolicyError

    fixture = browser_grant
    fixture.admin.connector_service.session_argument_authorizer = None
    with pytest.raises(ConnectorPolicyError):
        fixture.admin.connector_service.authorize_current(
            fixture.session.session_id,
            "website",
            "apply_change",
            {
                "site_id": "main",
                "change_id": fixture.change["change_id"],
                "expected_revision": 1,
                "idempotency_key": "publication-request",
            },
        )


def test_durable_authorization_binds_session_and_normalizes_connector_aliases(browser_grant):
    fixture = browser_grant
    arguments = {
        "site_id": "main",
        "change_id": fixture.change["change_id"],
        "expected_revision": 1,
        "idempotency_key": "publication-request",
    }
    restrict_connector_arguments(fixture, "directory", "session_id", fixture.session.session_id)
    fixture.admin.connector_service.authorize_current(
        fixture.session.session_id, " Website ", " APPLY_CHANGE ", arguments
    )
    restrict_connector_arguments(fixture, "directory", "connector_id", "website")
    fixture.admin.connector_service.authorize_current(
        fixture.session.session_id, " Website ", " APPLY_CHANGE ", arguments
    )


@pytest.mark.parametrize("origin", ["session", "directory"])
@pytest.mark.parametrize("cached", [False, True])
def test_browser_authorization_rechecks_argument_policy_before_cache_delivery(
    browser_grant, origin, cached
):
    fixture = browser_grant
    if cached:
        run(fixture.broker.acquire(fixture.session.session_id, "editor", "fixture-proof"))
    restrict_connector_arguments(fixture, origin, "arguments.site_id", "other")
    with pytest.raises(WebsiteSessionError):
        run(fixture.broker.acquire(fixture.session.session_id, "editor", "fixture-proof"))
    assert len(fixture.calls) == int(cached)


def prepare_extended_browser_change(fixture, *, pending=False):
    fixture.store.update(fixture.change, state="resolved", browser_ready=False)
    remote, probe = Remote(), Probe()
    remote.pending_extension = pending
    original = {
        "site_id": "main",
        "summary": "Change heading",
        "browser": True,
        "idempotency_key": "prepare-heading",
        "resources": [
            {
                "key": "post:7",
                "expected_version": digest(remote.value),
                "value": {"post_title": "After", "post_content": "Welcome"},
            }
        ],
    }

    def handle(request):
        route = parse_qs(request.url.query.decode()).get("rest_route", [request.url.path])[0]
        operation = route.split("/")[-1]
        if operation == "prepare-extend":
            return httpx.Response(
                200,
                json={
                    "snapshot": {
                        "meta:7:builder": {
                            "before": [],
                            "before_version": digest([]),
                            "value": ["New builder data"],
                        }
                    }
                },
            )
        if operation == "arm-extend":
            remote.calls.append((operation, json.loads(request.content)))
            return (
                httpx.Response(503)
                if remote.pending_extension
                else httpx.Response(200, json={"state": "browser_open"})
            )
        if operation == "close":
            remote.calls.append((operation, json.loads(request.content)))
            remote.state = "applied"
            return httpx.Response(200, json={"state": remote.state})
        return remote.handle(request)

    host = fixture.admin.connector_service
    service = Service(
        fixture.store.runtime_dir,
        Actor("root", fixture.session.session_id),
        host,
        authorize=lambda: host.authorize_current(
            fixture.session.session_id, "website", "prepare_change", original
        ),
        transport_factory=lambda _: httpx.MockTransport(handle),
        checks=probe,
        wait=async_no_wait,
    )
    prepared = run(service.prepare(original))
    remote.value, remote.state = dict(remote.desired), "browser_open"
    extension = {
        "site_id": "main",
        "summary": "Cover builder metadata",
        "browser": True,
        "idempotency_key": "extend-builder",
        "change_id": prepared["change_id"],
        "expected_revision": prepared["revision"],
        "resources": [
            {"key": "meta:7:builder", "expected_version": digest([]), "value": ["New builder data"]}
        ],
    }
    service.authorize = lambda: host.authorize_current(
        fixture.session.session_id, "website", "prepare_change", extension
    )
    extended = run(service.prepare(extension))
    assert bool(extended["ready"]) is not pending
    return SimpleNamespace(
        service=service,
        remote=remote,
        probe=probe,
        change=extended,
        original=original,
        extension=extension,
    )


@pytest.mark.parametrize("origin", ["session", "directory"])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("allowed_resource", ["post:7", "meta:7:builder"])
def test_extended_browser_cache_rechecks_every_preparation(
    browser_grant, origin, cached, allowed_resource
):
    fixture = browser_grant
    prepare_extended_browser_change(fixture)
    if cached:
        run(fixture.broker.acquire(fixture.session.session_id, "editor", "fixture-proof"))
    restrict_connector_arguments(
        fixture, origin, "arguments.resources[].key", [allowed_resource], operator="allowlist"
    )
    with pytest.raises(WebsiteSessionError):
        run(fixture.broker.acquire(fixture.session.session_id, "editor", "fixture-proof"))
    assert len(fixture.calls) == int(cached)


@pytest.mark.parametrize("origin", ["session", "directory"])
@pytest.mark.parametrize("stage", ["extension_pending", "queued"])
@pytest.mark.parametrize("revoked", ["initial", "extension", "extension-key"])
def test_background_browser_extensions_recover_when_any_preparation_is_revoked(
    browser_grant, origin, stage, revoked
):
    fixture = browser_grant
    prepared = prepare_extended_browser_change(fixture, pending=stage == "extension_pending")
    if stage == "queued":
        publication = {
            "site_id": "main",
            "change_id": prepared.change["change_id"],
            "expected_revision": prepared.change["revision"],
            "idempotency_key": "publish-heading",
        }
        prepared.service.authorize = lambda: fixture.admin.connector_service.authorize_current(
            fixture.session.session_id, "website", "apply_change", publication
        )
        run(prepared.service.queue(publication))
    if revoked == "extension-key":
        restrict_connector_arguments(
            fixture,
            origin,
            "arguments.idempotency_key",
            ["prepare-heading", "publish-heading", "browser-access"],
            operator="allowlist",
        )
    else:
        restrict_connector_arguments(
            fixture,
            origin,
            "arguments.resources[].key",
            ["post:7" if revoked == "extension" else "meta:7:builder"],
            operator="allowlist",
        )
    worker = background_service(
        SimpleNamespace(
            metadata={"directory_path": "root"},
            resolved_config=SimpleNamespace(runtime_dir=fixture.store.runtime_dir),
            admin_service=fixture.admin,
        ),
        fixture.store.get(prepared.change["change_id"], "root"),
    )
    worker.transport_factory, worker.checks, worker.wait = (
        prepared.service.transport_factory,
        prepared.probe,
        async_no_wait,
    )
    result = run(worker.execute("main", prepared.change["change_id"]))
    assert result["state"] == "restored"
    assert prepared.remote.value == {"post_title": "Before", "post_content": "Welcome"}
    assert len([name for name, _ in prepared.remote.calls if name == "arm-extend"]) == 1
    assert not any(name in {"apply", "close"} for name, _ in prepared.remote.calls)


def test_browser_extension_with_unrecorded_authorization_requires_reconciliation(browser_grant):
    fixture = browser_grant
    prepared = prepare_extended_browser_change(fixture)
    change = fixture.store.get(prepared.change["change_id"], "root")
    fixture.store.update(change, preparation_requests=None)
    with pytest.raises(WebsiteSessionError):
        run(fixture.broker.acquire(fixture.session.session_id, "editor", "fixture-proof"))
    assert not fixture.calls


@pytest.mark.parametrize("stage", ["extension_pending", "queued"])
def test_background_browser_extensions_preserve_authorized_retry_and_publication(
    browser_grant, stage
):
    fixture = browser_grant
    prepared = prepare_extended_browser_change(fixture, pending=stage == "extension_pending")
    if stage == "queued":
        publication = {
            "site_id": "main",
            "change_id": prepared.change["change_id"],
            "expected_revision": prepared.change["revision"],
            "idempotency_key": "publish-heading",
        }
        prepared.service.authorize = lambda: fixture.admin.connector_service.authorize_current(
            fixture.session.session_id, "website", "apply_change", publication
        )
        run(prepared.service.queue(publication))
    restrict_connector_arguments(
        fixture,
        "directory",
        "arguments.resources[].key",
        ["post:7", "meta:7:builder"],
        operator="allowlist",
    )
    worker = background_service(
        SimpleNamespace(
            metadata={"directory_path": "root"},
            resolved_config=SimpleNamespace(runtime_dir=fixture.store.runtime_dir),
            admin_service=fixture.admin,
        ),
        fixture.store.get(prepared.change["change_id"], "root"),
    )
    prepared.remote.pending_extension = False
    worker.transport_factory, worker.checks, worker.wait = (
        prepared.service.transport_factory,
        prepared.probe,
        async_no_wait,
    )
    result = run(worker.execute("main", prepared.change["change_id"]))
    assert result["state"] == ("prepared" if stage == "extension_pending" else "published")
    assert result["ready"]
    assert prepared.remote.value["post_title"] == "After"
    if stage == "extension_pending":
        assert run(fixture.broker.acquire(fixture.session.session_id, "editor", "fixture-proof"))[
            "storageState"
        ]["cookies"]
    else:
        assert any(name == "close" for name, _ in prepared.remote.calls)
    assert not any(name == "restore" for name, _ in prepared.remote.calls)


def test_adapter_managed_profile_can_save_lifetime_and_enabled_state(browser_grant):
    admin = browser_grant.admin.website_session_admin
    snapshot = admin.snapshot("root")
    profile = snapshot["profiles"][0]["profile"]
    updated = profile | {"enabled": False, "max_age_seconds": 600}
    saved = admin.save(
        "root",
        "website",
        {"expected_revision": snapshot["revision"], "profile": updated, "credentials": {}},
    )
    entry = saved["profiles"][0]
    assert entry["profile"] == updated
    assert entry["credentials"]["managed_by"] == "wordpress"
    assert entry["credentials"]["configured_fields"] == []


@pytest.mark.parametrize("stop", ["restricted", "allowed", "expired_browser"])
def test_background_publication_uses_original_arguments_and_current_policy(browser_grant, stop):
    from datetime import UTC, datetime, timedelta

    allowed = stop == "allowed"
    fixture = browser_grant
    fixture.store.update(fixture.change, state="resolved", browser_ready=False)
    remote, probe = Remote(), Probe()
    request = {
        "site_id": "main",
        "summary": "Change heading",
        "idempotency_key": "prepare-heading",
        "browser": stop == "expired_browser",
        "resources": [
            {
                "key": "post:7",
                "expected_version": digest(remote.value),
                "value": {"post_title": "After", "post_content": "Welcome"},
            }
        ],
    }
    host = fixture.admin.connector_service
    service = Service(
        fixture.store.runtime_dir,
        Actor("root", fixture.session.session_id),
        host,
        authorize=lambda: host.authorize_current(
            fixture.session.session_id, "website", "prepare_change", request
        ),
        transport_factory=lambda _: httpx.MockTransport(remote.handle),
        checks=probe,
        wait=async_no_wait,
    )
    prepared = run(service.prepare(request))
    if stop == "expired_browser":
        remote.value, remote.state = dict(remote.desired), "browser_open"
    publication = {
        "site_id": "main",
        "change_id": prepared["change_id"],
        "expected_revision": prepared["revision"],
        "idempotency_key": "explicit-publication",
    }
    service.authorize = lambda: host.authorize_current(
        fixture.session.session_id, "website", "apply_change", publication
    )
    queued = run(service.queue(publication))
    if stop == "expired_browser":
        fixture.session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        fixture.runtime.session_repository.upsert_session(fixture.session)
    else:
        restrict_connector_arguments(
            fixture,
            "directory",
            "arguments.idempotency_key" if allowed else "arguments.site_id",
            "explicit-publication" if allowed else "other",
        )
    worker = background_service(
        SimpleNamespace(
            metadata={"directory_path": "root"},
            resolved_config=SimpleNamespace(runtime_dir=fixture.store.runtime_dir),
            admin_service=fixture.admin,
        ),
        fixture.store.get(queued["change_id"], "root"),
    )
    worker.transport_factory, worker.checks, worker.wait = (
        service.transport_factory,
        probe,
        async_no_wait,
    )
    result = run(worker.execute("main", queued["change_id"]))
    assert result["state"] == (
        "restored" if stop == "expired_browser" else "published" if allowed else "uncertain"
    )
    assert remote.writes == int(allowed)
    assert remote.value["post_title"] == ("After" if allowed else "Before")


async def async_no_wait(seconds):
    pass


def test_idempotent_publish_and_changed_key_payload_conflict(rig):
    first = prepare(rig)
    # Retries keep the original request, including its original resource version.
    original = rig.store.get(first["change_id"], "root/design")["request"]
    assert run(rig.service.prepare(original))["change_id"] == first["change_id"]
    with pytest.raises(Conflict):
        run(rig.service.prepare({**original, "summary": "A different change"}))
    queued = queue(rig, first)
    assert queue(rig, first)["revision"] == queued["revision"]
    published = run(rig.service.execute("main", first["change_id"]))
    assert run(rig.service.execute("main", first["change_id"])) == published
    assert rig.remote.writes == 1


def test_lost_apply_reply_reconciles_after_new_service_process_without_duplicate_mutation(rig):
    change = queue(rig, prepare(rig))
    rig.remote.lost_reply = True
    assert run(rig.service.execute("main", change["change_id"]))["state"] == "uncertain"
    resumed = Service(
        rig.store.runtime_dir,
        rig.service.actor,
        rig.host,
        authorize=rig.service.authorize,
        checks=rig.probe,
        wait=rig.service.wait,
        transport_factory=rig.service.transport_factory,
    )
    assert run(resumed.execute("main", change["change_id"]))["state"] == "published"
    assert rig.remote.writes == 1


@pytest.mark.parametrize("interruption", ["lost_reply", "pending", "verification"])
def test_restore_retry_survives_worker_restart_without_another_request(rig, interruption):
    published = run(rig.service.execute("main", queue(rig, prepare(rig))["change_id"]))
    queued = queue(rig, published, restore=True)
    interrupted = False

    def handle(request):
        nonlocal interrupted
        if request.url.path.endswith("/restore") and not interrupted:
            interrupted = True
            if interruption == "pending":
                return httpx.Response(200, json={"state": "restoring"})
            response = rig.remote.handle(request)
            return httpx.Response(503) if interruption == "lost_reply" else response
        return rig.remote.handle(request)

    rig.service.transport_factory = lambda origin: httpx.MockTransport(handle)
    if interruption == "verification":
        rig.probe.results = [{"uncertain": True}, {"uncertain": True}]
    uncertain = run(rig.service.execute("main", queued["change_id"]))
    assert uncertain["state"] == "uncertain"
    assert uncertain["phase"] == "restore"
    events = pending_events(rig)
    assert len(events) == 1 and events[0].payload["change_id"] == queued["change_id"]
    rig.revoked[0] = True
    restored = run(resumed_service(rig).execute("main", events[0].payload["change_id"]))
    assert restored["state"] == "restored"
    assert rig.remote.value["post_title"] == "Before"
    assert not pending_events(rig)


@pytest.mark.parametrize("stop", ["expired", "disabled", "revoked"])
def test_pending_browser_extension_recovers_existing_edits(
    pending_browser_extension, monkeypatch, stop
):
    rig, pending = pending_browser_extension
    if stop == "expired":
        now = time.time() + 91 * 86400
        monkeypatch.setattr(time, "time", lambda: now)
        rig.store.prune("root/design", 90)
    elif stop == "disabled":
        revision, settings = rig.store.settings("root/design")
        settings.sites["main"].enabled = False
        rig.store.save_settings("root/design", revision, settings)
    else:
        rig.revoked[0] = True
    assert pending_events(rig)
    restored = run(resumed_service(rig).execute("main", pending["change_id"]))
    assert restored["state"] == "restored"
    assert rig.remote.value["post_title"] == "Before"
    assert len([name for name, _ in rig.remote.calls if name == "arm-extend"]) == 1
    assert not pending_events(rig)


@pytest.mark.parametrize("state", ["queued", "uncertain"])
@pytest.mark.parametrize("stop", ["expired", "disabled", "revoked"])
def test_queued_browser_changes_recover_after_publication_authorization_is_lost(
    browser_change, monkeypatch, state, stop
):
    rig, prepared = browser_change
    queued = queue(rig, prepared)
    if state == "uncertain":
        rig.store.update(rig.store.get(queued["change_id"], "root/design"), state="uncertain")
    if stop == "expired":
        monkeypatch.setattr(time, "time", lambda: prepared["expires_at"] + 1)
    elif stop == "disabled":
        revision, settings = rig.store.settings("root/design")
        settings.sites["main"].enabled = False
        rig.store.save_settings("root/design", revision, settings)
    else:
        rig.revoked[0] = True
    assert queued["change_id"] in {event.payload["change_id"] for event in pending_events(rig)}
    result = run(resumed_service(rig).execute("main", queued["change_id"]))
    assert result["state"] == "restored"
    assert rig.remote.value == {"post_title": "Before", "post_content": "Welcome"}
    assert not any(operation in {"apply", "close"} for operation, args in rig.remote.calls)
    assert not pending_events(rig)


def test_operator_can_undo_pending_browser_extension(pending_browser_extension):
    rig, pending = pending_browser_extension
    rig.revoked[0] = True
    operator = resumed_service(rig, operator=True)
    queued = run(
        operator.queue(
            {
                "site_id": "main",
                "change_id": pending["change_id"],
                "expected_revision": pending["revision"],
                "idempotency_key": "operator-undo",
            },
            restore=True,
        )
    )
    restored = run(resumed_service(rig).execute("main", queued["change_id"]))
    assert restored["state"] == "restored"
    assert rig.remote.value["post_title"] == "Before"


@pytest.mark.parametrize("directory", ["root/design", "root/other"])
def test_pending_browser_extension_reserves_origin_across_directories(
    pending_browser_extension, directory
):
    rig, pending = pending_browser_extension
    if directory != "root/design":
        _, settings = rig.store.settings("root/design")
        rig.store.save_settings(directory, 0, settings)
    other = resumed_service(rig)
    other.directory = directory
    other.actor = Actor(directory, "another-session")
    with pytest.raises(Conflict):
        run(
            other.prepare(
                {
                    "site_id": "main",
                    "summary": "Competing edit",
                    "idempotency_key": "competing-edit",
                    "resources": [
                        {
                            "key": "post:7",
                            "expected_version": digest(rig.remote.value),
                            "value": {"post_title": "Competing heading"},
                        }
                    ],
                }
            )
        )
    assert rig.remote.value["post_title"] == "After"
    assert rig.service.status("main", pending["change_id"])["state"] == "prepared"


def failure(id, objective=True):
    return {"failures": [{"id": id, "objective": objective}], "screenshots": []}


@pytest.mark.parametrize(
    "baseline,after,expected",
    [
        ({"failures": []}, failure("server"), "restored"),
        (failure("server"), failure("server"), "published"),
        ({"failures": []}, failure("aesthetic", False), "published"),
    ],
)
def test_only_new_confirmed_objective_regressions_auto_restore(rig, baseline, after, expected):
    rig.probe.results = [baseline]
    change = queue(rig, prepare(rig))
    rig.probe.results = [after, after, baseline, baseline]
    assert run(rig.service.execute("main", change["change_id"]))["state"] == expected


def test_restore_conflict_preserves_human_edit_and_blocks_further_changes(rig):
    change = queue(rig, prepare(rig))
    published = run(rig.service.execute("main", change["change_id"]))
    rig.remote.value = {"post_title": "Human edit", "post_content": "Welcome"}
    queued = queue(rig, published, restore=True)
    assert run(rig.service.execute("main", queued["change_id"]))["state"] == "recovery_required"
    assert rig.remote.value["post_title"] == "Human edit"
    with pytest.raises(Conflict):
        prepare(rig, idempotency_key="another-change")


def test_operator_can_verify_and_keep_human_edits_to_resolve_a_conflict(rig):
    prepared = prepare(rig)
    published = run(rig.service.execute("main", queue(rig, prepared)["change_id"]))
    rig.remote.value = {"post_title": "Human heading", "post_content": "Welcome"}
    unresolved = run(rig.service.execute("main", queue(rig, published, restore=True)["change_id"]))
    payload = {
        "site_id": "main",
        "change_id": unresolved["change_id"],
        "expected_revision": unresolved["revision"],
        "idempotency_key": "keep-human-heading",
    }
    with pytest.raises(PermissionError):
        run(rig.service.resolve(payload))
    rig.service.actor = Actor("root/design", "operator:reviewer", True)
    rig.probe.results = [failure("/:1280:server"), failure("/:1280:server")]
    with pytest.raises(RuntimeError, match="confirmed regressions"):
        run(rig.service.resolve(payload))
    payload["expected_revision"] = rig.service.status("main", unresolved["change_id"])["revision"]
    resolved = run(rig.service.resolve(payload))
    assert (
        resolved["state"] == "resolved" and resolved["resolution"]["actor"] == "operator:reviewer"
    )
    assert rig.remote.value["post_title"] == "Human heading"
    assert run(rig.service.resolve(payload))["state"] == "resolved"
    assert prepare(rig, idempotency_key="next-after-human-review")["ready"]


def test_active_recovery_binding_cannot_be_removed_but_can_be_disabled(rig):
    prepared = prepare(rig)
    queue(rig, prepared)
    revision, settings = rig.store.settings("root/design")
    with pytest.raises(Conflict, match="before rebinding"):
        rig.store.save_settings("root/design", revision, Settings())
    settings.sites["main"].enabled = False
    assert rig.store.save_settings("root/design", revision, settings) == revision + 1


def test_revoked_actor_cannot_write_and_foreign_directory_cannot_read(rig):
    prepared = prepare(rig)
    rig.revoked[0] = True
    with pytest.raises(PermissionError):
        queue(rig, prepared)
    assert rig.remote.writes == 0
    with pytest.raises(Conflict):
        rig.store.get(prepared["change_id"], "root/other")


def test_incomplete_baseline_and_unverified_recovery_reject_preparation(rig):
    rig.probe.results = [{"uncertain": True}]
    with pytest.raises(Exception, match="Baseline"):
        prepare(rig)
    assert rig.remote.writes == 0
    revision, settings = rig.store.settings("root/design")
    settings.sites["main"].recovery_verified = None
    rig.store.save_settings("root/design", revision, settings)
    with pytest.raises(Exception, match="restore probe"):
        prepare(rig, idempotency_key="new-attempt")


def test_site_config_cas_and_protocol_registration(tmp_path):
    store = Store(tmp_path)
    store.save_settings("root", 0, Settings())
    with pytest.raises(Conflict):
        store.save_settings("root", 0, Settings())
    addon = register_addon()
    assert "wordpress" in addon.connector_drivers and "wordpress" in addon.website_session_adapters
    plugin = register_providers()
    assert "effect:wordpress:execute-change" in plugin.effect_providers
    with zipfile.ZipFile(io.BytesIO(plugin_zip())) as archive:
        assert set(archive.namelist()) == {
            "evander-wordpress/evander-wordpress.php",
            "evander-wordpress/resources.php",
        }
        assert archive.testzip() is None
    assert plugin_zip() == plugin_zip()


def test_trigger_retries_pending_jobs_and_retains_current_recovery_baseline(rig):
    prepared = prepare(rig)
    queued = queue(rig, prepared)
    trigger = WordPressReadyProvider()
    trigger.initialize(
        ReadyDefinition(id="ready", display_name="Ready", type="trigger:wordpress:change-ready"),
        SimpleNamespace(
            metadata={"directory_path": "root/design"},
            resolved_config=SimpleNamespace(runtime_dir=rig.store.runtime_dir),
        ),
    )
    events = trigger.poll()
    assert events[0].payload["change_id"] == queued["change_id"]
    assert trigger.poll()[0].event_id == events[0].event_id
    assert rig.store.prune("root/design", 90) is None
    assert rig.store.get(queued["change_id"], "root/design")["state"] == "queued"


@pytest.mark.parametrize("state", ["queued", "restoring"])
def test_pending_recovery_is_not_hidden_by_newer_inactive_preparations(rig, state):
    actionable = rig.store.create("root/design", "main", rig.site.origin, {"ready": True})
    rig.store.update(actionable, state=state)
    for index in range(501):
        rig.store.create(
            "root/design",
            "main",
            rig.site.origin,
            {"ready": True, "summary": f"Unpublished draft {index}"},
        )
    assert [event.payload["change_id"] for event in pending_events(rig)] == [
        actionable["change_id"]
    ]


def test_site_retention_does_not_shorten_another_sites_undo_window(rig, monkeypatch):
    revision, settings = rig.store.settings("root/design")
    settings.sites["main"].retention_days = 90
    settings.sites["short"] = Site(
        origin="https://short.example.test", connector_id="website", retention_days=7
    )
    rig.store.save_settings("root/design", revision, settings)
    older = {}
    now = time.time()
    for site_id in ["main", "short"]:
        origin = settings.sites[site_id].origin
        with monkeypatch.context() as past:
            past.setattr(time, "time", lambda: now - 20 * 86400)
            row = rig.store.create("root/design", site_id, origin, {})
            older[site_id] = row["change_id"]
            rig.store.update(row, state="published")
        newest = rig.store.create("root/design", site_id, origin, {})
        rig.store.update(newest, state="published")
    assert not pending_events(rig)
    assert rig.store.get(older["main"], "root/design")["state"] == "published"
    with pytest.raises(Conflict):
        rig.store.get(older["short"], "root/design")


def test_example_workflow_imports_and_routes_queued_change_to_publication(rig, monkeypatch):
    pack = WorkflowPack.model_validate_json(
        (Path(__file__).parents[1] / "examples/workflow-packs/wordpress.v1-example.json").read_text()
    )
    trigger_node, effect_node = (entry.node for entry in pack.ir.nodes)
    context = SimpleNamespace(
        metadata={"directory_path": pack.graphs[0].directory_path},
        resolved_config=SimpleNamespace(runtime_dir=rig.store.runtime_dir),
    )
    queued = queue(rig, prepare(rig))
    trigger = WordPressReadyProvider()
    trigger.initialize(trigger.validate_definition(trigger_node), context)
    event = trigger.poll()[0]
    edge = pack.ir.edges[0]
    assert (edge.source_id, edge.target_id) == (event.trigger_id, effect_node.id)
    bound = resolve_workflow_bindings(
        effect_node.config,
        {event.trigger_id: {"payload": event.payload}},
        {event.trigger_id: {"payload"}},
    )
    monkeypatch.setattr(
        "evander_wordpress_addon.providers.background_service", lambda context, change: rig.service
    )
    effect = WordPressEffectProvider()
    effect.initialize(effect.validate_definition(effect_node), context)
    result = effect.execute(resolved_config=bound.resolved_config)
    assert result["change_id"] == queued["change_id"]
    assert result["state"] == "published"
    assert rig.remote.value["post_title"] == "After"


def test_cookie_state_rejects_foreign_domains_insecure_or_overlong_ttl():
    def state(**changes):
        return {
            "origin": "https://design.example.test",
            "expiresAt": 3000,
            "storageState": {
                "origins": [],
                "cookies": [
                    {
                        "name": "session",
                        "value": "temporary",
                        "domain": "design.example.test",
                        "path": "/",
                        "secure": True,
                        "httpOnly": True,
                        "sameSite": "Lax",
                        "expires": 3000,
                        **changes,
                    }
                ],
            },
            "sessionStorage": {},
        }

    result = validate_browser_state(state(), "https://design.example.test", 1000, 1900)
    assert result["expiresAt"] == result["storageState"]["cookies"][0]["expires"] == 1900
    for values in [
        {"domain": "elsewhere.example.test"},
        {"secure": False},
        {"expires": float("inf")},
    ]:
        with pytest.raises(WebsiteSessionError):
            validate_browser_state(state(**values), "https://design.example.test", 1000, 1900)


def archive_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return stream.getvalue()


@pytest.fixture
def hosting(tmp_path):
    root = tmp_path / "site"
    initial = root / "releases" / "initial"
    initial.mkdir(parents=True)
    (initial / "index.php").write_text("<?php echo 'Before';")
    (root / "current").symlink_to("releases/initial")
    return Helper({"components": {"theme": str(root)}}), root


def test_ssh_helper_switches_and_restores_without_running_php(hosting):
    helper, root = hosting
    archive = archive_bytes({"index.php": "<?php fatal_function_that_does_not_exist();"})
    payload = {
        "protocol_version": 1,
        "component": "theme",
        "operation_id": "change-one",
        "fence": 1,
        "artifact_sha256": sha256(archive).hexdigest(),
        "artifact_archive": base64.b64encode(archive).decode(),
    }
    baseline = helper.execute({**payload, "action": "prepare"})
    deployed = helper.execute(
        {**payload, "action": "deploy", "expected_release": baseline["expected_release"]}
    )
    assert deployed["state"] == "deployed"
    assert "fatal_function" in (root / "current" / "index.php").read_text()
    restored = helper.execute(
        {**payload, "action": "restore", "expected_release": deployed["release_version"]}
    )
    assert (
        restored["state"] == "restored"
        and (root / "current" / "index.php").read_text() == "<?php echo 'Before';"
    )


@pytest.mark.parametrize("retained", [False, True])
def test_ssh_helper_publishes_traversable_directories_under_private_umask(hosting, retained):
    import stat

    helper, root = hosting
    archive = archive_bytes(
        {
            "index.php": "<?php echo 'After';",
            "inc/bootstrap.php": "<?php echo 'Included';",
            "assets/css/site.css": "body { color: navy; }",
            "images/icons/empty/": "",
        }
    )
    payload = {
        "protocol_version": 1,
        "component": "theme",
        "operation_id": "private-umask",
        "fence": 1,
        "artifact_sha256": sha256(archive).hexdigest(),
        "artifact_archive": base64.b64encode(archive).decode(),
    }
    script = """import json, os, sys
from evander_wordpress_addon.ssh_helper import Helper
config, payload = json.load(sys.stdin)
os.umask(0o077)
helper = Helper(config)
prepared = helper.execute({**payload, 'action': 'prepare'})
print(json.dumps(helper.execute({**payload, 'action': 'deploy',
                                'expected_release': prepared['expected_release']})))
"""

    def deploy():
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=json.dumps([helper.config, payload]),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        assert json.loads(result.stdout)["state"] == "deployed"

    deploy()
    release = (root / "current").resolve()
    if retained:
        # Reusing an artifact created by an older helper must repair its modes too.
        for path in [release, *(row for row in release.rglob("*") if row.is_dir())]:
            path.chmod(0o700)
        payload.update(operation_id="retained-umask", fence=2)
        deploy()
    for path in [release, *release.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) == (0o755 if path.is_dir() else 0o644)
    assert stat.S_IMODE((root / "state").stat().st_mode) == 0o700
    assert (root / "current/assets/css/site.css").read_text() == "body { color: navy; }"


def test_ssh_helper_can_undo_a_release_whose_activation_fails(hosting):
    helper, root = hosting
    activation = root / "activate.py"
    activation.write_text(
        "import pathlib, sys\n"
        "live = pathlib.Path(sys.argv[1]).resolve()\n"
        "sys.exit(0 if live.name == 'initial' else 1)\n"
    )
    helper.config["post_switch_command"] = [sys.executable, str(activation), str(root / "current")]
    archive = archive_bytes({"index.php": "<?php missing_function();"})
    payload = {
        "protocol_version": 1,
        "component": "theme",
        "operation_id": "activation-failure",
        "fence": 1,
        "artifact_sha256": sha256(archive).hexdigest(),
        "artifact_archive": base64.b64encode(archive).decode(),
    }
    baseline = helper.execute({**payload, "action": "prepare"})
    with pytest.raises(subprocess.CalledProcessError):
        helper.execute(
            {**payload, "action": "deploy", "expected_release": baseline["expected_release"]}
        )
    restarted = Helper(helper.config)
    status = restarted.execute({**payload, "action": "status"})
    assert status["state"] == "deploying"
    assert "missing_function" in (root / "current/index.php").read_text()
    restored = restarted.execute(
        {**payload, "action": "restore", "expected_release": status["release_version"]}
    )
    assert restored["state"] == "restored"
    assert (root / "current/index.php").read_text() == "<?php echo 'Before';"


@pytest.mark.parametrize(
    "action, pending_state, finished_state",
    [
        ("deploy", "deploying", "deployed"),
        ("restore", "restoring", "restored"),
    ],
)
def test_ssh_helper_status_retries_interrupted_activation(
    hosting, action, pending_state, finished_state
):
    helper, root = hosting
    blocked = root / "activation-blocked"
    activation = root / "activate.py"
    activation.write_text(
        "import pathlib, sys\nsys.exit(1 if pathlib.Path(sys.argv[1]).exists() else 0)\n"
    )
    helper.config["post_switch_command"] = [sys.executable, str(activation), str(blocked)]
    archive = archive_bytes({"index.php": "<?php echo 'After';"})
    payload = {
        "protocol_version": 1,
        "component": "theme",
        "operation_id": "activation-retry",
        "fence": 1,
        "artifact_sha256": sha256(archive).hexdigest(),
        "artifact_archive": base64.b64encode(archive).decode(),
    }
    baseline = helper.execute({**payload, "action": "prepare"})
    expected = baseline["expected_release"]
    if action == "restore":
        deployed = helper.execute({**payload, "action": "deploy", "expected_release": expected})
        expected = deployed["release_version"]
    blocked.touch()
    with pytest.raises(subprocess.CalledProcessError):
        helper.execute({**payload, "action": action, "expected_release": expected})
    restarted = Helper(helper.config)
    assert restarted.execute({**payload, "action": "status"})["state"] == pending_state
    blocked.unlink()
    assert restarted.execute({**payload, "action": "status"})["state"] == finished_state
    expected_content = "<?php echo 'After';" if action == "deploy" else "<?php echo 'Before';"
    assert (root / "current/index.php").read_text() == expected_content


@pytest.mark.parametrize("name", ["../escape.php", "/absolute.php", "dir/../../escape", "a\\b"])
def test_ssh_helper_rejects_artifact_path_escape(hosting, name):
    helper, root = hosting
    archive = archive_bytes({name: "bad"})
    with pytest.raises(ValueError):
        helper.install(
            root,
            {
                "artifact_archive": base64.b64encode(archive).decode(),
                "artifact_sha256": sha256(archive).hexdigest(),
            },
        )
    assert (root / "current" / "index.php").read_text() == "<?php echo 'Before';"


def test_ssh_helper_preserves_intervening_manual_file_edit(hosting):
    helper, root = hosting
    archive = archive_bytes({"index.php": "<?php echo 'After';"})
    payload = {
        "protocol_version": 1,
        "component": "theme",
        "operation_id": "change-two",
        "fence": 1,
        "artifact_sha256": sha256(archive).hexdigest(),
        "artifact_archive": base64.b64encode(archive).decode(),
    }
    baseline = helper.execute({**payload, "action": "prepare"})
    deployed = helper.execute(
        {**payload, "action": "deploy", "expected_release": baseline["expected_release"]}
    )
    (root / "current" / "index.php").write_text("Human edit")
    result = helper.execute(
        {**payload, "action": "restore", "expected_release": deployed["release_version"]}
    )
    assert result["conflict"] and (root / "current" / "index.php").read_text() == "Human edit"


def test_admin_settings_and_restore_enforce_directory_permissions_and_server_owned_probe(tmp_path):
    class Auth:
        allowed = False
        unlocked = False

        def require(self, request):
            return {"login": "operator"}

        def require_permission(self, request, permission, directory_path):
            if not self.allowed:
                raise PermissionError

        def privileged_for_request(self, request):
            return self.unlocked

    auth = Auth()
    context = AddonContext(
        "wordpress-addon",
        tmp_path,
        tmp_path / "addons" / "wordpress-addon",
        auth=auth,
        service=SimpleNamespace(connector_service=None),
    )
    app = Starlette(routes=admin_api_routes(context))

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://admin.example.test"
        ) as client:
            assert (await client.get("/settings?directory=root/design")).status_code == 403
            auth.allowed = True
            assert (await client.get("/settings?directory=root/design")).status_code == 200
            for path, method, body in [
                ("/settings", "PUT", {"expected_revision": 0, "settings": {"sites": {}}}),
                ("/test", "POST", {"site_id": "main"}),
                ("/changes/wp_" + "a" * 32 + "/restore", "POST", {"site_id": "main"}),
                ("/changes/wp_" + "a" * 32 + "/resolve", "POST", {"site_id": "main"}),
            ]:
                assert (await client.request(method, path + "?directory=root/design", json=body)).status_code == 403
            assert (await client.get("/settings?directory=root/design")).json()["revision"] == 0
            auth.unlocked = True
            payload = {
                "expected_revision": 0,
                "settings": {
                    "sites": {
                        "main": {
                            "origin": "https://design.example.test",
                            "connector_id": "website",
                            "enabled": True,
                            "recovery_verified": "forged",
                        }
                    }
                },
            }
            assert (
                await client.put("/settings?directory=root/design", json=payload)
            ).status_code == 400
            payload["settings"]["sites"]["main"]["enabled"] = False
            assert (
                await client.put("/settings?directory=root/design", json=payload)
            ).status_code == 200
            assert (
                await client.put("/settings?directory=root/design", json=payload)
            ).status_code == 409
            result = (await client.get("/settings?directory=root/design")).json()
            assert result["settings"]["sites"]["main"]["recovery_verified"] is None

    run(exercise())


@pytest.mark.parametrize("endpoint", ["test", "restore", "resolve"])
@pytest.mark.parametrize("attack", ["text_plain", "foreign_origin", "foreign_json", "null_origin"])
def test_admin_recovery_rejects_cross_origin_and_simple_requests(rig, monkeypatch, endpoint, attack):
    from evander_wordpress_addon import api

    prepared = prepare(rig)
    published = run(rig.service.execute("main", queue(rig, prepared)["change_id"]))
    if endpoint == "resolve":
        stored = rig.store.get(published["change_id"], "root/design")
        published = rig.service.receipt(rig.store.update(stored, state="conflict"))
    operator = resumed_service(rig, operator=True)
    monkeypatch.setattr(api, "Service", lambda *args: operator)
    original = rig.remote.handle

    def remote(request):
        if request.url.path.endswith("/probe"):
            return httpx.Response(200, json={"protocol_version": 1, "restore_verified": True})
        return original(request)

    operator.transport_factory = lambda origin: httpx.MockTransport(remote)
    auth = SimpleNamespace(require=lambda request: {"login": "operator"},
        require_permission=lambda *args, **kwargs: None, privileged_for_request=lambda request: True)
    context = AddonContext("wordpress-addon", rig.store.runtime_dir, rig.store.runtime_dir / "addons",
                           auth=auth, service=SimpleNamespace(connector_service=rig.host))
    app = Starlette(routes=admin_api_routes(context))
    path = "/test" if endpoint == "test" else "/changes/" + published["change_id"] + "/" + endpoint
    payload = {"site_id": "main", "expected_revision": published["revision"], "idempotency_key": "operator-recovery"}
    headers = {"Content-Type": "text/plain" if attack in {"text_plain", "foreign_origin"} else "application/json"}
    if attack != "text_plain":
        headers["Origin"] = "null" if attack == "null_origin" else "https://evil.example.test"

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="https://admin.example.test") as client:
            response = await client.post(path + "?directory=root/design", content=json.dumps(payload), headers=headers)
            assert response.status_code == 403
            assert rig.store.get(published["change_id"], "root/design")["revision"] == published["revision"]
            response = await client.post(path + "?directory=root/design", content=json.dumps(payload),
                headers={"Content-Type": "Application/JSON; charset=utf-8", "Origin": "https://admin.example.test:443"})
            assert response.status_code == 200, response.text
            if endpoint != "test":
                assert response.json()["state"] == ("restoring" if endpoint == "restore" else "resolved")

    run(exercise())


@pytest.mark.parametrize("action", ["prepare", "deploy", "restore"])
def test_ssh_response_limit_stops_and_reaps_streaming_helper(monkeypatch, action):
    from evander_wordpress_addon import deployment

    create = asyncio.create_subprocess_exec
    processes = []

    async def helper(*args, **kwargs):
        process = await create(sys.executable, "-c",
            "import sys,time; sys.stdout.write('x' * 262144); sys.stdout.flush(); time.sleep(60)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(deployment.asyncio, "create_subprocess_exec", helper)
    adapter = SSHAdapter.__new__(SSHAdapter)
    adapter.config = {"host": "hosting.example.test", "user": "publisher",
                      "key_file": "/synthetic/key", "known_hosts_file": "/synthetic/known_hosts"}

    async def exercise():
        expected = UnknownOutcome if action in {"deploy", "restore"} else WordPressError
        with pytest.raises(expected):
            await asyncio.wait_for(adapter.call(action, {"artifact_archive": "a" * 1024**2}), 5)
        assert processes[0].returncode is not None

    run(exercise())


def test_ssh_reads_output_while_sending_large_artifact(monkeypatch):
    from evander_wordpress_addon import deployment

    create = asyncio.create_subprocess_exec

    async def helper(*args, **kwargs):
        return await create(sys.executable, "-c", """import json,sys
sys.stdout.write(' ' * 100000); sys.stdout.flush()
payload = json.load(sys.stdin)
print(json.dumps({'protocol_version':1,'state':'prepared','received':len(payload['artifact_archive'])}))
""", **kwargs)

    monkeypatch.setattr(deployment.asyncio, "create_subprocess_exec", helper)
    adapter = SSHAdapter.__new__(SSHAdapter)
    adapter.config = {"host": "hosting.example.test", "user": "publisher",
                      "key_file": "/synthetic/key", "known_hosts_file": "/synthetic/known_hosts"}
    result = run(asyncio.wait_for(adapter.call("prepare", {"artifact_archive": "a" * 1024**2}), 5))
    assert result == {"protocol_version": 1, "state": "prepared", "received": 1024**2}


@pytest.mark.parametrize("action", ["prepare", "deploy"])
def test_ssh_timeout_reaps_helper_and_keeps_mutation_reconciliation(monkeypatch, action):
    from evander_wordpress_addon import deployment

    create, timeout = asyncio.create_subprocess_exec, asyncio.timeout
    processes = []

    async def helper(*args, **kwargs):
        process = await create(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(deployment.asyncio, "create_subprocess_exec", helper)
    monkeypatch.setattr(deployment.asyncio, "timeout", lambda seconds: timeout(0.1))
    adapter = SSHAdapter.__new__(SSHAdapter)
    adapter.config = {"host": "hosting.example.test", "user": "publisher",
                      "key_file": "/synthetic/key", "known_hosts_file": "/synthetic/known_hosts"}

    async def exercise():
        with pytest.raises(UnknownOutcome if action == "deploy" else WordPressError):
            await adapter.call(action, {})
        assert processes[0].returncode is not None

    run(exercise())


@pytest.mark.parametrize(
    "scenario",
    [
        "fatal",
        "manual_edit",
        "restore_conflict",
        "adapter_unavailable",
        "hosting_unavailable",
        "expiry_before_build",
        "expiry_during_build",
        "expiry_during_deploy",
        "expiry_apply_race",
        "expiry_uncertain_deploy",
        "expiry_applied_reply",
        "expiry_source_only",
        "expiry_unexpired_control",
        "custom_prefix_hosted",
        "custom_prefix_explicit_hosted",
        "custom_prefix_dedicated_builder",
    ],
)
def test_pinned_git_build_can_resume_and_recover_a_broken_deployment(
    rig, hosting, tmp_path, scenario, monkeypatch
):
    from evander.admin.services.managed_git_service import ManagedGitAdminService

    helper, root = hosting
    expiry_scenario = scenario.startswith("expiry_")
    prefix_scenario = scenario.startswith("custom_prefix_")
    prefix = "/design-releases/v2" if prefix_scenario else "/evander-releases/v1"
    candidate = (
        "<?php echo 'After';" if expiry_scenario or prefix_scenario else "<?php missing_function();"
    )
    clock = [time.time()]
    if expiry_scenario:
        monkeypatch.setattr(time, "time", lambda: clock[0])
    expiry, deployments = [None], []
    source = tmp_path / "source"
    source.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=source, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Synthetic designer")
    git("config", "user.email", "designer@example.test")
    (source / "functions.php").write_text(candidate)
    git("add", "functions.php")
    git("commit", "-m", "Synthetic pinned candidate")
    sha, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    managed = ManagedGitAdminService(
        SimpleNamespace(runtime_dir=rig.store.runtime_dir), SimpleNamespace(), tmp_path
    )
    repo = managed.register_repository("root/design", str(source), default_branch="main")
    revision, settings = rig.store.settings("root/design")
    settings.sites["main"] = Site(
        origin=rig.site.origin,
        connector_id="website",
        enabled=True,
        capabilities={"cms", "source"},
        source={
            "managed_repo_id": repo["managed_repo_id"],
            "component": "theme",
            "required_checks": ["php-lint"],
            "build_connector_id": (
                "builder"
                if scenario == "custom_prefix_dedicated_builder"
                else "hosting"
                if scenario == "custom_prefix_explicit_hosted"
                else None
            ),
        },
        deployment={"kind": "pipeline", "connector_id": "hosting", "prefix": prefix},
    )
    settings.sites["main"].recovery_verified = settings.sites["main"].binding()
    rig.store.save_settings("root/design", revision, settings)
    website = rig.host.provider_config.resolve_connectors_for_directory("root/design").connectors[
        "website"
    ]
    pipeline = ConnectorDefinition(
        display_name="Hosting",
        description="Hosting",
        driver="rest",
        config={"base_url": "https://hosting.example.test"},
        operations={
            "status": operation_definitions()["discover"].model_copy(
                update={"config": {"method": "POST", "path": prefix + "/status"}}
            )
        },
    )
    config = ConnectorDirectoryConfig(connectors={"website": website, "hosting": pipeline})
    if scenario == "custom_prefix_dedicated_builder":
        config.connectors["builder"] = pipeline.model_copy(
            update={"config": {"base_url": "https://build.example.test"}}
        )
    rig.host.provider_config.resolve_connectors_for_directory = lambda directory: config

    async def auth(directory, connector):
        return config.connectors[connector], {}

    rig.host.host_auth = auth
    rig.host.host_services["managed_git"] = lambda: managed
    artifact = archive_bytes({"index.php": candidate})
    builds = [0]

    def handle(request):
        if request.url.host == "design.example.test":
            if "missing_function" in (root / "current/index.php").read_text():
                return httpx.Response(503)
            operation = request.url.path.rsplit("/", 1)[-1]
            if expiry_scenario and operation == "apply":
                if scenario == "expiry_apply_race":
                    clock[0] = expiry[0] + 1
                if clock[0] >= expiry[0] and rig.remote.state != "applied":
                    return httpx.Response(409)
            response = rig.remote.handle(request)
            if (
                scenario == "expiry_applied_reply"
                and operation == "apply"
                and response.status_code == 500
            ):
                clock[0] = expiry[0] + 1
            return response
        payload = json.loads(request.content)
        action = request.url.path.rsplit("/", 1)[-1]
        expected_prefix = (
            "/evander-releases/v1" if request.url.host == "build.example.test" else prefix
        )
        if request.url.path != expected_prefix + "/" + action:
            return httpx.Response(404)
        if scenario == "hosting_unavailable":
            return httpx.Response(503)
        if action == "prepare" and payload["operation_id"].endswith(":build"):
            builds[0] += 1
            submitted = base64.b64decode(payload["source_archive"])
            assert sha256(submitted).hexdigest() == payload["source_sha256"]
            with zipfile.ZipFile(io.BytesIO(submitted)) as archive:
                assert archive.read("functions.php") == candidate.encode()
            if builds[0] == 1:
                return httpx.Response(200, json={"protocol_version": 1, "state": "building"})
            if scenario == "expiry_during_build":
                clock[0] = expiry[0] + 1
            return httpx.Response(
                200,
                json={
                    "protocol_version": 1,
                    "commit_sha": sha,
                    "execution_backend": "container_worker_vm",
                    "checks": {"php-lint": True},
                    "artifact_sha256": sha256(artifact).hexdigest(),
                    "artifact_archive": base64.b64encode(artifact).decode(),
                },
            )
        if action == "restore" and scenario == "restore_conflict":
            (root / "current/index.php").write_text("Human edit during recovery")
        result = helper.execute({"protocol_version": 1, "action": action, **payload})
        if action == "deploy":
            deployments.append(result)
            if scenario in {"expiry_during_deploy", "expiry_uncertain_deploy"}:
                clock[0] = expiry[0] + 1
                if scenario == "expiry_uncertain_deploy":
                    return httpx.Response(503)
        return httpx.Response(200, json=result)

    rig.service.transport_factory = lambda origin: httpx.MockTransport(handle)
    if scenario == "adapter_unavailable":

        async def unavailable_adapter(*args):
            raise WordPressError("Hosting adapter unavailable")

        rig.service.deployments.adapter = unavailable_adapter
    request = {
        "site_id": "main",
        "summary": "Deploy a pinned website template",
        "idempotency_key": "source-pinned",
        "code": {"source_ref": "refs/heads/main", "commit_sha": sha, "tree_hash": tree},
        "resources": [
            {
                "key": "post:7",
                "expected_version": digest(rig.remote.value),
                "value": {"post_title": "After"},
            }
        ],
    }
    if scenario == "expiry_source_only":
        request["resources"] = []
    if scenario in {"adapter_unavailable", "hosting_unavailable"}:
        with pytest.raises(WordPressError):
            run(rig.service.prepare(request))
        failed = rig.store.list("root/design")[0]
        assert failed["state"] == "failed"
        pin = failed["build_pending"]["pin"]
        assert (
            managed.resolve_snapshot(
                repo["managed_repo_id"], "root/design", source_ref=pin["pin_ref"], commit_sha=sha
            )["commit_sha"]
            == sha
        )
        trigger = WordPressReadyProvider()
        trigger.initialize(
            ReadyDefinition(
                id="ready", display_name="Ready", type="trigger:wordpress:change-ready"
            ),
            SimpleNamespace(
                metadata={"directory_path": "root/design"},
                resolved_config=SimpleNamespace(runtime_dir=rig.store.runtime_dir),
                admin_service=SimpleNamespace(connector_service=rig.host),
            ),
        )
        later = time.time() + 100 * 86400
        with monkeypatch.context() as future:
            future.setattr(time, "time", lambda: later)
            assert not trigger.poll()
        with pytest.raises(Conflict):
            rig.store.get(failed["change_id"], "root/design")
        assert managed.delete_repository(repo["managed_repo_id"])["deleted"] is True
        return
    prepared = run(rig.service.prepare(request))
    assert prepared["build_pending"]
    expiry[0] = int(prepared["expires_at"])
    if scenario == "manual_edit":
        (root / "current/index.php").write_text("Manual edit during build")
    queued = queue(rig, prepared)
    if prefix_scenario:
        result = run(resumed_service(rig).execute("main", queued["change_id"]))
        assert result["state"] == "published"
        assert builds[0] == 2 and len(deployments) == 1
        assert (root / "current/index.php").read_text() == candidate
        assert rig.remote.value["post_title"] == "After"
        return
    if expiry_scenario:
        if scenario in {"expiry_before_build", "expiry_source_only"}:
            clock[0] = expiry[0] + 1
        if scenario == "expiry_applied_reply":
            rig.remote.lost_reply = True
        result = run(rig.service.execute("main", queued["change_id"]))
        if scenario in {"expiry_uncertain_deploy", "expiry_applied_reply"}:
            assert result["state"] == "uncertain"
            result = run(resumed_service(rig).execute("main", queued["change_id"]))
        publishes = scenario in {
            "expiry_unexpired_control",
            "expiry_applied_reply",
            "expiry_source_only",
        }
        assert result["state"] == ("published" if publishes else "restored")
        assert (root / "current/index.php").read_text() == (
            candidate if publishes else "<?php echo 'Before';"
        )
        assert rig.remote.value["post_title"] == (
            "After" if publishes and scenario != "expiry_source_only" else "Before"
        )
        if scenario in {"expiry_before_build", "expiry_during_build"}:
            assert not deployments
        else:
            assert len(deployments) == 1
        assert builds[0] == (1 if scenario == "expiry_before_build" else 2)
        return
    rig.probe.results = [failure("server"), failure("server"), {"failures": []}, {"failures": []}]
    result = run(rig.service.execute("main", queued["change_id"]))
    if scenario == "manual_edit":
        assert result["state"] == "conflict"
        assert (root / "current/index.php").read_text() == "Manual edit during build"
        assert rig.remote.value["post_title"] == "Before"
        return
    if scenario == "restore_conflict":
        assert result["state"] == "recovery_required"
        assert (root / "current/index.php").read_text() == "Human edit during recovery"
        assert rig.remote.value["post_title"] == "Before"
        assert not pending_events(rig)
        return
    assert result["state"] == "restored"
    assert builds[0] == 2
    assert (root / "current/index.php").read_text() == "<?php echo 'Before';"
    assert rig.remote.value["post_title"] == "Before"
    assert rig.store.get(queued["change_id"], "root/design")["deployment"]["commit_sha"] == sha


def test_private_retention_keeps_unresolved_and_latest_baseline_and_removes_old_artifacts(tmp_path):
    import os
    import time

    store = Store(tmp_path)
    old = time.time() - 100 * 86400
    old_artifact = store.artifact(b"old snapshot")
    keep_artifact = store.artifact(b"current snapshot")
    older = store.create(
        "root",
        "main",
        "https://design.example.test",
        {"actor": "test", "state": "published", "created_at": old, "snapshot": old_artifact},
    )
    current = store.create(
        "root",
        "main",
        "https://design.example.test",
        {"actor": "test", "state": "published", "created_at": old + 1, "snapshot": keep_artifact},
    )
    pending = store.create(
        "root",
        "main",
        "https://design.example.test",
        {"actor": "test", "state": "uncertain", "created_at": old},
    )
    # SQL envelope state follows the created payload, just as persisted transitions do.
    with store.db() as db:
        for row in [older, current, pending]:
            db.execute(
                "UPDATE changes SET state=?,updated=? WHERE id=?",
                (row["state"], row["created_at"], row["change_id"]),
            )
    for artifact in [old_artifact, keep_artifact]:
        os.utime(store.root / "artifacts" / artifact, (old, old))
    store.prune("root", 90)
    with pytest.raises(Conflict):
        store.get(older["change_id"], "root")
    assert store.get(current["change_id"], "root")["snapshot"] == keep_artifact
    assert store.get(pending["change_id"], "root")["state"] == "uncertain"
    assert not (store.root / "artifacts" / old_artifact).exists()
    assert store.read_artifact(keep_artifact) == b"current snapshot"
