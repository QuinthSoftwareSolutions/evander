from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from evander.admin.services.codex_auth_admin_service import (
    CodexAuthAdminDependencies,
    CodexAuthAdminService,
)
from evander.admin.web import codex_auth as codex_auth_module
from evander.messaging.agent_prompt_queue import AgentPromptQueueStore, QueuedAgentPromptRecord
from evander.execution.container_worker_client import ContainerWorkerClientError
from evander.models import SessionManifest


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
    def __init__(self) -> None:
        self.reset_requests: list[tuple[str, dict[str, object]]] = []
        self.deleted_profiles: list[str] = []

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

    def delete_codex_auth_profile(self, profile_id: str) -> dict[str, object]:
        self.deleted_profiles.append(profile_id)
        return {"profile_id": profile_id, "deleted": True}

    def consume_codex_profile_reset_credit(self, profile_id, payload):
        self.reset_requests.append((profile_id, payload))
        return {
            "outcome": "reset",
            "profile": {
                "profile_id": profile_id,
                "enabled": True,
                "available": True,
                "signed_in": True,
                "rate_limits": {"default": {"primary": {"used_percent": 0}}},
            },
        }


def _service(
    worker: FakeWorkerClient | None = None,
    *,
    wake_reconciliation=None,  # noqa: ANN001
    runtime_dir: Path | None = None,
    worker_url: str = "http://worker.test",
    session_worker_urls: dict[str, str] | None = None,
    sessions: list[SessionManifest] | None = None,
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
                session_repository=SimpleNamespace(
                    read_all=lambda: {s.session_id: s for s in (sessions or [])}
                ),
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


def test_delete_profile_requires_unlock_expected_worker_and_audits_success(tmp_path: Path) -> None:
    worker = FakeWorkerClient()
    auth = FakeAuth(privileged=False)
    service = _service(worker, runtime_dir=tmp_path)
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, auth))
    path = "/api/admin/codex-subscriptions/team-a?directory_path=engineering"
    with TestClient(app) as client:
        assert client.request("DELETE", path, json={"expected_worker_url": "http://worker.test"}).status_code == 403
        auth.privileged = True
        assert client.request("DELETE", path, json={}).status_code == 400
        assert client.request("DELETE", path, json={"expected_worker_url": "http://changed.test"}).status_code == 409
        assert worker.deleted_profiles == []
        deleted = client.request("DELETE", path, json={"expected_worker_url": "http://worker.test/"})
    assert deleted.status_code == 200
    assert worker.deleted_profiles == ["team-a"]
    assert service.audit_events[-1].event_type == "codex_subscription_profile_delete"
    assert service.audit_events[-1].decision == "allowed"
    assert all(check == ("admin.owner", "root") for check in auth.permission_checks)


@pytest.mark.parametrize("expired,other_worker,legacy", [
    (False, False, False), (False, False, True), (True, False, False), (False, True, False),
])
def test_delete_profile_guards_unexpired_bindings_across_directories(
    tmp_path: Path, expired: bool, other_worker: bool, legacy: bool,
) -> None:
    profile_id = "default" if legacy else "team-a"
    session = SessionManifest(
        session_id="bound", vm_id="vm-bound", directory_path="root/another-directory",
        allowed_tools=[], codex_auth_mode="chatgpt_subscription",
        subscription_profile_id=None if legacy else profile_id,
        expires_at=datetime.now(UTC) + timedelta(days=-1 if expired else 1),
    )
    worker = FakeWorkerClient()
    service = _service(worker, runtime_dir=tmp_path, sessions=[session], session_worker_urls={
        "bound": "http://another-worker.test" if other_worker else "http://worker.test",
    })
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, FakeAuth()))
    with TestClient(app) as client:
        response = client.request("DELETE", f"/api/admin/codex-subscriptions/{profile_id}",
                                  json={"expected_worker_url": "http://worker.test"})
    blocked = not expired and not other_worker
    assert response.status_code == (409 if blocked else 200)
    assert worker.deleted_profiles == ([] if blocked else [profile_id])


def test_delete_profile_refuses_queued_work_and_worker_active_job_conflicts(tmp_path: Path) -> None:
    worker = FakeWorkerClient()
    queue = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue.put(QueuedAgentPromptRecord(
        queue_key="queued", target="vm-a", vm_name="vm-a", session_id="queued",
        prompt="Continue", effect_id="effect-queued", subscription_profile_id="team-a",
    ))
    service = _service(worker, runtime_dir=tmp_path)
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, FakeAuth()))
    with TestClient(app) as client:
        response = client.request("DELETE", "/api/admin/codex-subscriptions/team-a",
                                  json={"expected_worker_url": "http://worker.test"})
    assert response.status_code == 409
    assert worker.deleted_profiles == []

    def active(_profile_id):
        raise ContainerWorkerClientError("active jobs", status_code=409)
    worker.delete_codex_auth_profile = active
    queue.store.delete("queued")
    service = _service(worker, runtime_dir=tmp_path)
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, FakeAuth()))
    with TestClient(app) as client:
        response = client.request("DELETE", "/api/admin/codex-subscriptions/team-a",
                                  json={"expected_worker_url": "http://worker.test"})
    assert response.status_code == 409
    assert "active jobs" in response.json()["error"]
    assert service.audit_events[-1].decision == "error"


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


