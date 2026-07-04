from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import MethodType

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.admin.web.app import create_app
from evander.admin.web.teams_auth import teams_auth_routes
from evander.messaging.teams_auth import DeviceCodeLoginSession

_SENSITIVE_KEY_NAMES = {
    "access_token",
    "authorization_header",
    "bearer_token",
    "client_secret",
    "session_cookie",
    "refresh_token",
    "device_code",
    "store_path",
    "token",
}


def write_config(
    tmp_path: Path,
    *,
    enable_directory_scoped_config: bool = False,
) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    lines = [
        f'runtime_dir: "{runtime_dir}"',
        f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
        f'session_store_path: "{runtime_dir / "sessions.json"}"',
        f'approval_store_path: "{runtime_dir / "approvals.json"}"',
        f'draft_store_path: "{runtime_dir / "drafts.json"}"',
        f'email_store_path: "{runtime_dir / "emails.json"}"',
        f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
    ]
    if enable_directory_scoped_config:
        lines.append("enable_directory_scoped_config: true")
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def _assert_no_sensitive_fields(payload: object) -> None:
    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                assert str(key) not in _SENSITIVE_KEY_NAMES
                walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if isinstance(value, str):
            assert "should-not-leak" not in value
            return
        assert value != "should-not-leak"

    walk(payload)


class _PrivilegedFakeAuth:
    def require(self, _request: Request) -> dict[str, str]:
        return {"sid": "test-session", "sub": "test"}

    def privileged_for_request(self, _request: Request) -> bool:
        return True


def test_admin_web_teams_auth_routes_use_focused_admin_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    async def run_inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("evander.admin.web.teams_auth.run_in_threadpool", run_inline)

    class TeamsService:
        def normalize_directory_reference(self, value: str) -> str:
            calls.append(("normalize_directory_reference", value))
            return value

    class OperationsService:
        def privileged_helper_required(self) -> bool:
            calls.append(("privileged_helper_required",))
            return True

        def privileged_operations_unlocked(self) -> bool:
            calls.append(("privileged_operations_unlocked",))
            return True

        def teams_auth_status(self, *, directory: str = "root") -> dict[str, object]:
            calls.append(("status", directory))
            return {"directory_path": directory, "status": "authenticated"}

        def teams_auth_list(self) -> list[dict[str, object]]:
            calls.append(("list",))
            return [{"directory_path": "root/team-a", "status": "authenticated"}]

        def teams_auth_login(self, *, directory: str = "root") -> dict[str, object]:
            calls.append(("login", directory))
            return {
                "tenant_id": "tenant-123",
                "scopes": ["scope-a"],
                "message": "Use code TEST-CODE",
                "user_code": "TEST-CODE",
                "metadata": {"identity_id": "teams-identity"},
            }

        def teams_auth_complete_login(
            self,
            login_session,
            *,
            directory: str = "root",
            timeout_seconds: float | None = None,
        ) -> dict[str, object]:
            calls.append(("complete", directory, timeout_seconds, login_session["user_code"]))
            return {
                "directory_path": directory,
                "status": "authenticated",
                "authenticated": True,
            }

        def teams_auth_logout(self, *, directory: str = "root") -> dict[str, object]:
            calls.append(("logout", directory))
            return {"directory_path": directory, "status": "logged_out"}

        def teams_auth_list_channels(
            self,
            *,
            directory: str = "root",
            team_id: str,
            channel_name: str | None = None,
            include_all: bool = False,
        ) -> dict[str, object]:
            calls.append(("channels", directory, team_id, channel_name, include_all))
            return {"directory_path": directory, "channels": []}

    class FocusedService:
        teams_admin = TeamsService()
        operations_admin = OperationsService()

    endpoints = {
        route.path: route.endpoint
        for route in teams_auth_routes(FocusedService(), _PrivilegedFakeAuth())
    }

    def request(
        path: str,
        *,
        method: str = "GET",
        json_payload: dict[str, object] | None = None,
    ) -> Request:
        route_path, _, query_string = path.partition("?")
        body = b"" if json_payload is None else json.dumps(json_payload).encode("utf-8")

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": body, "more_body": False}

        return Request(
            {
                "type": "http",
                "method": method,
                "path": route_path,
                "query_string": query_string.encode("ascii"),
                "headers": [(b"content-type", b"application/json")],
            },
            receive,
        )

    async def call(
        path: str,
        *,
        method: str = "GET",
        json_payload: dict[str, object] | None = None,
    ):
        route_path = path.partition("?")[0]
        return await endpoints[route_path](request(path, method=method, json_payload=json_payload))

    status = asyncio.run(call("/api/teams-auth/status?directory_path=root/team-a"))
    identities = asyncio.run(call("/api/teams-auth/identities"))
    login = asyncio.run(call("/api/teams-auth/login?directory_path=root/team-a", method="POST"))
    login_session_id = json.loads(login.body)["login_session_id"]
    complete = asyncio.run(
        call(
            "/api/teams-auth/complete?directory_path=root/team-a",
            method="POST",
            json_payload={"login_session_id": login_session_id},
        )
    )
    logout = asyncio.run(call("/api/teams-auth/logout?directory_path=root/team-a", method="POST"))
    channels = asyncio.run(
        call(
            "/api/teams-auth/channels?directory_path=root/team-a&team_id=team-123&channel_name=General"
        )
    )

    assert status.status_code == 200
    assert identities.status_code == 200
    assert login.status_code == 200
    assert complete.status_code == 200
    assert logout.status_code == 200
    assert channels.status_code == 200

    assert calls == [
        ("normalize_directory_reference", "root/team-a"),
        ("status", "root/team-a"),
        ("list",),
        ("normalize_directory_reference", "root/team-a"),
        ("login", "root/team-a"),
        ("normalize_directory_reference", "root/team-a"),
        ("complete", "root/team-a", 5.0, "TEST-CODE"),
        ("normalize_directory_reference", "root/team-a"),
        ("logout", "root/team-a"),
        ("normalize_directory_reference", "root/team-a"),
        ("channels", "root/team-a", "team-123", "General", False),
    ]


