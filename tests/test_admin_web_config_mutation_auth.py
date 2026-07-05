from __future__ import annotations

from pathlib import Path
from types import MethodType

from starlette.testclient import TestClient

from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.admin.web.app import create_app
from evander.models import WorkerActionResult, WorkerSummary


def _write_config(tmp_path: Path) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(
        "\n".join(
            [
                f'runtime_dir: "{runtime_dir}"',
                f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
                f'session_store_path: "{runtime_dir / "sessions.json"}"',
                f'approval_store_path: "{runtime_dir / "approvals.json"}"',
                f'draft_store_path: "{runtime_dir / "drafts.json"}"',
                f'email_store_path: "{runtime_dir / "emails.json"}"',
                f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
                "enable_directory_scoped_config: true",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _stub_worker_listing(service: AdminRuntimeComponents, monkeypatch) -> None:
    def list_background_workers(self):
        return [
            WorkerSummary(
                worker_id="trigger-orchestrator",
                name="Trigger Orchestrator",
                unit="evander-trigger-orchestrator.service",
                active_state="inactive",
                running=False,
                failed=False,
            )
        ]

    monkeypatch.setattr(
        service.operations_admin,
        "list_background_workers",
        MethodType(list_background_workers, service.operations_admin),
    )


def _authenticated_clients(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = build_admin_runtime(_write_config(tmp_path))
    _stub_worker_listing(service, monkeypatch)
    app = create_app(service)
    app.state.admin_user_store.upsert_user(
        login="operator@example.com", role="regular", display_name="Operator"
    )
    app.state.admin_user_store.set_local_password(
        login="operator@example.com", password="operator-pass"
    )

    owner = TestClient(app)
    non_owner = TestClient(app)
    owner_login = owner.post(
        "/api/auth/login", json={"username": "admin", "password": "admin-pass"}
    )
    non_owner_login = non_owner.post(
        "/api/auth/login",
        json={"username": "operator@example.com", "password": "operator-pass"},
    )
    assert owner_login.status_code == 200
    assert non_owner_login.status_code == 200
    return service, owner, non_owner


def _worker_config_payload(client: TestClient) -> dict[str, object]:
    detail_response = client.get("/api/workers/trigger-orchestrator")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    config = dict(detail["config"]["config"])
    config["poll_interval_seconds"] = int(config.get("poll_interval_seconds") or 5) + 1
    return {"config": config, "expected_revision": detail["config"]["revision"]}


def _directory_domain_payload(
    client: TestClient, *, directory_path: str = "root"
) -> dict[str, object]:
    state_response = client.get(f"/api/configuration/state?directory_path={directory_path}")
    assert state_response.status_code == 200
    revision = state_response.json()["snapshots"]["providers"]["revision"]
    return {
        "config": {"execution_backend": "container_worker_vm"},
        "expected_revision": revision,
    }


def _root_providers_config(service: AdminRuntimeComponents) -> dict[str, object]:
    providers = service.operations_admin.read_directory_domain_config("root", "providers")
    assert providers.ok is True
    assert providers.data is not None
    return dict(providers.data.config or {})


def _assert_forbidden(response) -> None:
    assert response.status_code == 403
    assert response.json()["error"] == "Privileged operations are locked."


def test_locked_owner_cannot_mutate_worker_or_directory_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    service, owner, _non_owner = _authenticated_clients(tmp_path, monkeypatch)
    existing_directory = service.operations_admin.create_directory_child("root", "ops")
    assert existing_directory.ok is True
    providers_before = _root_providers_config(service)
    action_calls: list[tuple[str, str]] = []

    def start_background_worker(self, worker_id: str):
        action_calls.append(("start", worker_id))
        return WorkerActionResult(
            success=True,
            worker_id=worker_id,
            unit="evander-trigger-orchestrator.service",
            action="start",
            message="started",
        )

    def stop_background_worker(self, worker_id: str):
        action_calls.append(("stop", worker_id))
        return WorkerActionResult(
            success=True,
            worker_id=worker_id,
            unit="evander-trigger-orchestrator.service",
            action="stop",
            message="stopped",
        )

    monkeypatch.setattr(
        service.operations_admin,
        "start_background_worker",
        MethodType(start_background_worker, service.operations_admin),
    )
    monkeypatch.setattr(
        service.operations_admin,
        "stop_background_worker",
        MethodType(stop_background_worker, service.operations_admin),
    )

    _assert_forbidden(
        owner.put("/api/workers/trigger-orchestrator/config", json=_worker_config_payload(owner))
    )
    _assert_forbidden(owner.post("/api/workers/trigger-orchestrator/actions/start"))
    _assert_forbidden(owner.post("/api/workers/trigger-orchestrator/actions/stop"))
    _assert_forbidden(
        owner.post(
            "/api/configuration/directories",
            json={"parent_path": "root", "name": "blocked"},
        )
    )
    _assert_forbidden(owner.delete("/api/configuration/directories?directory_path=root/ops"))
    _assert_forbidden(
        owner.put(
            "/api/configuration/domains/providers?directory_path=root",
            json=_directory_domain_payload(owner),
        )
    )

    assert action_calls == []
    assert service.workflow_config_admin.read_worker_global_config(
        "trigger-orchestrator"
    ).exists is False
    assert service.directory_admin.get("root/blocked").ok is False
    assert service.directory_admin.get("root/ops").ok is True
    assert _root_providers_config(service) == providers_before


def test_unlocked_non_owner_cannot_mutate_worker_or_directory_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    service, _owner, non_owner = _authenticated_clients(tmp_path, monkeypatch)
    existing_directory = service.operations_admin.create_directory_child("root", "ops")
    assert existing_directory.ok is True
    providers_before = _root_providers_config(service)
    unlock = non_owner.post("/api/admin/privileged/unlock")
    assert unlock.status_code == 200
    action_calls: list[tuple[str, str]] = []

    def start_background_worker(self, worker_id: str):
        action_calls.append(("start", worker_id))
        return WorkerActionResult(
            success=True,
            worker_id=worker_id,
            unit="evander-trigger-orchestrator.service",
            action="start",
            message="started",
        )

    def stop_background_worker(self, worker_id: str):
        action_calls.append(("stop", worker_id))
        return WorkerActionResult(
            success=True,
            worker_id=worker_id,
            unit="evander-trigger-orchestrator.service",
            action="stop",
            message="stopped",
        )

    monkeypatch.setattr(
        service.operations_admin,
        "start_background_worker",
        MethodType(start_background_worker, service.operations_admin),
    )
    monkeypatch.setattr(
        service.operations_admin,
        "stop_background_worker",
        MethodType(stop_background_worker, service.operations_admin),
    )

    _assert_forbidden(
        non_owner.put(
            "/api/workers/trigger-orchestrator/config",
            json=_worker_config_payload(non_owner),
        )
    )
    _assert_forbidden(non_owner.post("/api/workers/trigger-orchestrator/actions/start"))
    _assert_forbidden(non_owner.post("/api/workers/trigger-orchestrator/actions/stop"))
    _assert_forbidden(
        non_owner.post(
            "/api/configuration/directories",
            json={"parent_path": "root", "name": "blocked"},
        )
    )
    _assert_forbidden(non_owner.delete("/api/configuration/directories?directory_path=root/ops"))
    _assert_forbidden(
        non_owner.put(
            "/api/configuration/domains/providers?directory_path=root",
            json=_directory_domain_payload(non_owner),
        )
    )

    assert action_calls == []
    assert service.workflow_config_admin.read_worker_global_config(
        "trigger-orchestrator"
    ).exists is False
    assert service.directory_admin.get("root/blocked").ok is False
    assert service.directory_admin.get("root/ops").ok is True
    assert _root_providers_config(service) == providers_before


def test_unlocked_owner_reaches_worker_and_directory_validation_and_success(
    tmp_path: Path, monkeypatch
) -> None:
    service, owner, _non_owner = _authenticated_clients(tmp_path, monkeypatch)
    unlock = owner.post("/api/admin/privileged/unlock")
    assert unlock.status_code == 200
    action_calls: list[tuple[str, str]] = []

    def start_background_worker(self, worker_id: str):
        action_calls.append(("start", worker_id))
        return WorkerActionResult(
            success=True,
            worker_id=worker_id,
            unit="evander-trigger-orchestrator.service",
            action="start",
            message="started",
        )

    def stop_background_worker(self, worker_id: str):
        action_calls.append(("stop", worker_id))
        return WorkerActionResult(
            success=True,
            worker_id=worker_id,
            unit="evander-trigger-orchestrator.service",
            action="stop",
            message="stopped",
        )

    monkeypatch.setattr(
        service.operations_admin,
        "start_background_worker",
        MethodType(start_background_worker, service.operations_admin),
    )
    monkeypatch.setattr(
        service.operations_admin,
        "stop_background_worker",
        MethodType(stop_background_worker, service.operations_admin),
    )

    worker_payload = _worker_config_payload(owner)
    missing_revision = owner.put(
        "/api/workers/trigger-orchestrator/config",
        json={"config": worker_payload["config"]},
    )
    stale_worker = owner.put(
        "/api/workers/trigger-orchestrator/config",
        json={"config": worker_payload["config"], "expected_revision": "stale"},
    )
    saved_worker = owner.put(
        "/api/workers/trigger-orchestrator/config",
        json=worker_payload,
    )
    start = owner.post("/api/workers/trigger-orchestrator/actions/start")
    stop = owner.post("/api/workers/trigger-orchestrator/actions/stop")
    create = owner.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    delete = owner.delete("/api/configuration/directories?directory_path=root/ops")
    directory_payload = _directory_domain_payload(owner)
    stale_domain = owner.put(
        "/api/configuration/domains/providers?directory_path=root",
        json={"config": directory_payload["config"], "expected_revision": "stale"},
    )
    saved_domain = owner.put(
        "/api/configuration/domains/providers?directory_path=root",
        json=directory_payload,
    )

    assert missing_revision.status_code == 428
    assert stale_worker.status_code == 409
    assert saved_worker.status_code == 200
    assert saved_worker.json()["config"]["exists"] is True
    assert start.status_code == 200
    assert stop.status_code == 200
    assert action_calls == [
        ("start", "trigger-orchestrator"),
        ("stop", "trigger-orchestrator"),
    ]
    assert create.status_code == 200
    assert delete.status_code == 200
    assert stale_domain.status_code == 409
    assert saved_domain.status_code == 200
    providers = service.operations_admin.read_directory_domain_config("root", "providers")
    assert providers.ok is True
    assert providers.data is not None
    assert providers.data.config["execution_backend"] == "container_worker_vm"
