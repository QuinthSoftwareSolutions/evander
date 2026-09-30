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
from evander_wordpress_addon.models import Conflict, Settings, Site, digest, operation_definitions
from evander_wordpress_addon.providers import (
    ReadyDefinition,
    WordPressEffectProvider,
    WordPressReadyProvider,
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
        operation = request.url.path.split("/")[-1]
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
def pending_browser_extension(rig):
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


def test_managed_browser_cache_rechecks_connector_and_tool_grants(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta
    import time
    from config_helpers import write_runtime_config
    from evander.addons import AddonLoaderConfig
    from evander.models import SessionManifest
    from evander.admin.composition import build_admin_runtime
    from evander.session_auth import session_mcp_auth_metadata
    from evander.website_sessions import ManagedWebsiteSessionService

    runtime_dir = write_runtime_config(
        tmp_path,
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
    runtime = build_admin_runtime(runtime_dir).runtime
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


def test_example_workflow_imports_and_routes_queued_change_to_publication(rig, monkeypatch):
    pack = WorkflowPack.model_validate_json(
        (Path(__file__).parents[1] / "config/workflow-packs/wordpress.v1-example.json").read_text()
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

        def require(self, request):
            return {"login": "operator"}

        def require_permission(self, request, permission, directory_path):
            if not self.allowed:
                raise PermissionError

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


@pytest.mark.parametrize("scenario", ["fatal", "manual_edit", "restore_conflict"])
def test_pinned_git_build_can_resume_and_recover_a_broken_deployment(
    rig, hosting, tmp_path, scenario
):
    from evander.admin.services.managed_git_service import ManagedGitAdminService

    helper, root = hosting
    source = tmp_path / "source"
    source.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=source, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Synthetic designer")
    git("config", "user.email", "designer@example.test")
    (source / "functions.php").write_text("<?php missing_function();")
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
        },
        deployment={"kind": "pipeline", "connector_id": "hosting"},
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
                update={"config": {"method": "POST", "path": "/evander-releases/v1/status"}}
            )
        },
    )
    config = ConnectorDirectoryConfig(connectors={"website": website, "hosting": pipeline})
    rig.host.provider_config.resolve_connectors_for_directory = lambda directory: config

    async def auth(directory, connector):
        return config.connectors[connector], {}

    rig.host.host_auth = auth
    rig.host.host_services["managed_git"] = lambda: managed
    artifact = archive_bytes({"index.php": "<?php missing_function();"})
    builds = [0]

    def handle(request):
        if request.url.host == "design.example.test":
            if "missing_function" in (root / "current/index.php").read_text():
                return httpx.Response(503)
            return rig.remote.handle(request)
        payload = json.loads(request.content)
        action = request.url.path.rsplit("/", 1)[-1]
        if action == "prepare" and payload["operation_id"].endswith(":build"):
            builds[0] += 1
            submitted = base64.b64decode(payload["source_archive"])
            assert sha256(submitted).hexdigest() == payload["source_sha256"]
            with zipfile.ZipFile(io.BytesIO(submitted)) as archive:
                assert archive.read("functions.php") == b"<?php missing_function();"
            if builds[0] == 1:
                return httpx.Response(200, json={"protocol_version": 1, "state": "building"})
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
        return httpx.Response(
            200, json=helper.execute({"protocol_version": 1, "action": action, **payload})
        )

    rig.service.transport_factory = lambda origin: httpx.MockTransport(handle)
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
    prepared = run(rig.service.prepare(request))
    assert prepared["build_pending"]
    if scenario == "manual_edit":
        (root / "current/index.php").write_text("Manual edit during build")
    queued = queue(rig, prepared)
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
