from __future__ import annotations

import hashlib
import importlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from evander.addons import (
    AddonLoaderConfig,
    AddonRegistration,
    DiscoveredAddon,
    LoadedAddonCatalog,
)
from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.admin.services.addon_admin_service import (
    AddonAdminDependencies,
    AddonAdminService,
)
from evander.admin.web.app import create_app
from evander.host_config import HostConfig, save_host_config


def _write_config(tmp_path: Path, *, module_name: str) -> Path:
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
                "addon_loader:",
                "  modules:",
                f'    - "{module_name}"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _write_admin_addon_module(tmp_path: Path, *, module_name: str = "admin_addon") -> str:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    (module_root / f"{module_name}.py").write_text(
        """
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

async def ping(request):
    return JSONResponse({"ok": True})

async def ui(_request):
    return HTMLResponse("<!doctype html><title>Team Add-on</title><main>Team Add-on</main>")

def register_addon():
    return {
        "addon_id": "team-addon",
        "display_name": "Team Add-on",
        "admin_navigation": {
            "label": "Teams",
            "short_label": "Teams",
            "description": "Team-oriented add-on surface.",
            "ui_path": "/addons/team-addon/",
        },
        "admin_api_routes": [Route("/ping", ping, methods=["GET"])],
        "admin_ui_routes": [Route("/", ui, methods=["GET"])],
    }
""",
        encoding="utf-8",
    )
    return str(module_root)


