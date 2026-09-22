from __future__ import annotations

import asyncio
import copy
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from evander.classification.jev import JevProvider
from evander.classification.models import ClassificationSettings
from evander.classification.service import ClassificationService
from evander.config import GatewayConfig
from evander.directories import DirectoryService, DirectoryStore
from evander.directory_config import DirectoryConfigStore
from evander.models import SessionManifest
from evander.privacy.providers import (
    PrivacyDispatchDenied,
    ProviderApproval,
    ProviderDispatchGuard,
    config_provider_guard,
    runtime_provider_guard,
)
from evander.relays.openai import OpenAIRelay, RelayRequestContext
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.worker.codex_egress_proxy import RestrictedCodexEgressProxy, _tunnel


def policy(
    *,
    mode="enforce",
    provider="openai_public",
    endpoint="https://api.openai.com/v1",
    connection_id="model:OPENAI_API_KEY",
    capability="model_relay",
):
    return {
        "mode": mode,
        "enabled_categories": ["business_contact", "source_code"],
        "provider_policy": {
            "approvals": [
                {
                    "approval_id": "review-1",
                    "provider": provider,
                    "endpoint": endpoint,
                    "connection_id": connection_id,
                    "allowed_categories": ["business_contact", "source_code"],
                    "capabilities": [capability],
                    "status": "approved",
                    "approved_by": "operator-1",
                    "legal_review_reference": "legal-review-1",
                    "installation_wide": True,
                }
            ]
        },
    }


DISPATCH = {
    "provider": "openai_public",
    "endpoint": "https://api.openai.com/v1",
    "connection_id": "model:OPENAI_API_KEY",
    "directory_path": "root",
    "capability": "model_relay",
}


def test_report_only_preserves_unknown_and_unregistered_routes():
    guard = ProviderDispatchGuard(
        lambda _: [{"mode": "report_only", "enabled_categories": ["unknown"]}]
    )
    decision = guard.check(**DISPATCH)
    assert decision.allowed and decision.would_deny
    assert decision.reason == "unknown_data_categories"
    assert guard.check(**{**DISPATCH, "endpoint": ""}).allowed


def test_missing_runtime_report_only_does_not_create_files(tmp_path):
    runtime = tmp_path / "absent"
    decision = runtime_provider_guard(runtime).check(**DISPATCH)
    assert decision.allowed and decision.would_deny
    assert not runtime.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"endpoint": "https://api.openai.com.evil.test/v1"},
        {"endpoint": "https://api.openai.com/v1/another"},
        {"connection_id": "model:OTHER_KEY"},
        {"directory_path": None},
        {"capability": "unregistered_connector"},
    ],
)
def test_enforce_requires_exact_registered_binding(change):
    guard = ProviderDispatchGuard(lambda _: [policy()])
    assert guard.check(**DISPATCH).allowed
    with pytest.raises(PrivacyDispatchDenied, match="blocked by privacy policy"):
        guard.require(**{**DISPATCH, **change})


