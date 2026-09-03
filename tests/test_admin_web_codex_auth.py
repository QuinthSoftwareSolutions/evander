from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.testclient import TestClient

from evander.admin.services.codex_auth_admin_service import (
    CodexAuthAdminDependencies,
    CodexAuthAdminService,
)
from evander.admin.web import codex_auth as codex_auth_module
from evander.messaging.agent_prompt_queue import AgentPromptQueueStore, QueuedAgentPromptRecord


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

    def list_codex_auth_profiles(self) -> dict[str, object]:
        return {
            "profiles": [
                {
                    "profile_id": "team-a",
                    "display_name": "Team A",
                    "enabled": True,
                    "signed_in": False,
                    "active_job_count": 0,
                }
            ]
        }

    def create_codex_auth_profile(self, payload) -> dict[str, object]:  # noqa: ANN001
        return {**payload, "enabled": payload.get("enabled", True), "signed_in": False}

    def codex_auth_profile_status(self, profile_id: str) -> dict[str, object]:
        return {"profile_id": profile_id, "display_name": "Team A", "enabled": True}

    def patch_codex_auth_profile(self, profile_id: str, payload) -> dict[str, object]:  # noqa: ANN001
        return {"profile_id": profile_id, **payload}

    def start_codex_profile_device_login(self, profile_id: str) -> dict[str, object]:
        return {"profile_id": profile_id, "login_id": "login-1", "status": "pending"}

    def codex_profile_device_login_status(
        self, profile_id: str, login_id: str
    ) -> dict[str, object]:
        return {"profile_id": profile_id, "login_id": login_id, "status": "completed"}

    def cancel_codex_profile_device_login(
        self, profile_id: str, login_id: str
    ) -> dict[str, object]:
        return {"profile_id": profile_id, "login_id": login_id, "status": "cancelled"}

    def logout_codex_auth_profile(self, profile_id: str) -> dict[str, object]:
        return {"profile_id": profile_id, "signed_in": False}


def _service(
    worker: FakeWorkerClient | None = None,
    *,
    wake_reconciliation=None,  # noqa: ANN001
    runtime_dir: Path | None = None,
    worker_url: str = "http://worker.test",
    session_worker_urls: dict[str, str] | None = None,
) -> SimpleNamespace:
    audit_events: list[object] = []
    session_urls = dict(session_worker_urls or {})

    def resolved_config(url: str) -> SimpleNamespace:
        return SimpleNamespace(
            container_worker_base_url=url,
            container_worker_token_env="WORKER_TOKEN",
            container_worker_request_timeout_seconds=30.0,
        )

    provider_config = SimpleNamespace(
        resolve_for_directory=lambda _directory_path: resolved_config(worker_url),
        resolve_for_session_id=lambda session_id: resolved_config(
            session_urls.get(session_id, worker_url)
        ),
    )
    runtime = SimpleNamespace(
        provider_config=provider_config,
        audit=SimpleNamespace(log=audit_events.append),
    )
    return SimpleNamespace(
        runtime=runtime,
        codex_auth_admin=CodexAuthAdminService(
            CodexAuthAdminDependencies(
                provider_config=provider_config,
                audit=runtime.audit,
                runtime_dir=runtime_dir,
                wake_reconciliation=wake_reconciliation,
                client_factory=lambda **_kwargs: worker or FakeWorkerClient(),
            )
        ),
        audit_events=audit_events,
    )


def test_admin_codex_auth_routes_proxy_account_actions_without_tokens() -> None:
    auth = FakeAuth()
    worker = FakeWorkerClient()
    service = _service(worker)
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


def test_admin_codex_auth_requires_privileged_unlock() -> None:
    service = _service()
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, FakeAuth(privileged=False)))

    with TestClient(app) as client:
        response = client.get("/api/admin/codex-auth")

    assert response.status_code == 403
    assert response.json() == {"error": "Privileged operations are locked."}


def test_admin_subscription_profile_reads_do_not_require_privileged_unlock() -> None:
    auth = FakeAuth(privileged=False)
    app = Starlette(routes=codex_auth_module.codex_auth_routes(_service(), auth))

    with TestClient(app) as client:
        listed = client.get("/api/admin/codex-subscriptions")
        status = client.get("/api/admin/codex-subscriptions/team-a")
        create = client.post(
            "/api/admin/codex-subscriptions",
            json={"profile_id": "team-b", "display_name": "Team B"},
        )

    assert listed.status_code == 200
    assert status.status_code == 200
    assert create.status_code == 403
    assert all(check == ("admin.owner", "root") for check in auth.permission_checks)


def test_subscription_profile_recovery_actions_wake_queue_reconciliation() -> None:
    wakes: list[str] = []
    service = _service(wake_reconciliation=lambda: wakes.append("wake")).codex_auth_admin

    service.list_profiles("root")
    service.patch_profile(
        "root",
        "team-a",
        {"enabled": True},
        actor_user_id="owner@example.test",
    )
    service.profile_device_login_status("root", "team-a", "login-1")

    assert wakes == ["wake", "wake", "wake"]