@pytest.mark.parametrize(
    ("used_percent", "credits", "block", "ready"),
    [
        (25, None, {}, True),
        (100, {"has_credits": True, "balance": "25.50"}, {}, True),
        (100, {"unlimited": True}, {}, True),
        (100, {"has_credits": False, "balance": "0"}, {}, False),
        (100, None, {}, False),
        (100, {"unlimited": True}, {"rate_limit_reached_type": "rate_limit_reached"}, False),
        (25, {"unlimited": True}, {"spend_control_reached": True}, False),
    ],
)
def test_subscription_profile_refresh_makes_recovered_capacity_immediately_eligible(
    tmp_path: Path, used_percent: int, credits: object, block: dict, ready: bool,
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
                            "default": {
                                "primary": {"used_percent": used_percent},
                                "credits": credits,
                                **block,
                            },
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
    worker = ReadyWorker()
    service = _service(
        worker,
        runtime_dir=tmp_path,
        wake_reconciliation=lambda: wakes.append("wake"),
    ).codex_auth_admin

    service.list_profiles("root")

    assert (queue_store.get("session-a").retry_not_before is None) is ready
    assert wakes == ["wake"]
    assert worker.reset_requests == []


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
                        "rate_limits": {
                            "default": {
                                "primary": {"used_percent": 100},
                                "credits": {"has_credits": True, "balance": "1"},
                            }
                        },
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


@pytest.mark.parametrize("permission_denied", [False, True])
def test_reset_redemption_requires_privileged_root_owner(permission_denied: bool) -> None:
    class OwnerAuth(FakeAuth):
        def require_permission(self, request, permission, *, directory_path):
            super().require_permission(request, permission, directory_path=directory_path)
            if permission_denied:
                raise PermissionError("Root ownership is required")

    worker = FakeWorkerClient()
    auth = OwnerAuth(privileged=permission_denied)
    service = _service(worker)
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, auth))
    with TestClient(app) as client:
        response = client.post(
            "/api/admin/codex-subscriptions/team-a/reset-credits/consume?directory_path=engineering",
            json={"idempotency_key": "attempt"},
        )
    assert response.status_code == 403
    assert worker.reset_requests == []
    assert service.audit_events == []
    if permission_denied:
        assert auth.permission_checks == [("admin.owner", "root")]


@pytest.mark.parametrize("outcome", ["reset", "alreadyRedeemed", "nothingToReset", "noCredit"])
def test_reset_redemption_refreshes_capacity_and_wakes_only_the_selected_worker(
    tmp_path: Path, outcome: str
) -> None:
    class ResetWorker(FakeWorkerClient):
        def consume_codex_profile_reset_credit(self, profile_id, payload):
            result = super().consume_codex_profile_reset_credit(profile_id, payload)
            result["outcome"] = outcome
            return result

    queue = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    for session_id, profile_id in (
        ("selected-worker", "team-a"),
        ("other-worker", "team-a"),
        ("other-profile", "team-b"),
    ):
        queue.defer_capacity(
            QueuedAgentPromptRecord(
                queue_key=session_id, target=session_id, vm_name=session_id,
                session_id=session_id, prompt="Continue", effect_id=f"effect-{session_id}",
                subscription_profile_id=profile_id,
            ),
            queue_reason="subscription_rate_limited",
            retry_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    wakes = []
    worker = ResetWorker()
    service = _service(
        worker, runtime_dir=tmp_path, worker_url="http://worker-a.test",
        session_worker_urls={"other-worker": "http://worker-b.test"},
        wake_reconciliation=lambda: wakes.append("wake"),
    )
    auth = FakeAuth()
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, auth))
    payload = {
        "idempotency_key": "same-attempt", "credit_id": "credit-a",
        "expected_worker_url": "http://worker-a.test/", "expected_account_id": "account-a",
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/admin/codex-subscriptions/team-a/reset-credits/consume?directory_path=engineering",
            json=payload,
        )
    assert response.status_code == 200
    assert response.json()["outcome"] == outcome
    assert response.json()["profile"]["queued_prompt_count"] == 1
    assert worker.reset_requests == [("team-a", {
        "idempotency_key": "same-attempt", "credit_id": "credit-a",
        "expected_account_id": "account-a",
    })]
    assert auth.permission_checks == [("admin.owner", "root")]
    assert (queue.get("selected-worker").retry_not_before is None) == (
        outcome in {"reset", "alreadyRedeemed"}
    )
    assert queue.get("other-worker").retry_not_before is not None
    assert queue.get("other-profile").retry_not_before is not None
    assert wakes == (["wake"] if outcome in {"reset", "alreadyRedeemed"} else [])
    assert len(service.audit_events) == 1
    audit = service.audit_events[0]
    assert audit.event_type == "codex_subscription_reset_credit_consume"
    assert audit.actor_user_id == "owner@example.test"
    assert audit.directory_path == "engineering"
    assert audit.decision == "allowed"


