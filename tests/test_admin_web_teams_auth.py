from __future__ import annotations
from datetime import UTC, datetime
from pathlib import Path
from types import MethodType

import pytest
from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import create_app
from agent_sandbox.messaging.teams_auth import DeviceCodeLoginSession

_SENSITIVE_KEY_NAMES = {
    "access_token",
    "refresh_token",
    "device_code",
    "store_path",
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


def test_admin_web_teams_auth_routes_use_directory_and_sanitize_sensitive_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", raising=False)

    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "operations")
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
            "refresh_token": "should-not-leak",
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

    monkeypatch.setattr(service, "teams_auth_status", MethodType(fake_status, service))
    monkeypatch.setattr(service, "teams_auth_list", MethodType(fake_list, service))
    monkeypatch.setattr(service, "teams_auth_login", MethodType(fake_login, service))
    monkeypatch.setattr(
        service, "teams_auth_complete_login", MethodType(fake_complete_login, service)
    )
    monkeypatch.setattr(service, "teams_auth_logout", MethodType(fake_logout, service))
    monkeypatch.setattr(service, "teams_auth_list_channels", MethodType(fake_list_channels, service))

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
        json={"login_session_id": login_session_id, "timeout_seconds": 3},
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
        ("complete", selected_directory_path, 3.0),
        ("logout", selected_directory_path),
    ]


def test_admin_web_teams_auth_requires_auth_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")

    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    status = client.get("/api/teams-auth/status")
    login = client.post("/api/teams-auth/login")

    assert status.status_code == 401
    assert status.json()["error"] == "Authentication required"
    assert login.status_code == 401
    assert login.json()["error"] == "Authentication required"


def test_admin_web_teams_auth_mutations_are_locked_until_privileged_unlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", raising=False)

    service = AdminService(write_config(tmp_path))
    calls: list[str] = []

    def fail_login(self, *, directory: str = "root"):
        calls.append("login")
        raise AssertionError("login should not run while privileged operations are locked")

    def fail_complete(self, login_session, *, directory: str = "root", timeout_seconds=None):
        calls.append("complete")
        raise AssertionError("complete should not run while privileged operations are locked")

    def fail_logout(self, *, directory: str = "root"):
        calls.append("logout")
        raise AssertionError("logout should not run while privileged operations are locked")

    monkeypatch.setattr(service, "teams_auth_login", MethodType(fail_login, service))
    monkeypatch.setattr(service, "teams_auth_complete_login", MethodType(fail_complete, service))
    monkeypatch.setattr(service, "teams_auth_logout", MethodType(fail_logout, service))

    client = TestClient(create_app(service))

    login = client.post("/api/teams-auth/login")
    complete = client.post(
        "/api/teams-auth/complete",
        json={"login_session_id": "opaque-session", "timeout_seconds": 1},
    )
    logout = client.post("/api/teams-auth/logout")

    assert login.status_code == 403
    assert login.json()["error"] == "Privileged operations are locked."
    assert complete.status_code == 403
    assert complete.json()["error"] == "Privileged operations are locked."
    assert logout.status_code == 403
    assert logout.json()["error"] == "Privileged operations are locked."
    assert calls == []


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
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    directory_path = "root"
    if provider_payload is not None:
        created = service.create_directory_child("root", directory_name or "ops")
        assert created.ok is True and created.data is not None
        directory_path = created.data.details.directory.path
        provider_payload = dict(provider_payload)

        def fake_status(self, *, directory: str = "root"):
            assert directory == directory_path
            raise ValueError(expected_fragment)

        monkeypatch.setattr(service, "teams_auth_status", MethodType(fake_status, service))

    client = TestClient(create_app(service))
    response = client.get("/api/teams-auth/status", params={"directory_path": directory_path})

    assert response.status_code == 400
    assert expected_fragment in response.json()["error"]
