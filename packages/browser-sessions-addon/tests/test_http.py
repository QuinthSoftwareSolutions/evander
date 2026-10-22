from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from evander.admin.web.app import AuthMiddleware
from evander.admin.web.shared import AdminWebAuth, AdminWebConfig
from evander.admin.web.user_store import AdminWebUserStore
from evander_browser_sessions_addon import web
from evander_browser_sessions_addon.relay import create_app as relay_app


@pytest.fixture
def admin(domain, tmp_path, monkeypatch):
    service, _, _ = domain
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "test-password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-session-signing-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_COOKIE_SECURE", "false")
    auth = AdminWebAuth(AdminWebConfig.from_env(), AdminWebUserStore(tmp_path / "users.db"))
    context = SimpleNamespace(auth=auth, runtime_dir=tmp_path, service=None)
    monkeypatch.setattr(web, "Service", lambda *a, **kw: service)

    async def login(request):
        token = auth.login("operator", "test-password")
        response = JSONResponse({"ok": True})
        response.set_cookie(auth.config.cookie_name, token)
        return response

    async def logout(request):
        auth.logout(request)
        response = JSONResponse({"ok": True})
        response.delete_cookie(auth.config.cookie_name)
        return response

    async def index(request):
        return JSONResponse({"login": True})

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/api/auth/login", login, methods=["POST"]),
            Route("/api/auth/logout", logout, methods=["POST"]),
            Mount("/api/addons/browser-sessions-addon", routes=web.admin_api_routes(context)),
            Mount("/addons/browser-sessions-addon", routes=web.admin_ui_routes(context)),
        ]
    )
    app.add_middleware(AuthMiddleware, auth=auth)
    return app, auth


