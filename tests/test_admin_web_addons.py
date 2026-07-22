from __future__ import annotations

import hashlib
import importlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
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
from evander_bokio_addon.state import PreparedActionStore


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


def _write_config_without_addons(tmp_path: Path) -> Path:
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


def _write_admin_addon_package(tmp_path: Path, *, module_name: str = "admin_package") -> None:
    package_dir = tmp_path / "addons" / "team-addon"
    package_dir.mkdir(parents=True)
    (package_dir / "addon.json").write_text(
        json.dumps({"addon_id": "team-addon", "module": module_name}),
        encoding="utf-8",
    )
    (package_dir / f"{module_name}.py").write_text(
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
    sys.modules.pop(module_name, None)


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

    client = TestClient(
        create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon")))
    )

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


def test_admin_web_merges_enabled_addon_tools_into_session_and_policy_catalogs(
    tmp_path: Path,
) -> None:
    runtime = build_admin_runtime(
        _write_config(tmp_path, module_name="evander_bokio_addon")
    )
    client = TestClient(create_app(runtime))

    session_catalog = client.get("/api/admin/sessions/catalog")
    mcp_catalog = client.get("/api/admin/mcp-tools?directory_path=root")

    assert session_catalog.status_code == 200
    session_tools = {
        item["value"]: item for item in session_catalog.json()["tool_options"]
    }
    assert session_tools["bokio_create_supplier_invoice"] == {
        "value": "bokio_create_supplier_invoice",
        "label": "Create supplier invoice",
        "description": (
            "Create a Bokio supplier invoice autonomously with a durable operation key."
        ),
        "group": "Bokio",
        "provider": "bokio-addon",
        "addon_id": "bokio-addon",
    }
    assert mcp_catalog.status_code == 200
    mcp_tools = {item["value"]: item for item in mcp_catalog.json()["items"]}
    assert mcp_tools["bokio_get_company"]["group"] == "Bokio"
    assert mcp_tools["bokio_get_company"]["provider"] == "bokio-addon"
    assert session_tools["bokio_list_uploads"]["label"] == "List uploads"
    assert session_tools["bokio_get_upload"]["label"] == "Get upload"
    assert session_tools["bokio_download_upload"]["label"] == "Download upload"
    assert mcp_tools["bokio_download_upload"]["provider"] == "bokio-addon"
    assert "bokio_create_supplier_invoice" in runtime.runtime.policy.extra_mcp_tool_names
    assert "bokio_list_uploads" in runtime.runtime.policy.extra_mcp_tool_names
    assert "bokio_get_upload" in runtime.runtime.policy.extra_mcp_tool_names
    assert "bokio_download_upload" in runtime.runtime.policy.extra_mcp_tool_names


def test_bokio_admin_prepared_action_mutations_require_privileged_unlock(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            build_admin_runtime(
                _write_config(tmp_path, module_name="evander_bokio_addon")
            )
        )
    )

    assert client.get("/api/addons/bokio-addon/prepared-actions").status_code == 200
    locked = client.post(
        "/api/addons/bokio-addon/prepared-actions",
        json={"kind": "supplier_invoice", "payload": {"supplierId": "supplier-1"}},
    )
    assert locked.status_code == 403
    assert locked.json()["error"] == "Privileged operations are locked."

    assert client.post("/api/admin/privileged/unlock").status_code == 200
    created = client.post(
        "/api/addons/bokio-addon/prepared-actions",
        json={"kind": "supplier_invoice", "payload": {"supplierId": "supplier-1"}},
    )

    assert created.status_code == 200
    assert created.json()["action"]["status"] == "pending_approval"


def test_bokio_admin_can_reject_a_failed_prepared_action(tmp_path: Path) -> None:
    runtime = build_admin_runtime(_write_config(tmp_path, module_name="evander_bokio_addon"))
    client = TestClient(create_app(runtime))
    assert client.post("/api/admin/privileged/unlock").status_code == 200
    created = client.post(
        "/api/addons/bokio-addon/prepared-actions",
        json={"kind": "supplier_invoice", "payload": {"supplierId": "supplier-1"}},
    ).json()["action"]
    store = PreparedActionStore(tmp_path / "runtime")
    store.approve_action(created["action_id"])
    store.mark_failed(created["action_id"], error={"message": "Validation failed"})

    rejected = client.post(
        f"/api/addons/bokio-addon/prepared-actions/{created['action_id']}/reject",
        json={"reason": "Do not retry this action"},
    )

    assert rejected.status_code == 200
    action = rejected.json()["action"]
    assert action["status"] == "rejected"
    assert action["rejection_reason"] == "Do not retry this action"
    assert action["error"] == {"message": "Validation failed"}
    assert [entry["status"] for entry in action["history"]] == [
        "pending_approval",
        "approved",
        "failed",
        "rejected",
    ]


def test_bokio_admin_recovers_an_interrupted_prepared_action(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = build_admin_runtime(_write_config(tmp_path, module_name="evander_bokio_addon"))
    client = TestClient(create_app(runtime))
    assert client.post("/api/admin/privileged/unlock").status_code == 200
    created = client.post(
        "/api/addons/bokio-addon/prepared-actions",
        json={"kind": "supplier_invoice", "payload": {"supplierId": "supplier-1"}},
    ).json()["action"]
    store = PreparedActionStore(tmp_path / "runtime")
    store.claim_execution(created["action_id"], approve=True)

    unconfirmed = client.post(
        f"/api/addons/bokio-addon/prepared-actions/{created['action_id']}/retry",
        json={},
    )
    assert unconfirmed.status_code == 400
    assert "confirm it was not created" in unconfirmed.json()["error"]

    real_client = httpx.Client
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"id": "supplier-invoice-1"})
    )

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    monkeypatch.setattr("evander_bokio_addon.state.process_is_alive", lambda _: False)

    recovered = client.post(
        f"/api/addons/bokio-addon/prepared-actions/{created['action_id']}/retry",
        json={"confirm_not_created": True},
    )

    assert recovered.status_code == 200
    assert recovered.json()["result"]["action"]["status"] == "executed"


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