@pytest.mark.parametrize(
    "change",
    [
        {"status": "revoked"},
        {"status": "pending"},
        {"approved_by": ""},
        {"legal_review_reference": ""},
        {"allowed_categories": ["source_code"]},
        {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
    ],
)
def test_approval_must_be_current_and_complete(change):
    settings = policy()
    guard = ProviderDispatchGuard(lambda _: [settings])
    assert guard.check(**DISPATCH).allowed
    settings["provider_policy"]["approvals"][0].update(change)
    assert not guard.check(**DISPATCH).allowed


def test_child_cannot_loosen_root_or_add_categories():
    root = policy()
    child = copy.deepcopy(root)
    child["mode"] = "report_only"
    root["provider_policy"]["approvals"] = []
    guard = ProviderDispatchGuard(lambda _: [root, child])
    assert not guard.check(**DISPATCH).allowed
    root = policy()
    child["enabled_categories"].append("medical")
    assert guard.check(**DISPATCH).reason == "categories_outside_installation_policy"


def test_failed_policy_load_does_not_fall_back_to_report_only():
    def broken(_):
        raise OSError("private-path-sensitive-value")

    decision = ProviderDispatchGuard(broken).check(**DISPATCH)
    assert not decision.allowed
    assert decision.reason == "policy_unavailable"


def test_policy_reloaded_from_revisioned_directory_store(tmp_path):
    config = GatewayConfig(runtime_dir=tmp_path)
    directories = DirectoryStore(config.directories_store_path)
    directories.ensure_root()
    store = DirectoryConfigStore(directories, runtime_dir=tmp_path)
    settings = policy()
    store.write("root", domain="privacy", payload={"privacy": settings})
    guard = config_provider_guard(config)
    assert guard.check(**DISPATCH).allowed
    settings["provider_policy"]["approvals"][0]["status"] = "revoked"
    store.write("root", domain="privacy", payload={"privacy": settings})
    assert not guard.check(**DISPATCH).allowed


def test_subscription_union_cannot_hide_unknown_child_categories(tmp_path):
    config = GatewayConfig(runtime_dir=tmp_path)
    directories = DirectoryStore(config.directories_store_path)
    directories.ensure_root()
    service = DirectoryService(directories)
    service.create(name="child", parent="root")
    store = DirectoryConfigStore(directories, runtime_dir=tmp_path)
    settings = policy(
        provider="codex_subscription",
        endpoint="https://chatgpt.com",
        connection_id="subscription:installation",
        capability="codex_subscription",
    )
    store.write("root", domain="privacy", payload={"privacy": settings})
    guard = config_provider_guard(config)
    dispatch = dict(
        provider="codex_subscription",
        endpoint="https://chatgpt.com",
        connection_id="subscription:installation",
        capability="codex_subscription",
        directory_path=None,
        installation_wide=True,
    )
    assert guard.check(**dispatch).allowed
    store.write(
        "root/child", domain="privacy", payload={"privacy": {"enabled_categories": ["unknown"]}}
    )
    assert not guard.check(**dispatch).allowed


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:secret@example.com",
        "https://*.example.com",
        "https://example.com?token=secret",
    ],
)
def test_approval_endpoints_reject_secret_and_wildcard_urls(endpoint):
    with pytest.raises(ValueError):
        ProviderApproval(approval_id="x", provider="x", connection_id="x", endpoint=endpoint)


def test_classifier_checks_before_delivery_and_again_before_retry(tmp_path):
    settings = policy(
        provider="jev",
        endpoint=JevProvider.endpoint,
        connection_id="classifier:jev:TYPESAFE_API_KEY",
        capability="classification",
    )
    sent = []

    def transport(request):
        sent.append(request)
        settings["provider_policy"]["approvals"][0]["status"] = "revoked"
        return httpx.Response(503)

    service = ClassificationService(
        tmp_path,
        providers={
            "jev": JevProvider(transport=httpx.MockTransport(transport), sleep=lambda _: None)
        },
        secret_resolver=lambda _: "fixture-secret",
        privacy_guard=ProviderDispatchGuard(lambda _: [settings]),
    )
    payload = {
        "state": "fixture content",
        "questions": {"urgent": {"type": "boolean", "instructions": "Urgent?"}},
    }
    with pytest.raises(PrivacyDispatchDenied):
        service.evaluate(
            ClassificationSettings(enabled=True), payload, attribution={"directory_path": "root"}
        )
    assert len(sent) == 1
    with pytest.raises(PrivacyDispatchDenied):
        service.evaluate(
            ClassificationSettings(enabled=True), payload, attribution={"directory_path": "root"}
        )
    assert len(sent) == 1