def test_reset_redemption_rejects_malformed_requests_and_audits_worker_failure() -> None:
    class UnavailableWorker(FakeWorkerClient):
        def consume_codex_profile_reset_credit(self, profile_id, payload):
            super().consume_codex_profile_reset_credit(profile_id, payload)
            raise ContainerWorkerClientError("worker unavailable")

    worker = UnavailableWorker()
    service = _service(worker)
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, FakeAuth()))
    path = "/api/admin/codex-subscriptions/team-a/reset-credits/consume"
    valid = {
        "idempotency_key": "attempt", "expected_worker_url": "http://worker.test",
        "expected_account_id": "account-a",
    }
    with TestClient(app) as client:
        for invalid in (
            [], {}, {**valid, "idempotency_key": " "}, {**valid, "idempotency_key": 123},
            {**valid, "credit_id": ""}, {**valid, "access_token": "secret"},
            {**valid, "expected_worker_url": " "}, {**valid, "expected_account_id": ""},
            {key: value for key, value in valid.items() if key != "expected_worker_url"},
            {key: value for key, value in valid.items() if key != "expected_account_id"},
        ):
            response = client.post(path, json=invalid)
            assert response.status_code == 400
            assert "secret" not in response.text
        assert worker.reset_requests == []
        response = client.post(path, json=valid)
    assert response.status_code == 502
    assert response.json()["error"] == "worker unavailable"
    assert len(worker.reset_requests) == 1
    assert service.audit_events[0].decision == "error"


def test_reset_retry_rejects_changed_worker_before_forwarding_and_can_resume_original_attempt() -> None:
    class IdempotentWorker(FakeWorkerClient):
        def __init__(self):
            super().__init__()
            self.keys = set()
            self.consumed = 0
            self.lose_response = False

        def consume_codex_profile_reset_credit(self, profile_id, payload):
            result = super().consume_codex_profile_reset_credit(profile_id, payload)
            key = payload["idempotency_key"]
            if key in self.keys:
                result["outcome"] = "alreadyRedeemed"
            else:
                self.keys.add(key)
                self.consumed += 1
            if self.lose_response:
                self.lose_response = False
                raise ContainerWorkerClientError("Response lost after redemption")
            return result

    original, replacement = IdempotentWorker(), IdempotentWorker()
    original.lose_response = True
    workers = {"http://worker-a.test": original, "http://worker-b.test": replacement}
    worker_url = "http://worker-a.test"
    service = _service(original)
    service.runtime.provider_config.resolve_for_directory = lambda _path: SimpleNamespace(
        container_worker_base_url=worker_url,
        container_worker_token_env="WORKER_TOKEN",
        container_worker_request_timeout_seconds=30.0,
    )
    service.codex_auth_admin = CodexAuthAdminService(CodexAuthAdminDependencies(
        provider_config=service.runtime.provider_config,
        audit=service.runtime.audit,
        client_factory=lambda **kwargs: workers[kwargs["base_url"]],
    ))
    app = Starlette(routes=codex_auth_module.codex_auth_routes(service, FakeAuth()))
    path = "/api/admin/codex-subscriptions/team-a/reset-credits/consume?directory_path=engineering"
    request = {
        "idempotency_key": "original-attempt", "expected_worker_url": "http://worker-a.test",
        "expected_account_id": "original-account",
    }
    with TestClient(app) as client:
        assert client.post(path, json=request).status_code == 502
        worker_url = "http://worker-b.test"
        rejected = client.post(path, json=request)
        assert rejected.status_code == 409
        assert "worker changed" in rejected.json()["error"]
        assert original.consumed == 1
        assert replacement.consumed == 0
        assert replacement.reset_requests == []
        worker_url = "http://worker-a.test"
        resumed = client.post(path, json=request)
    assert resumed.status_code == 200
    assert resumed.json()["outcome"] == "alreadyRedeemed"
    assert original.consumed == 1
    assert original.reset_requests[0] == original.reset_requests[1]
    assert [event.decision for event in service.audit_events] == ["error", "deny", "allowed"]