def test_admin_web_exposes_enabled_addon_routes_and_navigation(tmp_path: Path, monkeypatch) -> None:
    module_root = _write_admin_addon_module(tmp_path)
    monkeypatch.syspath_prepend(module_root)
    importlib.invalidate_caches()
    sys.modules.pop("admin_addon", None)

    client = TestClient(create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon"))))

    state = client.get("/api/admin/state")
    assert state.status_code == 200
    addons = state.json()["addons"]
    assert addons == [
        {
            "addon_id": "team-addon",
            "display_name": "Team Add-on",
            "tab_key": "addon:team-addon",
            "label": "Teams",
            "short_label": "Teams",
            "description": "Team-oriented add-on surface.",
            "api_base_path": "/api/addons/team-addon",
            "ui_path": "/addons/team-addon/",
        }
    ]

    ping = client.get("/api/addons/team-addon/ping")
    assert ping.status_code == 200
    assert ping.json()["ok"] is True

    ui = client.get("/addons/team-addon/")
    assert ui.status_code == 200
    assert "Team Add-on" in ui.text


def test_admin_web_addon_management_disables_addon_after_restart(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = _write_admin_addon_module(tmp_path)
    monkeypatch.syspath_prepend(module_root)
    importlib.invalidate_caches()
    sys.modules.pop("admin_addon", None)

    client = TestClient(
        create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon")))
    )

    state_response = client.get("/api/admin/addons")
    assert state_response.status_code == 200
    state = state_response.json()
    assert state["explicit_enabled_addon_ids"] == ["team-addon"]
    assert state["addons"][0]["loaded"] is True
    assert state["addons"][0]["enabled"] is True

    unlock_response = client.post("/api/admin/privileged/unlock")
    assert unlock_response.status_code == 200
    save_response = client.put(
        "/api/admin/addons/enabled",
        json={
            "enabled_addon_ids": [],
            "expected_revision": state["revision"],
        },
    )

    assert save_response.status_code == 200
    saved = save_response.json()
    assert saved["explicit_enabled_addon_ids"] == []
    assert saved["addons"][0]["loaded"] is True
    assert saved["addons"][0]["enabled"] is False
    assert saved["addons"][0]["pending_disable"] is True
    assert "evander-admin-web" in saved["required_restart_services"]

    host_config = json.loads((tmp_path / "runtime" / "host-config.json").read_text())
    assert host_config["addon_loader"]["enabled_addon_ids"] == []
    trigger_config = json.loads(
        (tmp_path / "config" / "trigger-orchestrator" / "config.json").read_text()
    )
    assert trigger_config["addon_loader"]["enabled_addon_ids"] == []


class _NonReentrantWorkflowConfigAdmin:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = {"addon_loader": {"enabled_addon_ids": ["team-addon"]}}
        self.lock_depth = 0
        self.unlocked_writes: list[dict[str, object]] = []

    @contextmanager
    def admin_config_mutation_lock(self):
        if self.lock_depth:
            raise AssertionError("admin config mutation lock was re-entered")
        self.lock_depth += 1
        try:
            yield
        finally:
            self.lock_depth -= 1

    def read_worker_global_config(self, worker_id: str) -> SimpleNamespace:
        assert worker_id == "trigger-orchestrator"
        return SimpleNamespace(
            worker_id=worker_id,
            path=str(self.config_path),
            exists=True,
            config=dict(self.config),
            revision=self.stable_json_revision(self.config),
        )

    def write_worker_global_config(
        self,
        worker_id: str,
        payload: dict[str, object],
        *,
        expected_revision: str | None = None,
    ) -> None:
        del worker_id, payload, expected_revision
        raise AssertionError("add-on save should not relock worker config writes")

    def write_worker_global_config_unlocked(
        self, worker_id: str, payload: dict[str, object]
    ) -> SimpleNamespace:
        assert worker_id == "trigger-orchestrator"
        assert self.lock_depth == 1
        self.config = dict(payload)
        self.unlocked_writes.append(dict(payload))
        return SimpleNamespace(
            worker_id=worker_id,
            path=str(self.config_path),
            config=dict(payload),
            revision=self.stable_json_revision(payload),
        )

    def stable_json_revision(self, payload: object) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def test_addon_management_uses_unlocked_worker_config_write_inside_outer_lock(
    tmp_path: Path, monkeypatch
) -> None:
    registration = AddonRegistration(addon_id="team-addon", display_name="Team Add-on")
    monkeypatch.setattr(
        "evander.admin.services.addon_admin_service.discover_addons",
        lambda _loader: [
            DiscoveredAddon(
                source="admin_addon",
                source_type="module",
                registration=registration,
            )
        ],
    )
    monkeypatch.setattr(
        "evander.admin.services.addon_admin_service.load_addons",
        lambda _loader: LoadedAddonCatalog(
            registrations=(registration,),
            loaded_addons=("team-addon",),
        ),
    )
    runtime_dir = tmp_path / "runtime"
    save_host_config(
        HostConfig(
            runtime_dir=runtime_dir,
            addon_loader=AddonLoaderConfig(
                modules=["admin_addon"],
                enabled_addon_ids=["team-addon"],
            ),
        )
    )
    workflow_config_admin = _NonReentrantWorkflowConfigAdmin(
        tmp_path / "config" / "trigger-orchestrator" / "config.json"
    )
    service = AddonAdminService(
        AddonAdminDependencies(
            config=SimpleNamespace(
                runtime_dir=runtime_dir,
                addon_loader=AddonLoaderConfig(modules=["admin_addon"]),
            ),
            project_dir=tmp_path,
            workflow_config_admin=workflow_config_admin,
        )
    )

    state = service.read_addon_state()
    saved = service.update_enabled_addons([], expected_revision=state["revision"])

    assert workflow_config_admin.unlocked_writes
    assert workflow_config_admin.config["addon_loader"]["enabled_addon_ids"] == []
    assert saved["explicit_enabled_addon_ids"] == []


def test_admin_web_addon_api_routes_follow_admin_auth(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = _write_admin_addon_module(tmp_path)
    monkeypatch.syspath_prepend(module_root)
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    importlib.invalidate_caches()
    sys.modules.pop("admin_addon", None)

    client = TestClient(create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon"))))

    response = client.get("/api/addons/team-addon/ping")

    assert response.status_code == 401
    assert response.json()["error"] == "Authentication required"


def test_admin_web_addon_ui_routes_follow_admin_auth(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = _write_admin_addon_module(tmp_path)
    monkeypatch.syspath_prepend(module_root)
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    importlib.invalidate_caches()
    sys.modules.pop("admin_addon", None)

    client = TestClient(create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon"))))

    response = client.get("/addons/team-addon/")

    assert response.status_code == 401
    assert response.json()["error"] == "Authentication required"