@pytest.mark.parametrize("mode", ["enforce", "report_only"])
def test_relay_gate_runs_before_mock_transport(tmp_path, monkeypatch, mode):
    config = GatewayConfig(runtime_dir=tmp_path)
    JsonSessionManifestRepository(config.session_store_path).upsert_session(
        SessionManifest(
            session_id="fixture-session",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-secret")
    settings = policy(mode=mode)
    settings["provider_policy"]["approvals"] = []
    sent = []

    def upstream(request):
        sent.append(request)
        return httpx.Response(200, json={"id": "fixture"})

    app = OpenAIRelay(
        config,
        transport=httpx.MockTransport(upstream),
        privacy_guard=ProviderDispatchGuard(lambda _: [settings]),
    ).create_app()

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fixture"
        ) as client:
            return await client.post(
                "/v1/session/fixture-session/responses", json={"input": "fixture"}
            )

    response = asyncio.run(request())
    assert response.status_code == (403 if mode == "enforce" else 200)
    assert len(sent) == (0 if mode == "enforce" else 1)


def test_idle_sse_revocation_cancels_pending_upstream_read(tmp_path):
    settings = policy()
    relay = OpenAIRelay(
        GatewayConfig(runtime_dir=tmp_path),
        privacy_guard=ProviderDispatchGuard(lambda _: [settings]),
    )
    JsonSessionManifestRepository(relay.config.session_store_path).upsert_session(
        SessionManifest(
            session_id="s",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context = RelayRequestContext("s", relay.config, False, False, "root")
    closed = []

    async def chunks():
        try:
            yield b"first"
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    async def exercise():
        stream = relay._privacy_stream_chunks(chunks(), context)
        assert await anext(stream) == b"first"
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.01)
        settings["provider_policy"]["approvals"][0]["status"] = "revoked"
        with pytest.raises(PrivacyDispatchDenied):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(exercise())
    assert closed


def test_relay_persistent_context_rejects_changed_provider_and_session(tmp_path):
    settings = policy()
    relay = OpenAIRelay(
        GatewayConfig(runtime_dir=tmp_path),
        privacy_guard=ProviderDispatchGuard(lambda _: [settings]),
    )
    sessions = JsonSessionManifestRepository(relay.config.session_store_path)
    session = SessionManifest(
        session_id="s",
        directory_path="root",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    sessions.upsert_session(session)
    context = RelayRequestContext("s", relay.config, False, False, "root")
    relay._require_privacy(context)
    directory_store = DirectoryStore(relay.config.directories_store_path)
    DirectoryConfigStore(directory_store, runtime_dir=tmp_path).write(
        "root",
        domain="providers",
        payload={"openai_api_base_url": "https://another.example/v1"},
    )
    with pytest.raises(PrivacyDispatchDenied):
        relay._require_privacy(context)
    sessions.upsert_session(session.model_copy(update={"directory_path": None}))
    with pytest.raises(PrivacyDispatchDenied):
        relay._require_privacy(context)


def test_websocket_redirects_and_environment_proxy_are_disabled(tmp_path, monkeypatch):
    import websockets

    seen = []
    redirect = RuntimeError("fixture redirect to unapproved endpoint")

    class Connect:
        def __init__(self, url, **kwargs):
            seen.append((url, kwargs))

        async def __aenter__(self):
            assert self.process_redirect(redirect) is redirect
            return object()

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr(websockets, "connect", Connect)
    relay = OpenAIRelay(GatewayConfig(runtime_dir=tmp_path))

    async def exercise():
        async with relay._connect_upstream_websocket(
            "wss://api.openai.com/v1/responses",
            headers={},
            timeout_seconds=1,
        ):
            pass

    asyncio.run(exercise())
    assert seen[0][1]["proxy"] is None


def test_idle_websocket_revocation_closes_both_sides(tmp_path):
    settings = policy()
    relay = OpenAIRelay(
        GatewayConfig(runtime_dir=tmp_path),
        privacy_guard=ProviderDispatchGuard(lambda _: [settings]),
    )
    JsonSessionManifestRepository(relay.config.session_store_path).upsert_session(
        SessionManifest(
            session_id="s",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context = RelayRequestContext("s", relay.config, False, False, "root")
    closed = []

    class Client:
        async def receive(self):
            await asyncio.Event().wait()

        async def close(self, **kwargs):
            closed.append(("client", kwargs.get("code")))

    class Upstream:
        async def recv(self):
            await asyncio.Event().wait()

        async def close(self):
            closed.append(("upstream", None))

    async def exercise():
        task = asyncio.create_task(
            relay._proxy_websocket_traffic(
                Client(),
                Upstream(),
                privacy_context=context,
            )
        )
        await asyncio.sleep(0.01)
        settings["provider_policy"]["approvals"][0]["status"] = "revoked"
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(exercise())
    assert ("client", 1008) in closed
    assert ("upstream", None) in closed


def test_subscription_denial_never_opens_upstream(monkeypatch):
    guard = ProviderDispatchGuard(lambda _: [policy()])
    proxy = RestrictedCodexEgressProxy(host="127.0.0.1", port=0, privacy_guard=guard)

    class Client:
        sent = []

        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            return b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com\r\n\r\n"

        def sendall(self, data):
            self.sent.append(data)

    client = Client()

    def forbidden(*_args, **_kwargs):
        pytest.fail("denied tunnel must not connect upstream")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    proxy._handle_connection(client)
    assert client.sent[0].startswith(b"HTTP/1.1 403")


def test_idle_subscription_tunnel_closes_on_revocation():
    client, client_peer = socket.socketpair()
    upstream, upstream_peer = socket.socketpair()
    revoked = threading.Event()
    thread = threading.Thread(
        target=_tunnel,
        args=(client, upstream),
        kwargs={
            "idle_timeout_seconds": 10,
            "privacy_check": lambda: not revoked.is_set(),
        },
    )
    thread.start()
    try:
        time.sleep(0.02)
        revoked.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        for peer in (client, client_peer, upstream, upstream_peer):
            peer.close()
        thread.join(timeout=2)