def test_hosted_link_requires_sign_in_and_returns_to_viewer(admin, hosted):
    app, auth = admin
    with TestClient(app, base_url="https://control.test") as client:
        path = "/addons/browser-sessions-addon/view/" + hosted.id
        response = client.get(path, headers={"Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303
        assert parse_qs(urlsplit(response.headers["location"]).query)["next"] == [path]
        assert (
            client.get("/api/addons/browser-sessions-addon/sessions/" + hosted.id).status_code
            == 401
        )
        client.post("/api/auth/login")
        assert client.get(path).status_code == 200
        assert (
            client.get("/api/addons/browser-sessions-addon/sessions/" + hosted.id).json()["id"]
            == hosted.id
        )
        client.post("/api/auth/logout")
        assert (
            client.get(path, headers={"Accept": "text/html"}, follow_redirects=False).status_code
            == 303
        )


def test_mutations_require_post_json_same_origin_and_guest_never_uses_admin_view(
    admin, hosted, local
):
    app, _ = admin
    with TestClient(app, base_url="https://control.test") as client:
        client.post("/api/auth/login")
        base = "/api/addons/browser-sessions-addon/sessions/"
        assert client.get(base + hosted.id + "/close").status_code == 405
        assert (
            client.post(
                base + hosted.id + "/control",
                json={"action": "claim"},
                headers={"Origin": "https://evil.test"},
            ).status_code
            == 403
        )
        assert client.post(base + hosted.id + "/close", content="{}").status_code == 403
        assert client.get(base + local.id).status_code == 404
        assert (
            client.post(
                base + hosted.id + "/messages", json={"request_id": "oversize", "body": "x" * 70000}
            ).status_code
            == 413
        )


def test_hosted_chat_returns_followup_replies_after_the_first_answer(admin, domain, hosted):
    app, _ = admin
    service, _, _ = domain
    path = "/api/addons/browser-sessions-addon/sessions/" + hosted.id + "/messages"
    with TestClient(app, base_url="https://control.test") as client:
        client.post("/api/auth/login")
        posted = client.post(
            path, headers={"Origin": "https://control.test"},
            json={"request_id": "question", "body": "Move the button"},
        )
        assert posted.status_code == 200
        message_id = posted.json()["id"]
        service.reply("agent-a", hosted.id, message_id, "Working.", request_id="progress")
        first = client.get(path).json()
        assert [row["body"] for row in first["messages"]] == ["Move the button", "Working."]
        assert first["message_updates"][0]["status"] == "answered"
        cursor = first["messages"][-1]["seq"]
        final = service.reply("agent-a", hosted.id, message_id, "Done.", request_id="final")
        service.reply("agent-a", hosted.id, message_id, "Done.", request_id="final")
        updated = client.get(path, params={"after": cursor}).json()
        assert [(row["id"], row["body"], row["reply_to"]) for row in updated["messages"]] == [
            (final["id"], "Done.", message_id)
        ]


def test_auth_disabled_cannot_open_hosted_browser(admin, hosted):
    app, auth = admin
    auth.config = replace(auth.config, auth_mode="none")
    with TestClient(app, base_url="https://control.test") as client:
        response = client.get("/addons/browser-sessions-addon/view/" + hosted.id)
        assert response.status_code == 409
        assert response.json()["code"] == "sign_in_not_configured"


def test_logout_revocation_is_shared_between_auth_processes(admin):
    app, auth = admin
    token = auth.login("operator", "test-password")
    second = AdminWebAuth(auth.config, auth.user_store)
    assert second.session_from_token(token) is not None
    request = Request(
        {"type": "http", "headers": [(b"cookie", (auth.config.cookie_name + "=" + token).encode())]}
    )
    auth.logout(request)
    assert second.session_from_token(token) is None


def test_browser_manager_cannot_select_secrets_or_change_connections(admin, domain):
    app, auth = admin
    service, _, peers = domain
    users = auth.user_store
    users.upsert_user(login="manager", display_name="Manager")
    users.set_local_password(login="manager", password="manager-test-password")
    users.upsert_group(group_id="managers", display_name="Managers")
    users.set_group_permissions("managers", ["browser_sessions.manage"])
    users.assign_user_group(login="manager", group_id="managers", directory_path="root/demo")
    with TestClient(app, base_url="https://control.test") as client:
        client.cookies.set(auth.config.cookie_name, auth.login("manager", "manager-test-password"))
        path = "/api/addons/browser-sessions-addon/settings?directory=root/demo"
        original = client.get(path).json()
        assert original["can_manage_connections"] is False
        for field, value in [("base_url", "https://collector.test"),
                             ("credential_ref", "cred_" + "a" * 32),
                             ("token_env", "EVANDER_ADMIN_WEB_SESSION_SECRET")]:
            payload = {"expected_revision": original["revision"], "settings": __import__("copy").deepcopy(original["settings"])}
            payload["settings"]["workers"]["worker-a"][field] = value
            assert client.put(path, json=payload).status_code in {400, 403}
        response = client.post("/api/addons/browser-sessions-addon/credentials?directory=root/demo", json={
            "expected_revision": original["revision"], "purpose": "relay", "token": "untrusted-value",
        })
        assert response.status_code == 403
        original["settings"]["idle_seconds"] = 120
        assert client.put(path, json={"expected_revision": original["revision"], "settings": original["settings"]}).status_code == 200
        assert peers.calls == []
        assert service.store.settings("root/demo")[1].workers["worker-a"].base_url == "https://worker.test/browser"


def test_owner_credential_changes_require_unlock_and_keep_values_private(admin, domain):
    app, auth = admin
    service, _, _ = domain
    with TestClient(app, base_url="https://control.test") as client:
        client.post("/api/auth/login")
        path = "/api/addons/browser-sessions-addon/credentials?directory=root/demo"
        revision, _ = service.store.settings("root/demo")
        payload = {"expected_revision": revision, "purpose": "relay", "token": "new-private-relay-token"}
        assert client.post(path, json=payload).status_code == 403
        request = Request({"type": "http", "headers": [(b"cookie", (auth.config.cookie_name + "=" + client.cookies[auth.config.cookie_name]).encode())]})
        auth.mark_privileged_for_request(request)
        response = client.post(path, json=payload)
        assert response.status_code == 200
        assert payload["token"] not in response.text
        ref = response.json()["settings"]["relay_credential_ref"]
        assert service.credentials.store.metadata(ref)["owner_directory"] == "root/demo"
        assert client.post(path, json=payload).status_code == 409


def test_retargeted_saved_relay_cannot_open_a_websocket(admin, domain, hosted, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    app, _ = admin
    service, _, _ = domain
    private = service.store.private(hosted.id)
    private["relay_url"] = "https://collector.test"
    service.store.set_private(hosted.id, private)
    async def connect(*args, **kwargs):
        pytest.fail("A credential reached an unapproved WebSocket destination")
    monkeypatch.setattr(web, "RelayWebSocket", connect)
    with TestClient(app, base_url="https://control.test") as client:
        client.post("/api/auth/login")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/addons/browser-sessions-addon/sessions/" + hosted.id + "/stream", headers={"Origin": "https://control.test"}):
                pass


def test_private_relay_and_guest_roles_are_separate(domain, hosted, local):
    service, _, _ = domain
    app = relay_app(service.runtime_dir, service=service, internal_token="private-control-token")
    invitation = service.invite(local.id, "agent:agent-a")["invitation_url"].split("invite=")[1]
    # No lifespan needed: these exercise API role boundaries, without dispatching.
    client = TestClient(app)
    paired = client.post(
        "/_browser/guest/pair",
        json={"invitation": invitation, "application_url": "http://localhost:4200/"},
    )
    assert paired.status_code == 200
    token = paired.json()["host_token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/_browser/guest/state", headers=headers).json()["session"]["id"] == local.id
    assert (
        client.post(
            "/internal/sessions/" + hosted.id + "/close", headers=headers, json={}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/_browser/guest/state", headers={"Authorization": "Bearer private-control-token"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/_browser/guest/pair",
            json={"invitation": invitation, "application_url": "http://localhost/"},
        ).status_code
        == 401
    )


def test_endpoint_reconnect_pauses_control_and_revoked_token_cannot_rejoin(domain, hosted):
    service, _, _ = domain
    app = relay_app(service.runtime_dir, service=service, internal_token="private")
    token = service.store.capability(hosted.id, "hosted_endpoint", "worker:a", 600)
    client = TestClient(app)
    with client.websocket_connect("/_browser/endpoint") as endpoint:
        endpoint.send_json({"kind": "authenticate", "version": 1, "token": token})
        state = endpoint.receive_json()["session"]
        assert state["controller"] == "agent:agent-a"
        endpoint.send_json({"kind": "heartbeat"})
        assert endpoint.receive_json()["kind"] == "state"
    assert service.store.get(hosted.id).state == "disconnected"
    with client.websocket_connect("/_browser/endpoint") as endpoint:
        endpoint.send_json({"kind": "authenticate", "version": 1, "token": token})
        state = endpoint.receive_json()["session"]
        assert state["controller"] is None
        assert state["control_generation"] > hosted.control_generation
    service.close(hosted.id)
    with client.websocket_connect("/_browser/endpoint") as endpoint:
        endpoint.send_json({"kind": "authenticate", "version": 1, "token": token})
        assert endpoint.receive()["type"] == "websocket.close"


def test_directory_permission_is_rechecked_and_viewers_cannot_take_control(admin, hosted):
    app, auth = admin
    users = auth.user_store
    users.upsert_user(login="viewer", display_name="Viewer")
    users.set_local_password(login="viewer", password="viewer-test-password")
    users.upsert_group(group_id="browser-viewers", display_name="Browser viewers")
    users.set_group_permissions("browser-viewers", ["browser_sessions.view"])
    users.assign_user_group(login="viewer", group_id="browser-viewers", directory_path="root/other")
    token = auth.login("viewer", "viewer-test-password")
    with TestClient(app, base_url="https://control.test") as client:
        client.cookies.set(auth.config.cookie_name, token)
        path = "/api/addons/browser-sessions-addon/sessions/" + hosted.id
        assert client.get(path).status_code == 403
        users.assign_user_group(
            login="viewer", group_id="browser-viewers", directory_path="root/demo"
        )
        assert client.get(path).status_code == 200
        assert client.post(path + "/control", json={"action": "claim"}).status_code == 403
        users.remove_user_group_assignment(
            login="viewer", group_id="browser-viewers", directory_path="root/demo"
        )
        assert client.get(path).status_code == 403


def test_failed_preview_cannot_be_resurrected_by_disconnect_or_reconnect(domain, hosted):
    service, _, _ = domain
    app = relay_app(service.runtime_dir, service=service, internal_token="private")
    token = service.store.capability(hosted.id, "hosted_endpoint", "worker:a", 600)
    client = TestClient(app)
    with client.websocket_connect("/_browser/endpoint") as endpoint:
        endpoint.send_json({"kind": "authenticate", "version": 1, "token": token})
        endpoint.receive_json()
        service.store.mutate(
            hosted.id, "system", lambda value: setattr(value, "state", "failed")
        )
        endpoint.send_json({"kind": "heartbeat"})
        assert endpoint.receive()["type"] == "websocket.close"
    assert service.store.get(hosted.id).state == "failed"
    with client.websocket_connect("/_browser/endpoint") as endpoint:
        endpoint.send_json({"kind": "authenticate", "version": 1, "token": token})
        assert endpoint.receive()["type"] == "websocket.close"
    assert service.store.get(hosted.id).state == "failed"


def test_hosted_voice_requires_login_control_and_same_origin(admin, hosted, speech_provider):
    app, auth = admin
    path = "/api/addons/browser-sessions-addon/sessions/" + hosted.id + "/voice/"
    with TestClient(app, base_url="https://control.test") as client:
        assert client.get(path + "info").status_code == 401
        client.post("/api/auth/login")
        assert client.get(path + "info").json()["enabled"] is True
        headers = {"Content-Type": "audio/wav", "X-Voice-Request-ID": "clip-a",
                   "Origin": "https://evil.test"}
        assert client.post(path + "transcribe", content=speech_provider["audio"], headers=headers).status_code == 403
        headers["Origin"] = "https://control.test"
        response = client.post(path + "transcribe", content=speech_provider["audio"], headers=headers)
        assert response.status_code == 200 and response.json()["text"] == "Move the button to the left."
        assert client.post(
            path + "transcribe", content=b"x" * 2_000_001,
            headers={**headers, "X-Voice-Request-ID": "large"},
        ).status_code == 413
        users = auth.user_store
        users.upsert_user(login="reader", display_name="Reader")
        users.set_local_password(login="reader", password="reader-test-password")
        users.upsert_group(group_id="readers", display_name="Readers")
        users.set_group_permissions("readers", ["browser_sessions.view"])
        users.assign_user_group(login="reader", group_id="readers", directory_path="root/demo")
        client.cookies.set(auth.config.cookie_name, auth.login("reader", "reader-test-password"))
        assert client.get(path + "info").status_code == 200
        assert client.post(path + "transcribe", content=speech_provider["audio"], headers=headers).status_code == 403
        assert len(speech_provider["calls"]) == 1


def test_logout_during_transcription_does_not_return_private_text(admin, hosted, speech_provider):
    app, auth = admin
    with TestClient(app, base_url="https://control.test") as client:
        client.post("/api/auth/login")
        token = client.cookies.get(auth.config.cookie_name)
        def revoke():
            auth.logout(Request({"type": "http", "headers": [
                (b"cookie", (auth.config.cookie_name + "=" + token).encode())]}))
        speech_provider["after"] = revoke
        response = client.post(
            "/api/addons/browser-sessions-addon/sessions/" + hosted.id + "/voice/transcribe",
            content=speech_provider["audio"], headers={"Origin": "https://control.test", "Content-Type": "audio/wav",
                                     "X-Voice-Request-ID": "revoked"},
        )
        assert response.status_code == 401 and "Move the button" not in response.text


@pytest.mark.parametrize("voice_origin", ["https://control.test", "chrome-extension://" + "a" * 32])
def test_local_voice_capability_cannot_control_or_reach_another_session(
    domain, local, hosted, speech_provider, voice_origin
):
    service, _, _ = domain
    invitation = service.invite(local.id, "agent:agent-a")["invitation_url"].split("invite=")[1]
    paired = service.pair(invitation, "http://localhost:4200/")
    principal = paired["session"]["host_principal"]
    voice_url = service.voice_invite(local.id, principal)["voice_url"]
    token = voice_url.split("#token=")[1]
    client = TestClient(relay_app(service.runtime_dir, service=service, internal_token="private"),
                        base_url="https://control.test")
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/_browser/guest/voice").status_code == 200
    assert client.get("/_browser/guest/voice/info", headers=headers).json()["enabled"] is True
    assert client.get("/_browser/guest/state", headers=headers).status_code == 401
    assert client.post(
        "/_browser/guest/control", headers=headers, json={"action": "grant"}
    ).status_code == 401
    assert client.post(
        "/_browser/guest/voice/control",
        headers={**headers, "Origin": "https://control.test"}, json={"action": "grant"}
    ).status_code == 404
    assert client.get(
        "/_browser/guest/voice/info",
        headers={"Authorization": "Bearer " + paired["host_token"]},
    ).status_code == 401
    response = client.post("/_browser/guest/voice/transcribe", content=speech_provider["audio"], headers={
        **headers, "Origin": voice_origin, "Content-Type": "audio/wav",
        "X-Voice-Request-ID": "local-clip"})
    assert response.status_code == 200
    assert service.store.get(local.id).controller is None
    question = service.enqueue_message(
        hosted.id, "user:operator", web.MessageRequest(request_id="other-chat", body="Other session")
    )
    reply = service.reply("agent-a", hosted.id, question["id"], "Other session reply")
    assert client.post(
        "/_browser/guest/voice/speech", headers={**headers, "Origin": voice_origin},
        json={"request_id": "cross-session-speech", "message_id": reply["id"]},
    ).status_code == 404
    service.close(local.id)
    assert client.get("/_browser/guest/voice/info", headers=headers).status_code == 401


@pytest.mark.parametrize("voice_origin", [
    None, "https://other.test", "chrome-extension://invalid",
    "chrome-extension://" + "a" * 32 + ".other.test", "chrome-extension://" + "a" * 32 + "/",
])
def test_local_voice_rejects_unapproved_web_or_malformed_extension_origins(
    domain, local, speech_provider, voice_origin
):
    service, _, _ = domain
    invitation = service.invite(local.id, "agent:agent-a")["invitation_url"].split("invite=")[1]
    paired = service.pair(invitation, "http://localhost:4200/")
    token = service.voice_invite(local.id, paired["session"]["host_principal"])["voice_url"].split("#token=")[1]
    headers = {"Authorization": "Bearer " + token, "Content-Type": "audio/wav", "X-Voice-Request-ID": "bad-origin"}
    if voice_origin is not None:
        headers["Origin"] = voice_origin
    with TestClient(relay_app(service.runtime_dir, service=service, internal_token="private"),
                    base_url="https://control.test") as client:
        response = client.post("/_browser/guest/voice/transcribe", content=speech_provider["audio"], headers=headers)
    assert response.status_code == 403


def test_hosted_voice_never_accepts_extension_origin(admin, hosted, speech_provider):
    app, _ = admin
    with TestClient(app, base_url="https://control.test") as client:
        client.post("/api/auth/login")
        response = client.post(
            "/api/addons/browser-sessions-addon/sessions/" + hosted.id + "/voice/transcribe",
            content=speech_provider["audio"], headers={
                "Origin": "chrome-extension://" + "a" * 32, "Content-Type": "audio/wav",
                "X-Voice-Request-ID": "extension-hosted",
            },
        )
    assert response.status_code == 403


def test_helper_downloads_only_serve_published_known_packages(domain, tmp_path, monkeypatch):
    from evander_browser_sessions_addon import relay

    service, _, _ = domain
    monkeypatch.setattr(relay, "__file__", str(tmp_path / "relay.py"))
    assets = tmp_path / "ui_static"
    assets.mkdir()
    package = b"PK\x03\x04synthetic-helper-package"
    (assets / "helper-win-x64.zip").write_bytes(package)
    (assets / "private.txt").write_text("not-downloadable")
    with TestClient(relay_app(tmp_path, service=service, internal_token="test-relay-token")) as client:
        index = client.get("/_browser/helper")
        assert index.status_code == 200
        assert 'href="/_browser/helper/win-x64.zip"' in index.text
        assert 'href="/_browser/helper/linux-x64.zip"' not in index.text
        download = client.get("/_browser/helper/win-x64.zip")
        assert download.status_code == 200 and download.content == package
        assert "attachment" in download.headers["content-disposition"]
        assert download.headers["cache-control"] == "no-store"
        assert client.get("/_browser/helper/linux-x64.zip").status_code == 404
        assert client.get("/_browser/helper/private.txt.zip").status_code == 404
        assert client.get("/_browser/helper/..%2Fprivate.txt.zip").status_code == 404


def test_cancel_run_requires_authenticated_same_origin_control_and_is_retry_safe(
    admin, domain, hosted
):
    from evander_browser_sessions_addon.models import RunRequest

    service, _, _ = domain
    run, _ = service.store.claim_run(
        hosted.id,
        "agent:agent-a",
        RunRequest(
            request_id="cancel-http", generation=hosted.control_generation, goal="Save account"
        ),
        "fingerprint",
        seconds=30,
        max_steps=1,
    )
    path = "/api/addons/browser-sessions-addon/sessions/" + hosted.id + "/cancel-run"
    with TestClient(admin[0], base_url="https://control.test") as client:
        assert client.post(path, json={"run_id": run["id"]}).status_code == 401
        client.post("/api/auth/login")
        assert (
            client.post(
                path, json={"run_id": run["id"]}, headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        result = client.post(path, json={"run_id": run["id"]})
        assert result.status_code == 200
        assert result.json()["status"] == "cancelled"
        generation = service.store.get(hosted.id).control_generation
        assert client.post(path, json={"run_id": run["id"]}).json()["status"] == "cancelled"
        assert service.store.get(hosted.id).control_generation == generation
