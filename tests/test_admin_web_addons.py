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
from evander.admin.composition import build_admin_runtime
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


def _write_admin_addon_module(
    tmp_path: Path,
    *,
    module_name: str = "admin_addon",
    runtime_workflow_pack: bool = False,
) -> str:
    module_root = tmp_path / "plugins"
    module_root.mkdir()
    metadata = (
        f'"metadata": {{"runtime_workflow_pack": "{module_name}.runtime_pack_v1"}},'
        if runtime_workflow_pack
        else ""
    )
    runtime_pack_source = (
        """
def runtime_pack_v1():
    return {
        "pack_id": "team-runtime",
        "pack_version": "v1",
        "description": "Runtime pack from add-on.",
        "graphs": [
            {
                "id": "team-runtime",
                "name": "Team Runtime",
                "directory_path": "root",
                "node_ids": ["team-cron"],
            }
        ],
        "ir": {
            "version": "v1",
            "nodes": [
                {
                    "node": {
                        "id": "team-cron",
                        "display_name": "Team runtime tick",
                        "type": "trigger:cron",
                        "config": {"expression": "*/15 * * * *"},
                    }
                }
            ],
            "edges": [],
        },
    }
"""
        if runtime_workflow_pack
        else ""
    )
    (module_root / f"{module_name}.py").write_text(
        f"""
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

async def ping(request):
    return JSONResponse({{"ok": True}})

async def ui(_request):
    return HTMLResponse("<!doctype html><title>Team Add-on</title><main>Team Add-on</main>")

def register_addon():
    return {{
        "addon_id": "team-addon",
        "display_name": "Team Add-on",
        "admin_navigation": {{
            "label": "Teams",
            "short_label": "Teams",
            "description": "Team-oriented add-on surface.",
            "ui_path": "/addons/team-addon/",
        }},
        "admin_api_routes": [Route("/ping", ping, methods=["GET"])],
        "admin_ui_routes": [Route("/", ui, methods=["GET"])],
        {metadata}
    }}
{runtime_pack_source}
""",
        encoding="utf-8",
    )
    return str(module_root)


def _write_disabled_addon_configs(tmp_path: Path, *, module_name: str = "admin_addon") -> None:
    runtime_dir = tmp_path / "runtime"
    save_host_config(
        HostConfig(
            runtime_dir=runtime_dir,
            addon_loader=AddonLoaderConfig(
                modules=[module_name],
                enabled_entry_points=False,
                enabled_addon_ids=[],
            ),
        )
    )
    trigger_config = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    trigger_config.parent.mkdir(parents=True, exist_ok=True)
    trigger_config.write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "addon_loader": {
                    "modules": [module_name],
                    "enabled_entry_points": False,
                    "enabled_addon_ids": [],
                },
            }
        ),
        encoding="utf-8",
    )


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


def test_admin_web_addon_management_materializes_runtime_workflow_pack_on_enable(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = _write_admin_addon_module(tmp_path, runtime_workflow_pack=True)
    _write_disabled_addon_configs(tmp_path)
    monkeypatch.syspath_prepend(module_root)
    importlib.invalidate_caches()
    sys.modules.pop("admin_addon", None)
    client = TestClient(
        create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon")))
    )

    state = client.get("/api/admin/addons").json()
    runtime_pack = state["addons"][0]["runtime_workflow_pack"]
    assert runtime_pack["reference"] == "team-runtime.v1.json"
    assert runtime_pack["exists"] is False
    assert runtime_pack["configured"] is False
    client.post("/api/admin/privileged/unlock")

    response = client.put(
        "/api/admin/addons/enabled",
        json={
            "enabled_addon_ids": ["team-addon"],
            "expected_revision": state["revision"],
        },
    )

    assert response.status_code == 200
    saved = response.json()
    saved_pack = saved["addons"][0]["runtime_workflow_pack"]
    assert saved_pack["exists"] is True
    assert saved_pack["configured"] is True
    trigger_config = json.loads(
        (tmp_path / "config" / "trigger-orchestrator" / "config.json").read_text()
    )
    assert trigger_config["addon_loader"]["enabled_addon_ids"] == ["team-addon"]
    assert trigger_config["workflow_packs"] == ["team-runtime.v1.json"]
    pack_payload = json.loads(
        (tmp_path / "config" / "workflow-packs" / "team-runtime.v1.json").read_text()
    )
    assert pack_payload["pack_id"] == "team-runtime"
    assert pack_payload["description"] == "Runtime pack from add-on."


def test_admin_web_addon_management_does_not_overwrite_existing_runtime_pack_on_enable(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = _write_admin_addon_module(tmp_path, runtime_workflow_pack=True)
    _write_disabled_addon_configs(tmp_path)
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    existing_payload = {
        "pack_id": "team-runtime",
        "pack_version": "v1",
        "description": "Operator edited runtime pack.",
        "ir": {"version": "v1", "nodes": [], "edges": []},
    }
    (packs_dir / "team-runtime.v1.json").write_text(
        json.dumps(existing_payload),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(module_root)
    importlib.invalidate_caches()
    sys.modules.pop("admin_addon", None)
    client = TestClient(
        create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon")))
    )
    state = client.get("/api/admin/addons").json()
    client.post("/api/admin/privileged/unlock")

    response = client.put(
        "/api/admin/addons/enabled",
        json={
            "enabled_addon_ids": ["team-addon"],
            "expected_revision": state["revision"],
        },
    )

    assert response.status_code == 200
    pack_payload = json.loads((packs_dir / "team-runtime.v1.json").read_text())
    assert pack_payload["description"] == "Operator edited runtime pack."
    trigger_config = json.loads(
        (tmp_path / "config" / "trigger-orchestrator" / "config.json").read_text()
    )
    assert trigger_config["workflow_packs"] == ["team-runtime.v1.json"]


def test_admin_web_addon_runtime_workflow_pack_refresh_overwrites_existing_pack(
    tmp_path: Path, monkeypatch
) -> None:
    module_root = _write_admin_addon_module(tmp_path, runtime_workflow_pack=True)
    _write_disabled_addon_configs(tmp_path)
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "team-runtime.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "team-runtime",
                "pack_version": "v1",
                "description": "Operator edited runtime pack.",
                "ir": {"version": "v1", "nodes": [], "edges": []},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(module_root)
    importlib.invalidate_caches()
    sys.modules.pop("admin_addon", None)
    client = TestClient(
        create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon")))
    )
    state = client.get("/api/admin/addons").json()

    locked = client.post(
        "/api/admin/addons/team-addon/runtime-workflow-pack/refresh",
        json={"expected_revision": state["revision"]},
    )
    assert locked.status_code == 403
    client.post("/api/admin/privileged/unlock")
    response = client.post(
        "/api/admin/addons/team-addon/runtime-workflow-pack/refresh",
        json={"expected_revision": state["revision"]},
    )

    assert response.status_code == 200
    pack_payload = json.loads((packs_dir / "team-runtime.v1.json").read_text())
    assert pack_payload["description"] == "Runtime pack from add-on."
    assert pack_payload["graphs"][0]["id"] == "team-runtime"
    trigger_config = json.loads(
        (tmp_path / "config" / "trigger-orchestrator" / "config.json").read_text()
    )
    assert trigger_config["workflow_packs"] == ["team-runtime.v1.json"]


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
