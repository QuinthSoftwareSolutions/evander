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