def test_admin_web_addon_management_discovers_root_addon_packages_without_config(
    tmp_path: Path,
) -> None:
    _write_admin_addon_package(tmp_path)
    client = TestClient(create_app(build_admin_runtime(_write_config_without_addons(tmp_path))))

    state_response = client.get("/api/admin/addons")

    assert state_response.status_code == 200
    state = state_response.json()
    assert state["explicit_enabled_addon_ids"] == []
    assert state["loaded_addon_ids"] == []
    addon = state["addons"][0]
    assert addon["addon_id"] == "team-addon"
    assert addon["display_name"] == "Team Add-on"
    assert addon["source"] == "package:addons/team-addon"
    assert addon["source_type"] == "package"
    assert addon["enabled"] is False
    assert addon["loaded"] is False
    assert addon["configurable"] is True
    assert addon["has_admin_ui"] is True
    assert addon["has_admin_api"] is True


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
    assert runtime_pack["refresh_available"] is False
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
    assert saved_pack["refresh_available"] is True
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


def test_admin_web_addon_management_unlists_runtime_workflow_pack_on_disable(
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
    client.post("/api/admin/privileged/unlock")
    enabled = client.put(
        "/api/admin/addons/enabled",
        json={
            "enabled_addon_ids": ["team-addon"],
            "expected_revision": state["revision"],
        },
    )
    assert enabled.status_code == 200

    disabled = client.put(
        "/api/admin/addons/enabled",
        json={
            "enabled_addon_ids": [],
            "expected_revision": enabled.json()["revision"],
        },
    )

    assert disabled.status_code == 200
    trigger_config = json.loads(
        (tmp_path / "config" / "trigger-orchestrator" / "config.json").read_text()
    )
    assert trigger_config["addon_loader"]["enabled_addon_ids"] == []
    assert trigger_config["workflow_packs"] == []
    assert (tmp_path / "config" / "workflow-packs" / "team-runtime.v1.json").exists()


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


def test_admin_web_addon_runtime_workflow_pack_refresh_rejects_disabled_addon(
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
    assert state["addons"][0]["runtime_workflow_pack"]["refresh_available"] is False
    client.post("/api/admin/privileged/unlock")

    response = client.post(
        "/api/admin/addons/team-addon/runtime-workflow-pack/refresh",
        json={"expected_revision": state["revision"]},
    )

    assert response.status_code == 400
    assert "trigger-orchestrator addon_loader does not load that add-on" in response.json()["error"]
    pack_payload = json.loads((packs_dir / "team-runtime.v1.json").read_text())
    assert pack_payload["description"] == "Operator edited runtime pack."
    trigger_config = json.loads(
        (tmp_path / "config" / "trigger-orchestrator" / "config.json").read_text()
    )
    assert "workflow_packs" not in trigger_config


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
    client.post("/api/admin/privileged/unlock")

    disabled_response = client.post(
        "/api/admin/addons/team-addon/runtime-workflow-pack/refresh",
        json={"expected_revision": state["revision"]},
    )
    assert disabled_response.status_code == 400
    enabled_response = client.put(
        "/api/admin/addons/enabled",
        json={
            "enabled_addon_ids": ["team-addon"],
            "expected_revision": state["revision"],
        },
    )
    assert enabled_response.status_code == 200
    enabled_state = enabled_response.json()
    pack_payload = json.loads((packs_dir / "team-runtime.v1.json").read_text())
    assert pack_payload["description"] == "Operator edited runtime pack."
    response = client.post(
        "/api/admin/addons/team-addon/runtime-workflow-pack/refresh",
        json={"expected_revision": enabled_state["revision"]},
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
        lambda _loader, **_kwargs: [
            DiscoveredAddon(
                source="admin_addon",
                source_type="module",
                registration=registration,
            )
        ],
    )
    monkeypatch.setattr(
        "evander.admin.services.addon_admin_service.load_addons",
        lambda _loader, **_kwargs: LoadedAddonCatalog(
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


def test_admin_web_addon_api_routes_follow_admin_auth(tmp_path: Path, monkeypatch) -> None:
    module_root = _write_admin_addon_module(tmp_path)
    monkeypatch.syspath_prepend(module_root)
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    importlib.invalidate_caches()
    sys.modules.pop("admin_addon", None)

    client = TestClient(
        create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon")))
    )

    response = client.get("/api/addons/team-addon/ping")

    assert response.status_code == 401
    assert response.json()["error"] == "Authentication required"


def test_admin_web_addon_ui_routes_follow_admin_auth(tmp_path: Path, monkeypatch) -> None:
    module_root = _write_admin_addon_module(tmp_path)
    monkeypatch.syspath_prepend(module_root)
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    importlib.invalidate_caches()
    sys.modules.pop("admin_addon", None)

    client = TestClient(
        create_app(build_admin_runtime(_write_config(tmp_path, module_name="admin_addon")))
    )

    response = client.get("/addons/team-addon/")

    assert response.status_code == 401
    assert response.json()["error"] == "Authentication required"
