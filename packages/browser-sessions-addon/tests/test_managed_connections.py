import asyncio
import copy
import json
import sqlite3

import httpx
import pytest

from evander.session_dependencies import SessionDependencies
from evander_browser_sessions_addon.models import BrowserError, CreateRequest
from evander_browser_sessions_addon.voice import Voice
from evander_browser_sessions_addon.migrate_credentials import inventory, migrate


def request():
    return CreateRequest(
        mode="hosted",
        request_id="credential-check",
        profile_id="web",
        target_aliases=["app"],
        entry_url="http://preview-app:4200",
    )


@pytest.mark.parametrize(
    "destination",
    [
        "https://collector.test",
        "http://worker.test/browser",
        "https://worker.test:9443/browser",
        "https://worker.test/other",
    ],
)
def test_retargeted_worker_is_denied_before_first_health_request(domain, destination):
    service, _, peers = domain
    revision, settings = service.store.settings("root")
    settings.workers["worker-a"].base_url = destination
    service.store.save_settings("root", revision, settings)
    with pytest.raises(BrowserError, match="not authorized"):
        service.create("agent-a", request())
    assert peers.calls == []
    assert service.store.list() == []


@pytest.mark.parametrize("change", ["foreign", "disabled", "expired"])
def test_wrong_scope_or_inactive_credential_never_reaches_peer(domain, change):
    service, _, peers = domain
    revision, settings = service.store.settings("root")
    connection = settings.workers["worker-a"]
    if change == "foreign":
        connection.credential_ref = service.credentials.create(
            "root/other",
            "worker",
            connection.base_url,
            "foreign-secret",
            worker_id="worker-a",
            connection=connection,
        )["id"]
        service.store.save_settings("root", revision, settings)
    else:
        metadata = service.credentials.store.metadata(connection.credential_ref)
        kwargs = (
            {"status": "disabled"}
            if change == "disabled"
            else {"values": {"token": "expired-secret"}, "expires_at": 1}
        )
        service.credentials.store.update(
            connection.credential_ref, expected_revision=metadata["revision"], **kwargs
        )
    with pytest.raises(BrowserError, match="not authorized"):
        service.create("agent-a", request())
    assert peers.calls == []


def test_legacy_private_connection_cannot_exfiltrate_during_cleanup(domain, hosted, monkeypatch):
    service, _, peers = domain
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "must-not-leave-process")
    private = service.store.private(hosted.id)
    private["connection"] = {
        "base_url": "https://collector.test",
        "token_env": "EVANDER_ADMIN_WEB_SESSION_SECRET",
    }
    service.store.set_private(hosted.id, private)
    peers.calls.clear()
    assert service.close(hosted.id)["state"] == "cleanup_failed"
    assert peers.calls == []
    with sqlite3.connect(service.dependencies.path) as db:
        assert (
            db.execute("SELECT count(*) FROM dependencies WHERE id=?", (hosted.id,)).fetchone()[0]
            == 1
        )


def test_retargeted_voice_key_never_reaches_speech_provider(domain, hosted, speech_provider):
    service, _, _ = domain
    revision, settings = service.store.settings("root")
    settings.voice.api_base_url = "https://collector.test"
    service.store.save_settings("root", revision, settings)
    with pytest.raises(BrowserError, match="not authorized"):
        asyncio.run(
            Voice(service).transcribe(
                hosted.id, "user:owner", "clip", speech_provider["audio"], "audio/wav"
            )
        )
    assert speech_provider["calls"] == []


def test_managed_cleanup_checks_actual_callback_destination(domain, hosted):
    service, _, _ = domain
    with sqlite3.connect(service.dependencies.path) as db:
        db.execute(
            "UPDATE dependencies SET release_url=? WHERE id=?",
            ("https://collector.test/close", hosted.id),
        )
    calls = []
    with pytest.raises(ValueError, match="preserved"):
        SessionDependencies(service.runtime_dir).release(
            "agent-a", transport=httpx.MockTransport(lambda r: calls.append(r))
        )
    assert calls == []


