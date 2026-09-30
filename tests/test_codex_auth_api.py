from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from evander.codex_auth import (
    CodexRateLimitResetRequest,
    CodexSubscriptionDeferral,
    CodexSubscriptionDeferredError,
)
from evander.worker.api import create_app
from evander.worker.codex_auth_broker import CodexAuthBrokerError


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


class FakeProfileRegistry:
    def __init__(self) -> None:
        self.closed = False
        self.defer_jobs = False
        self.device_login_error: str | None = None
        self.reset_requests: list[tuple[str, CodexRateLimitResetRequest]] = []
        self.reset_error: Exception | None = None
        self.profiles = {
            "default": {
                "profile_id": "default",
                "display_name": "Default",
                "enabled": True,
                "signed_in": False,
            }
        }

    def list_profiles(self):
        return {"profiles": list(self.profiles.values())}

    def create_profile(self, payload):
        profile_id = payload["profile_id"]
        record = {
            "profile_id": profile_id,
            "display_name": payload["display_name"],
            "enabled": payload.get("enabled", True),
            "signed_in": False,
        }
        self.profiles[profile_id] = record
        return dict(record)

    def profile_status(self, profile_id):
        return dict(self.profiles[profile_id])

    def patch_profile(self, profile_id, payload):
        self.profiles[profile_id].update(payload)
        return dict(self.profiles[profile_id])

    def start_device_login(self, profile_id):
        if self.device_login_error is not None:
            raise CodexAuthBrokerError(self.device_login_error)
        return {"profile_id": profile_id, "login_id": "login-1", "status": "pending"}

    def device_login_status(self, profile_id, login_id):
        return {"profile_id": profile_id, "login_id": login_id, "status": "completed"}

    def cancel_device_login(self, profile_id, login_id):
        return {"profile_id": profile_id, "login_id": login_id, "status": "cancelled"}

    def logout(self, profile_id):
        self.profiles[profile_id]["signed_in"] = False
        return dict(self.profiles[profile_id])

    def consume_reset_credit(self, profile_id, request):
        if self.reset_error is not None:
            raise self.reset_error
        self.reset_requests.append((profile_id, request))
        return {"outcome": "reset", "profile": dict(self.profiles[profile_id])}

    def preflight(self, profile_id):
        if self.defer_jobs:
            raise CodexSubscriptionDeferredError(
                CodexSubscriptionDeferral(
                    profile_id=profile_id,
                    reason="rate_limited",
                    retry_at="2026-08-01T12:30:00Z",
                )
            )
        raise AssertionError("preflight was not expected")

    def broker(self, profile_id):
        raise AssertionError(f"broker was not expected for {profile_id}")

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


def test_worker_codex_auth_profile_endpoints_and_default_aliases(tmp_path: Path) -> None:
    registry = FakeProfileRegistry()
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=object(),  # type: ignore[arg-type]
        codex_auth_registry=registry,  # type: ignore[arg-type]
        token="worker-secret",
    )
    headers = {"Authorization": "Bearer worker-secret"}

    with TestClient(app) as client:
        assert client.get("/v1/codex-auth", headers=headers).json()["profile_id"] == "default"
        created = client.post(
            "/v1/codex-auth/profiles",
            headers=headers,
            json={"profile_id": "team-a", "display_name": "Team A"},
        )
        assert created.status_code == 201
        assert created.json()["active_job_count"] == 0
        listed = client.get("/v1/codex-auth/profiles", headers=headers).json()
        assert [profile["profile_id"] for profile in listed["profiles"]] == [
            "default",
            "team-a",
        ]
        renamed = client.patch(
            "/v1/codex-auth/profiles/team-a",
            headers=headers,
            json={"display_name": "Team Alpha", "enabled": False},
        )
        assert renamed.json()["display_name"] == "Team Alpha"
        assert renamed.json()["enabled"] is False
        assert (
            client.post("/v1/codex-auth/profiles/team-a/device-login", headers=headers).json()[
                "login_id"
            ]
            == "login-1"
        )
        assert (
            client.get(
                "/v1/codex-auth/profiles/team-a/device-login/login-1",
                headers=headers,
            ).json()["status"]
            == "completed"
        )
        assert (
            client.delete(
                "/v1/codex-auth/profiles/team-a/device-login/login-1",
                headers=headers,
            ).json()["status"]
            == "cancelled"
        )
        assert (
            client.delete("/v1/codex-auth/profiles/team-a/session", headers=headers).json()[
                "signed_in"
            ]
            is False
        )
        assert client.delete("/v1/codex-auth/profiles/team-a", headers=headers).status_code == 405

    assert registry.closed is True


