from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from evander.worker.api import create_app


class FakeBroker:
    def __init__(self) -> None:
        self.closed = False

    def status(self):
        return {"available": True, "signed_in": False, "email": None, "plan_type": None}

    def start_device_login(self):
        return {
            "login_id": "login-1",
            "status": "pending",
            "verification_url": "https://example.test/device",
            "user_code": "ABCD",
            "error": None,
        }

    def device_login_status(self, login_id: str):
        return {"login_id": login_id, "status": "pending"}

    def cancel_device_login(self, login_id: str):
        return {"login_id": login_id, "status": "cancelled"}

    def logout(self):
        return {"available": True, "signed_in": False}

    def close(self):
        self.closed = True


def test_worker_codex_auth_endpoints_require_worker_bearer_token(tmp_path: Path) -> None:
    broker = FakeBroker()
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=object(),  # type: ignore[arg-type]
        codex_auth_broker=broker,  # type: ignore[arg-type]
        token="worker-secret",
    )
    with TestClient(app) as client:
        assert client.get("/v1/codex-auth").status_code == 401
        headers = {"Authorization": "Bearer worker-secret"}
        assert client.get("/v1/codex-auth", headers=headers).json()["signed_in"] is False
        started = client.post("/v1/codex-auth/device-login", headers=headers)
        assert started.status_code == 201
        assert started.json()["user_code"] == "ABCD"
        assert (
            client.get("/v1/codex-auth/device-login/login-1", headers=headers).json()["status"]
            == "pending"
        )
        assert (
            client.delete("/v1/codex-auth/device-login/login-1", headers=headers).json()["status"]
            == "cancelled"
        )
        assert client.delete("/v1/codex-auth", headers=headers).json()["signed_in"] is False
    assert broker.closed is True