def test_reviewed_migration_preserves_original_placement_and_rejects_stale_plan(
    domain, hosted, monkeypatch
):
    service, _, peers = domain
    revision, settings = service.store.settings("root")
    raw = settings.model_dump()
    raw.pop("relay_credential_ref")
    raw["relay_token_env"] = "LEGACY_RELAY"
    worker = raw["workers"]["worker-a"]
    worker.pop("credential_ref")
    worker.update(base_url="https://replacement.test/browser", token_env="LEGACY_WORKER")
    raw["voice"].pop("credential_ref")
    raw["voice"]["api_key_env"] = "LEGACY_VOICE"
    private = service.store.private(hosted.id)
    private.pop("relay_credential_ref")
    private["relay_token_env"] = "LEGACY_RELAY"
    private["connection"].pop("credential_ref")
    private["connection"]["token_env"] = "LEGACY_PINNED_WORKER"
    for name in ["LEGACY_RELAY", "LEGACY_WORKER", "LEGACY_VOICE", "LEGACY_PINNED_WORKER"]:
        monkeypatch.setenv(name, "secret-for-" + name)
    with service.store.connect(write=True) as db:
        db.execute("UPDATE settings SET payload=? WHERE directory='root'", (json.dumps(raw),))
    service.store.set_private(hosted.id, private)
    with sqlite3.connect(service.dependencies.path) as db:
        db.execute(
            "UPDATE dependencies SET token_env='LEGACY_RELAY',managed_auth=NULL WHERE id=?",
            (hosted.id,),
        )
    plan = inventory(service.runtime_dir)[0]
    assert plan["connections"] and not plan["conflicts"]
    assert "secret-for-" not in json.dumps(plan)
    with pytest.raises(ValueError, match="approval"):
        migrate(service.runtime_dir, plan, maintenance=True)
    approved = copy.deepcopy(plan)
    for entry in approved["connections"]:
        entry["approved"] = True
    stale = copy.deepcopy(approved)
    stale["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        migrate(service.runtime_dir, stale, maintenance=True)
    result = migrate(service.runtime_dir, approved, maintenance=True)
    assert result["settings_rows"] == 1 and result["dependencies"] == 1
    assert (
        service.store.settings("root")[1].workers["worker-a"].base_url
        == "https://replacement.test/browser"
    )
    peers.calls.clear()
    assert service.close(hosted.id)["state"] == "closed"
    assert next(c for c in peers.calls if c[2] == "DELETE")[:2] == (
        "https://worker.test/browser",
        "secret-for-LEGACY_PINNED_WORKER",
    )
    assert inventory(service.runtime_dir)[0]["connections"] == []


@pytest.mark.parametrize("voice_enabled", [False, True])
def test_migration_clears_unused_voice_selector_and_preserves_worker_callbacks(
    domain,
    monkeypatch,
    voice_enabled,
):
    service, _, _ = domain
    _, settings = service.store.settings("root")
    raw = settings.model_dump()
    raw.pop("relay_credential_ref")
    raw["relay_token_env"] = "LEGACY_RELAY"
    worker = raw["workers"]["worker-a"]
    worker.pop("credential_ref")
    worker["token_env"] = "LEGACY_WORKER"
    raw["voice"].pop("credential_ref")
    raw["voice"].update(enabled=voice_enabled, api_key_env="ABSENT_OPTIONAL_VOICE")
    monkeypatch.delenv("ABSENT_OPTIONAL_VOICE", raising=False)
    monkeypatch.setenv("LEGACY_RELAY", "old-relay-token")
    monkeypatch.setenv("LEGACY_WORKER", "old-worker-token")
    with service.store.connect(write=True) as db:
        db.execute("UPDATE settings SET payload=? WHERE directory='root'", (json.dumps(raw),))
    service.dependencies.register(
        "resource:preview-resource",
        "browser-preview:target:endpoint-a",
        "https://worker.test/v1/sessions/browser-preview/detach-preview-resource",
        "WORKER_LOCAL_TOKEN",
    )
    plan = inventory(service.runtime_dir)[0]
    assert not plan["conflicts"]
    assert len(plan["connections"]) == 3
    for entry in plan["connections"]:
        entry["approved"] = True
    if voice_enabled:
        with pytest.raises(ValueError, match="unavailable"):
            migrate(service.runtime_dir, plan, maintenance=True)
        with service.store.connect() as db:
            assert (
                json.loads(
                    db.execute("SELECT payload FROM settings WHERE directory='root'").fetchone()[0]
                )
                == raw
            )
        return
    result = migrate(service.runtime_dir, plan, maintenance=True)
    assert result["migrated_credentials"] == 2
    assert result["dependencies"] == 0
    updated = service.store.settings("root")[1]
    assert updated.voice.enabled is False and updated.voice.credential_ref is None
    assert (
        service.credentials.token("root", "relay", updated.relay_url, updated.relay_credential_ref)
        == "old-relay-token"
    )
    monkeypatch.setenv("WORKER_LOCAL_TOKEN", "worker-local-token")
    calls = []
    service.dependencies.release(
        "resource:preview-resource",
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200, json={"released": True})
        ),
    )
    assert len(calls) == 1 and calls[0].headers["authorization"] == "Bearer worker-local-token"
    assert inventory(service.runtime_dir)[0]["connections"] == []


@pytest.mark.parametrize("cross_origin", [True, False])
def test_relay_websocket_redirect_never_forwards_credentials(cross_origin):
    from websockets.exceptions import InvalidStatus
    from evander_browser_sessions_addon.web import RelayWebSocket

    async def exercise():
        received = []
        redirected = []

        async def collector(reader, writer):
            try:
                redirected.append(await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2))
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        target = await asyncio.start_server(collector, "127.0.0.1", 0)
        async with target:
            target_port = target.sockets[0].getsockname()[1]
            port = None

            async def relay(reader, writer):
                try:
                    incoming = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
                    if not incoming.startswith(b"GET /approved/"):
                        redirected.append(incoming)
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                    else:
                        received.append(incoming)
                        destination = f"ws://127.0.0.1:{target_port if cross_origin else port}/outside-binding"
                        writer.write(
                            (
                                f"HTTP/1.1 302 Found\r\nLocation: {destination}\r\nContent-Length: 0\r\n\r\n"
                            ).encode()
                        )
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()

            source = await asyncio.start_server(relay, "127.0.0.1", 0)
            async with source:
                port = source.sockets[0].getsockname()[1]
                with pytest.raises(InvalidStatus):
                    await RelayWebSocket(
                        f"ws://127.0.0.1:{port}/approved/internal/sessions/browser-demo/view",
                        additional_headers={"Authorization": "Bearer bound-relay-token"},
                        proxy=None,
                        open_timeout=2,
                    )
        assert len(received) == 1
        assert b"Authorization: Bearer bound-relay-token" in received[0]
        assert redirected == []

    asyncio.run(exercise())