def test_worker_profile_device_login_returns_actionable_gateway_error(tmp_path: Path) -> None:
    registry = FakeProfileRegistry()
    registry.device_login_error = "failed to request device code"
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=object(),  # type: ignore[arg-type]
        codex_auth_registry=registry,  # type: ignore[arg-type]
        token="worker-secret",
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/codex-auth/profiles/default/device-login",
            headers={"Authorization": "Bearer worker-secret"},
        )

    assert response.status_code == 502
    assert response.json() == {"error": "failed to request device code"}


def test_worker_reset_redemption_requires_bearer_and_valid_retry_identity(tmp_path: Path) -> None:
    registry = FakeProfileRegistry()
    registry.create_profile({"profile_id": "team-a", "display_name": "Team A"})
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=object(),  # type: ignore[arg-type]
        codex_auth_registry=registry,  # type: ignore[arg-type]
        token="worker-secret",
    )
    path = "/v1/codex-auth/profiles/team-a/reset-credits/consume"
    payload = {"idempotency_key": "attempt-1", "credit_id": "credit-1"}
    headers = {"Authorization": "Bearer worker-secret"}
    with TestClient(app) as client:
        assert client.post(path, json=payload).status_code == 401
        for invalid in ({}, {"idempotency_key": " "}, {"idempotency_key": 123}):
            assert client.post(path, headers=headers, json=invalid).status_code == 400
        assert registry.reset_requests == []
        response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json()["profile"]["profile_id"] == "team-a"
    assert response.json()["profile"]["active_job_count"] == 0
    assert registry.reset_requests == [("team-a", CodexRateLimitResetRequest(**payload))]


@pytest.mark.parametrize("disabled", [False, True])
def test_worker_reset_redemption_reports_broker_failures(tmp_path: Path, disabled: bool) -> None:
    registry = FakeProfileRegistry()
    registry.reset_error = (
        CodexSubscriptionDeferredError(CodexSubscriptionDeferral(profile_id="default", reason="disabled"))
        if disabled else CodexAuthBrokerError("reset redemption is unavailable")
    )
    app = create_app(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=object(),  # type: ignore[arg-type]
        codex_auth_registry=registry,  # type: ignore[arg-type]
        token="worker-secret",
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/codex-auth/profiles/default/reset-credits/consume",
            headers={"Authorization": "Bearer worker-secret"},
            json={"idempotency_key": "attempt-1"},
        )
    assert response.status_code == (409 if disabled else 502)
    assert registry.reset_requests == []


def test_worker_subscription_preflight_defers_without_starting_a_metered_job(
    tmp_path: Path,
) -> None:
    registry = FakeProfileRegistry()
    registry.defer_jobs = True

    class NoFallbackRuntime:
        def start_job(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("subscription deferral must not start a container")

    app = create_app(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker-root",
        container_runtime=NoFallbackRuntime(),  # type: ignore[arg-type]
        codex_auth_registry=registry,  # type: ignore[arg-type]
        token="worker-secret",
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/jobs",
            headers={"Authorization": "Bearer worker-secret"},
            json={
                "session_id": "session-1",
                "job_id": "job-1",
                "image": "runner:latest",
                "command": ["false"],
                "codex_turn": {
                    "auth_mode": "chatgpt_subscription",
                    "subscription_profile_id": "team-a",
                    "prompt": "continue",
                    "model": "gpt-5.4",
                },
            },
        )

    assert response.status_code == 429
    assert response.json()["deferral"] == {
        "profile_id": "team-a",
        "reason": "rate_limited",
        "retry_at": "2026-08-01T12:30:00Z",
    }