def test_admin_web_teams_auth_routes_use_directory_and_sanitize_sensitive_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AUTH_MODE", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_PASSWORD", raising=False)

    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="operations")
    assert created.ok is True and created.data is not None
    selected_directory_path = created.data.details.directory.path

    calls: list[tuple[str, object]] = []
    login_session = DeviceCodeLoginSession(
        client_id="client-123",
        tenant_id="tenant-123",
        scopes=["scope-a"],
        device_code="device-code-123",
        user_code="ABCD-EFGH",
        verification_uri="https://microsoft.com/devicelogin",
        verification_uri_complete="https://microsoft.com/devicelogin?code=ABCD-EFGH",
        message="Go to https://microsoft.com/devicelogin and enter code ABCD-EFGH",
        interval_seconds=7,
        created_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 1, 12, 15, tzinfo=UTC),
        metadata={
            "directory_path": selected_directory_path,
            "identity_id": "teams-identity",
            "store_path": "/tmp/secret",
        },
    )
    login_session_holder: dict[str, DeviceCodeLoginSession] = {}

    def fake_status(self, *, directory: str = "root"):
        calls.append(("status", directory))
        return {
            "directory_path": directory,
            "backend": "delegated_graph_api",
            "authenticated": True,
            "status": "authenticated",
            "account_identifier": "teams-identity",
            "tenant_id": "tenant-123",
            "scopes": ["scope-a"],
            "issued_at": "2026-05-01T12:00:00Z",
            "expires_at": "2026-05-01T12:15:00Z",
            "access_token": "should-not-leak",
            "authorization_header": "Bearer should-not-leak",
            "bearer_token": "should-not-leak",
            "refresh_token": "should-not-leak",
            "session_cookie": "should-not-leak",
            "store_path": "/tmp/secret",
        }

    def fake_list(self):
        calls.append(("list", None))
        return [
            {
                "directory_path": selected_directory_path,
                "backend": "delegated_graph_api",
                "status": "authenticated",
                "account_identifier": "teams-identity",
                "tenant_id": "tenant-123",
                "scopes": ["scope-a"],
                "refresh_token": "should-not-leak",
                "token": "should-not-leak",
                "store_path": "/tmp/secret",
            }
        ]

    def fake_login(self, *, directory: str = "root"):
        calls.append(("login", directory))
        login_session_holder["session"] = login_session
        return login_session

    def fake_complete_login(
        self,
        stored_session,
        *,
        directory: str = "root",
        timeout_seconds: float | None = None,
    ):
        calls.append(("complete", directory, timeout_seconds))
        assert stored_session is login_session_holder["session"]
        return {
            "directory_path": directory,
            "backend": "delegated_graph_api",
            "status": "authenticated",
            "authenticated": True,
            "account_identifier": "teams-identity",
            "tenant_id": "tenant-123",
            "scopes": ["scope-a"],
            "issued_at": "2026-05-01T12:00:00Z",
            "expires_at": "2026-05-01T12:15:00Z",
            "access_token": "should-not-leak",
            "refresh_token": "should-not-leak",
            "client_secret": "should-not-leak",
            "store_path": "/tmp/secret",
            "metadata": {"store_path": "/tmp/secret", "device_code": "should-not-leak"},
        }

    def fake_logout(self, *, directory: str = "root"):
        calls.append(("logout", directory))
        return {
            "directory_path": directory,
            "backend": "delegated_graph_api",
            "status": "logged_out",
            "authenticated": False,
            "identity_id": "teams-identity",
            "refresh_token": "should-not-leak",
            "store_path": "/tmp/secret",
        }

    def fake_list_channels(
        self,
        *,
        directory: str = "root",
        team_id: str,
        channel_name: str | None = None,
        include_all: bool = False,
    ):
        calls.append(("channels", directory, team_id, channel_name, include_all))
        return {
            "directory_path": directory,
            "backend": "delegated_graph_api",
            "team_id": team_id,
            "channel_name": channel_name,
            "match_count": 1,
            "channels": [
                {
                    "display_name": "General",
                    "channel_id": "19:general@thread.skype",
                    "membership_type": "standard",
                    "web_url": "https://teams.example/general",
                    "allowlist_entry": f"{team_id}/19:general@thread.skype",
                    "access_token": "should-not-leak",
                    "store_path": "/tmp/secret",
                }
            ],
        }

    monkeypatch.setattr(
        service.operations_admin, "teams_auth_status", MethodType(fake_status, service.operations_admin)
    )
    monkeypatch.setattr(
        service.operations_admin, "teams_auth_list", MethodType(fake_list, service.operations_admin)
    )
    monkeypatch.setattr(
        service.operations_admin, "teams_auth_login", MethodType(fake_login, service.operations_admin)
    )
    monkeypatch.setattr(
        service.operations_admin,
        "teams_auth_complete_login",
        MethodType(fake_complete_login, service.operations_admin),
    )
    monkeypatch.setattr(
        service.operations_admin, "teams_auth_logout", MethodType(fake_logout, service.operations_admin)
    )
    monkeypatch.setattr(
        service.operations_admin,
        "teams_auth_list_channels",
        MethodType(fake_list_channels, service.operations_admin),
    )

    client = TestClient(create_app(service))

    unlock = client.post("/api/admin/privileged/unlock")
    status = client.get(
        "/api/teams-auth/status", params={"directory_path": selected_directory_path}
    )
    identities = client.get("/api/teams-auth/identities")
    channels = client.get(
        "/api/teams-auth/channels",
        params={
            "directory_path": selected_directory_path,
            "team_id": "team-123",
            "channel_name": "General",
        },
    )
    login = client.post("/api/teams-auth/login", params={"directory_path": selected_directory_path})
    login_session_id = login.json()["login_session_id"]
    complete = client.post(
        "/api/teams-auth/complete",
        params={"directory_path": selected_directory_path},
        json={"login_session_id": login_session_id},
    )
    logout = client.post("/api/teams-auth/logout", params={"directory_path": selected_directory_path})

    assert unlock.status_code == 200

    assert status.status_code == 200
    status_payload = status.json()
    assert status_payload["directory_path"] == selected_directory_path
    assert status_payload["backend"] == "delegated_graph_api"
    assert status_payload["authenticated"] is True
    assert status_payload["status"] == "authenticated"
    assert status_payload["account_identifier"] == "teams-identity"
    assert status_payload["tenant_id"] == "tenant-123"
    assert status_payload["scopes"] == ["scope-a"]
    _assert_no_sensitive_fields(status_payload)

    assert identities.status_code == 200
    identities_payload = identities.json()
    assert identities_payload["items"][0]["directory_path"] == selected_directory_path
    assert identities_payload["items"][0]["status"] == "authenticated"
    _assert_no_sensitive_fields(identities_payload)

    assert channels.status_code == 200
    channels_payload = channels.json()
    assert channels_payload["directory_path"] == selected_directory_path
    assert channels_payload["channels"][0]["allowlist_entry"] == "team-123/19:general@thread.skype"
    _assert_no_sensitive_fields(channels_payload)

    assert login.status_code == 200
    login_payload = login.json()
    assert login_payload["login_session_id"]
    assert login_payload["directory_path"] == selected_directory_path
    assert login_payload["identity_id"] == "teams-identity"
    assert login_payload["tenant_id"] == "tenant-123"
    assert login_payload["scopes"] == ["scope-a"]
    assert login_payload["message"] == "Go to https://microsoft.com/devicelogin and enter code ABCD-EFGH"
    assert login_payload["user_code"] == "ABCD-EFGH"
    assert login_payload["status"] == "device_code_pending"
    assert login_payload["authenticated"] is False
    assert "device_code" not in login_payload
    assert "store_path" not in login_payload
    _assert_no_sensitive_fields(login_payload)

    assert complete.status_code == 200
    complete_payload = complete.json()
    assert complete_payload["directory_path"] == selected_directory_path
    assert complete_payload["backend"] == "delegated_graph_api"
    assert complete_payload["status"] == "authenticated"
    assert complete_payload["authenticated"] is True
    assert complete_payload["account_identifier"] == "teams-identity"
    assert complete_payload["tenant_id"] == "tenant-123"
    assert complete_payload["scopes"] == ["scope-a"]
    _assert_no_sensitive_fields(complete_payload)

    assert logout.status_code == 200
    logout_payload = logout.json()
    assert logout_payload["directory_path"] == selected_directory_path
    assert logout_payload["status"] == "logged_out"
    assert logout_payload["authenticated"] is False
    _assert_no_sensitive_fields(logout_payload)

    assert calls == [
        ("status", selected_directory_path),
        ("list", None),
        ("channels", selected_directory_path, "team-123", "General", False),
        ("login", selected_directory_path),
        ("complete", selected_directory_path, 5.0),
        ("logout", selected_directory_path),
    ]


