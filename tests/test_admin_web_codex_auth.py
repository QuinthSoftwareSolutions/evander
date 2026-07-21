from __future__ import annotations

from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.testclient import TestClient

from evander.admin.web import codex_auth as codex_auth_module


class FakeAuth:
    def __init__(self, *, privileged: bool = True) -> None:
        self.privileged = privileged
        self.permission_checks: list[tuple[str, str]] = []

    def require(self, request) -> None:  # noqa: ANN001
        del request

    def privileged_for_request(self, request) -> bool:  # noqa: ANN001
        del request
        return self.privileged

    def require_permission(self, request, permission: str, *, directory_path: str) -> None:  # noqa: ANN001
        del request
        self.permission_checks.append((permission, directory_path))

    def read_session(self, request) -> dict[str, str]:  # noqa: ANN001
        del request
        return {"login": "owner@example.test"}


class FakeWorkerClient:
    def codex_auth_status(self) -> dict[str, object]:
        return {"signed_in": False, "email": None}

    def start_codex_device_login(self) -> dict[str, object]:
        return {
            "login_id": "login-1",
            "status": "pending",
            "verification_url": "https://auth.openai.com/device",
            "user_code": "CODE-1",
        }

    def codex_device_login_status(self, login_id: str) -> dict[str, object]:
        return {"login_id": login_id, "status": "completed"}

    def cancel_codex_device_login(self, login_id: str) -> dict[str, object]:
        return {"login_id": login_id, "status": "cancelled"}

    def logout_codex_auth(self) -> dict[str, object]:
        return {"signed_in": False}


def _service() -> SimpleNamespace:
    audit_events: list[object] = []
    provider_config = SimpleNamespace(
        resolve_for_directory=lambda directory_path: SimpleNamespace(
            container_worker_base_url="http://worker.test",
            container_worker_token_env="WORKER_TOKEN",
            container_worker_request_timeout_seconds=30.0,
        )
    )
    return SimpleNamespace(
        runtime=SimpleNamespace(
            provider_config=provider_config,
            audit=SimpleNamespace(log=audit_events.append),
        ),
        audit_events=audit_events,
    )


def test_admin_codex_auth_routes_proxy_account_actions_without_tokens(monkeypatch) -> None:  # noqa: ANN001
    service = _service()
    auth = FakeAuth()
    worker = FakeWorkerClient()
    monkeypatch.setattr(codex_auth_module, "ContainerWorkerClient", lambda **kwargs: worker)
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, auth))

    with TestClient(app) as client:
        status = client.get("/api/admin/codex-auth?directory_path=engineering")
        started = client.post("/api/admin/codex-auth/device-login?directory_path=engineering")
        completed = client.get(
            "/api/admin/codex-auth/device-login/login-1?directory_path=engineering"
        )
        cancelled = client.delete(
            "/api/admin/codex-auth/device-login/login-1?directory_path=engineering"
        )
        logged_out = client.delete("/api/admin/codex-auth?directory_path=engineering")

    assert status.json() == {
        "directory_path": "engineering",
        "signed_in": False,
        "email": None,
    }
    assert started.status_code == 201
    assert started.json()["user_code"] == "CODE-1"
    assert completed.json()["status"] == "completed"
    assert cancelled.json()["status"] == "cancelled"
    assert logged_out.json() == {"signed_in": False}
    assert {event.event_type for event in service.audit_events} == {
        "codex_subscription_login_start",
        "codex_subscription_login_cancel",
        "codex_subscription_logout",
    }
    assert "token" not in str(service.audit_events).casefold()
    assert all(check[1] == "root" for check in auth.permission_checks)


def test_admin_codex_auth_requires_privileged_unlock(monkeypatch) -> None:  # noqa: ANN001
    service = _service()
    monkeypatch.setattr(
        codex_auth_module,
        "ContainerWorkerClient",
        lambda **kwargs: FakeWorkerClient(),
    )
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, FakeAuth(privileged=False)))

    with TestClient(app) as client:
        response = client.get("/api/admin/codex-auth")

    assert response.status_code == 403
    assert response.json() == {"error": "Privileged operations are locked."}