def test_subscription_profile_refresh_makes_recovered_capacity_immediately_eligible(
    tmp_path: Path,
) -> None:
    class ReadyWorker(FakeWorkerClient):
        def list_codex_auth_profiles(self) -> dict[str, object]:
            return {
                "profiles": [
                    {
                        "profile_id": "team-a",
                        "display_name": "Team A",
                        "enabled": True,
                        "available": True,
                        "signed_in": True,
                        "rate_limits": {
                            "default": {"primary": {"used_percent": 25}},
                            "by_limit_id": {},
                        },
                    }
                ]
            }

    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue_store.defer_capacity(
        QueuedAgentPromptRecord(
            queue_key="session-a",
            target="session-a",
            vm_name="session-a",
            session_id="session-a",
            prompt="Continue",
            effect_id="effect-a",
            subscription_profile_id="team-a",
        ),
        queue_reason="subscription_rate_limited",
        retry_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    wakes: list[str] = []
    service = _service(
        ReadyWorker(),
        runtime_dir=tmp_path,
        wake_reconciliation=lambda: wakes.append("wake"),
    ).codex_auth_admin

    service.list_profiles("root")

    assert queue_store.get("session-a").retry_not_before is None
    assert wakes == ["wake"]


def test_subscription_profile_queue_recovery_is_scoped_to_selected_worker(
    tmp_path: Path,
) -> None:
    class ReadyWorker(FakeWorkerClient):
        def list_codex_auth_profiles(self) -> dict[str, object]:
            return {
                "profiles": [
                    {
                        "profile_id": "default",
                        "display_name": "Default",
                        "enabled": True,
                        "available": True,
                        "signed_in": True,
                    }
                ]
            }

    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    for session_id in ("worker-a-session", "worker-b-session"):
        queue_store.defer_capacity(
            QueuedAgentPromptRecord(
                queue_key=session_id,
                target=session_id,
                vm_name=session_id,
                session_id=session_id,
                prompt="Continue",
                effect_id=f"effect-{session_id}",
                subscription_profile_id="default",
            ),
            queue_reason="subscription_rate_limited",
            retry_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    service = _service(
        ReadyWorker(),
        runtime_dir=tmp_path,
        worker_url="http://worker-a.test/",
        session_worker_urls={
            "worker-a-session": "http://worker-a.test",
            "worker-b-session": "http://worker-b.test",
        },
    ).codex_auth_admin

    listed = service.list_profiles("root")

    assert listed["profiles"][0]["queued_prompt_count"] == 1
    assert queue_store.get("worker-a-session").retry_not_before is None
    assert queue_store.get("worker-b-session").retry_not_before is not None


def test_admin_subscription_profile_routes_proxy_worker_operations_at_root_owner_scope() -> None:
    auth = FakeAuth()
    service = _service()
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, auth))

    with TestClient(app) as client:
        listed = client.get("/api/admin/codex-subscriptions?directory_path=engineering")
        created = client.post(
            "/api/admin/codex-subscriptions?directory_path=engineering",
            json={"profile_id": "team-a", "display_name": "Team A"},
        )
        status = client.get("/api/admin/codex-subscriptions/team-a?directory_path=engineering")
        patched = client.patch(
            "/api/admin/codex-subscriptions/team-a?directory_path=engineering",
            json={"display_name": "Team Alpha", "enabled": False},
        )
        started = client.post(
            "/api/admin/codex-subscriptions/team-a/device-login?directory_path=engineering"
        )
        completed = client.get(
            "/api/admin/codex-subscriptions/team-a/device-login/login-1?directory_path=engineering"
        )
        cancelled = client.delete(
            "/api/admin/codex-subscriptions/team-a/device-login/login-1?directory_path=engineering"
        )
        logged_out = client.delete(
            "/api/admin/codex-subscriptions/team-a/session?directory_path=engineering"
        )

    assert listed.json()["profiles"][0]["profile_id"] == "team-a"
    assert listed.json()["worker_url"] == "http://worker.test"
    assert created.status_code == 201
    assert created.json()["profile_id"] == "team-a"
    assert status.json()["queued_prompt_count"] == 0
    assert patched.json()["display_name"] == "Team Alpha"
    assert started.status_code == 201
    assert completed.json()["status"] == "completed"
    assert cancelled.json()["status"] == "cancelled"
    assert logged_out.json()["signed_in"] is False
    assert auth.permission_checks
    assert all(check == ("admin.owner", "root") for check in auth.permission_checks)
    assert {event.event_type for event in service.audit_events} >= {
        "codex_subscription_profile_create",
        "codex_subscription_profile_update",
        "codex_subscription_profile_login_start",
        "codex_subscription_profile_login_cancel",
        "codex_subscription_profile_logout",
    }