def test_admin_web_teams_auth_requires_auth_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    status = client.get("/api/teams-auth/status")
    login = client.post("/api/teams-auth/login")

    assert status.status_code == 401
    assert status.json()["error"] == "Authentication required"
    assert login.status_code == 401
    assert login.json()["error"] == "Authentication required"


def test_admin_web_teams_auth_mutations_do_not_require_privileged_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AUTH_MODE", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_PASSWORD", raising=False)

    service = build_admin_runtime(write_config(tmp_path))
    calls: list[str] = []

    def fake_login(self, *, directory: str = "root"):
        calls.append("login")
        return {
            "tenant_id": "tenant-123",
            "scopes": ["scope-a"],
            "message": "Use code TEST-CODE",
            "user_code": "TEST-CODE",
            "metadata": {"identity_id": "teams-identity"},
        }

    def fake_complete(self, login_session, *, directory: str = "root", timeout_seconds=None):
        calls.append("complete")
        assert login_session["user_code"] == "TEST-CODE"
        return {"directory_path": directory, "status": "authenticated", "authenticated": True}

    def fake_logout(self, *, directory: str = "root"):
        calls.append("logout")
        return {"directory_path": directory, "status": "logged_out", "authenticated": False}

    monkeypatch.setattr(
        service.operations_admin, "teams_auth_login", MethodType(fake_login, service.operations_admin)
    )
    monkeypatch.setattr(
        service.operations_admin,
        "teams_auth_complete_login",
        MethodType(fake_complete, service.operations_admin),
    )
    monkeypatch.setattr(
        service.operations_admin, "teams_auth_logout", MethodType(fake_logout, service.operations_admin)
    )

    client = TestClient(create_app(service))

    login = client.post("/api/teams-auth/login")
    login_session_id = login.json()["login_session_id"]
    complete = client.post(
        "/api/teams-auth/complete",
        json={"login_session_id": login_session_id, "timeout_seconds": 1},
    )
    logout = client.post("/api/teams-auth/logout")

    assert login.status_code == 200
    assert complete.status_code == 200
    assert logout.status_code == 200
    assert calls == ["login", "complete", "logout"]


@pytest.mark.parametrize(
    ("provider_payload", "directory_name", "expected_fragment"),
    [
        (None, None, "not configured for delegated Teams auth"),
        (
            {
                "teams_backend": "delegated_graph_api",
                "teams_delegated_client_id": "client-123",
                "teams_delegated_tenant_id": "tenant-123",
            },
            "ops-missing-identity",
            "missing teams_delegated_identity_id",
        ),
        (
            {
                "teams_backend": "delegated_graph_api",
                "teams_delegated_identity_id": "teams-identity",
                "teams_delegated_tenant_id": "tenant-123",
            },
            "ops-missing-client",
            "missing teams_delegated_client_id",
        ),
    ],
)
def test_admin_web_teams_auth_reports_configuration_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_payload: dict[str, object] | None,
    directory_name: str | None,
    expected_fragment: str,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    directory_path = "root"
    if provider_payload is not None:
        created = service.directory_admin.create_child(parent="root", name=directory_name or "ops")
        assert created.ok is True and created.data is not None
        directory_path = created.data.details.directory.path
        provider_payload = dict(provider_payload)

        def fake_status(self, *, directory: str = "root"):
            assert directory == directory_path
            raise ValueError(expected_fragment)

        monkeypatch.setattr(
            service.operations_admin,
            "teams_auth_status",
            MethodType(fake_status, service.operations_admin),
        )

    client = TestClient(create_app(service))
    response = client.get("/api/teams-auth/status", params={"directory_path": directory_path})

    assert response.status_code == 400
    assert expected_fragment in response.json()["error"]
