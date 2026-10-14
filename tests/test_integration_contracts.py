from __future__ import annotations

import asyncio
import json
from pathlib import Path
from threading import Event
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette
from starlette.exceptions import HTTPException

from evander.addons import (
    AddonContext,
    AddonLoaderConfig,
    AddonRegistration,
    LoadedAddonCatalog,
    load_addons,
)
from evander.admin.services.integration_admin_service import IntegrationAdminService
from evander.admin.web.addons import addon_routes
from evander.credentials.store import CredentialUnavailable
from evander.integration_contracts import (
    AddonIntegration,
    IntegrationBackgroundWorker,
    IntegrationCheckResult,
    IntegrationConfigSection,
    IntegrationCredentialProvider,
    IntegrationReadinessCheck,
    IntegrationWorkflowStarter,
)
from evander.integration_registry import IntegrationRegistry
from evander.workflow.packs import compose_workflow_ir


def context(tmp_path: Path, addon_id: str = "sample", directory: str = "root") -> AddonContext:
    return AddonContext(
        addon_id,
        tmp_path,
        tmp_path / "addons" / addon_id,
        project_dir=tmp_path,
        directory_path=directory,
    )


def registry(*addons: AddonRegistration) -> IntegrationRegistry:
    return IntegrationRegistry(LoadedAddonCatalog(addons, ()))


def healthy(addon_id: str) -> AddonRegistration:
    return AddonRegistration(
        addon_id,
        addon_id,
        integration=AddonIntegration(
            readiness_checks=(
                IntegrationReadinessCheck(
                    "connection",
                    "Connection",
                    lambda _: IntegrationCheckResult("ready", "connected"),
                ),
            ),
        ),
    )


def test_mapping_registration_keeps_legacy_extensions_and_integration_callbacks_inert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def not_during_registration(*_):
        pytest.fail("Registration or inventory invoked an integration callback")

    module = ModuleType("synthetic_stage1_integration")
    module.register_addon = lambda: {
        "addon_id": "sample",
        "display_name": "Sample",
        "metadata": {"existing": True},
        "connector_drivers": {"sample-driver": object()},
        "integration": {
            "credentials": [
                {
                    "id": "login",
                    "label": "Login",
                    "is_configured": not_during_registration,
                    "resolve": not_during_registration,
                }
            ],
            "workers": [{"id": "poll", "label": "Poll", "create": not_during_registration}],
            "readiness_checks": [
                {"id": "connection", "label": "Connection", "check": not_during_registration}
            ],
            "workflow_starters": [
                {
                    "id": "report",
                    "label": "Report",
                    "input_schema": {"type": "object"},
                    "build": not_during_registration,
                }
            ],
        },
    }
    monkeypatch.setitem(__import__("sys").modules, module.__name__, module)
    catalog = load_addons(
        AddonLoaderConfig(modules=[module.__name__], package_dirs=[]), base_dir=tmp_path
    )
    addon = catalog.registrations[0]
    assert addon.metadata == {"existing": True}
    assert "sample-driver" in addon.connector_drivers
    description = IntegrationRegistry(catalog).describe()[0]
    assert description["credentials"] == [{"id": "login", "label": "Login", "required": True}]
    assert description["workflow_starters"][0]["input_schema"] == {"type": "object"}
    json.dumps(description)


def test_disabled_package_is_not_imported_for_integration_registration(tmp_path: Path) -> None:
    package = tmp_path / "packages" / "sample"
    package.mkdir(parents=True)
    (package / "addon.json").write_text(json.dumps({"addon_id": "sample", "module": "unavailable"}))
    (package / "unavailable.py").write_text(
        "raise RuntimeError('must not import disabled integration')"
    )
    catalog = load_addons(
        AddonLoaderConfig(package_dirs=["packages"], enabled_addon_ids=[]), base_dir=tmp_path
    )
    assert IntegrationRegistry(catalog).describe() == []


def test_readiness_is_scoped_and_never_resolves_credentials_or_starts_workers(
    tmp_path: Path,
) -> None:
    called = []

    def configured(ctx):
        called.append((ctx.addon_id, ctx.directory_path, ctx.project_dir))
        return ctx.directory_path == "root/department"

    def forbidden(_):
        pytest.fail("Readiness resolved secret material or created a worker")

    addon = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            credentials=(IntegrationCredentialProvider("login", "Login", configured, forbidden),),
            workers=(IntegrationBackgroundWorker("poll", "Poll", forbidden),),
        ),
    )
    service = registry(addon)
    assert (
        service.readiness("sample", context(tmp_path, directory="root/department"))["status"]
        == "ready"
    )
    assert service.readiness("sample", context(tmp_path))["status"] == "blocked"
    assert called == [("sample", "root/department", tmp_path), ("sample", "root", tmp_path)]


def test_missing_dependency_only_blocks_its_integration_and_skips_callbacks(tmp_path: Path) -> None:
    def forbidden(_):
        pytest.fail("A dependent integration must not run before its dependency is enabled")

    blocked = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            required_addons=("identity",),
            readiness_checks=(IntegrationReadinessCheck("connection", "Connection", forbidden),),
        ),
    )
    service = registry(blocked, healthy("unrelated"))
    result = service.readiness("sample", context(tmp_path))
    assert result["status"] == "blocked"
    assert result["dependency_issues"] == [
        {"code": "dependency_not_enabled", "addon_id": "identity"}
    ]
    assert service.readiness("unrelated", context(tmp_path, "unrelated"))["status"] == "ready"


def test_dependency_cycles_and_transitive_missing_dependencies_are_reported(tmp_path: Path) -> None:
    service = registry(
        AddonRegistration(
            "sample", "Sample", integration=AddonIntegration(required_addons=("identity",))
        ),
        AddonRegistration(
            "identity",
            "Identity",
            integration=AddonIntegration(required_addons=("sample", "missing")),
        ),
    )
    result = service.readiness("sample", context(tmp_path))
    assert result["status"] == "blocked"
    assert {item["code"] for item in result["dependency_issues"]} == {
        "dependency_cycle",
        "dependency_not_enabled",
    }


def test_probe_errors_are_unknown_without_leaking_provider_secrets(tmp_path: Path) -> None:
    def failed(_):
        raise RuntimeError("Authorization: bearer secret-value")

    addon = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            credentials=(IntegrationCredentialProvider("login", "Login", failed, failed),),
            readiness_checks=(IntegrationReadinessCheck("connection", "Connection", failed),),
        ),
    )
    service = registry(addon)
    result = service.readiness("sample", context(tmp_path))
    assert result["status"] == "unknown"
    assert {item["code"] for item in result["checks"]} == {
        "credential_check_failed",
        "readiness_check_failed",
    }
    assert "secret-value" not in json.dumps(result)
    with pytest.raises(
        CredentialUnavailable, match="Integration credential is unavailable"
    ) as caught:
        service.resolve_credential("sample", "login", context(tmp_path))
    assert "secret-value" not in str(caught.value)


def test_optional_failure_does_not_block_a_verified_required_check(tmp_path: Path) -> None:
    addon = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            readiness_checks=(
                IntegrationReadinessCheck(
                    "required", "Required", lambda _: IntegrationCheckResult("ready", "connected")
                ),
                IntegrationReadinessCheck(
                    "optional",
                    "Optional",
                    lambda _: IntegrationCheckResult("blocked", "not_configured"),
                    required=False,
                ),
            )
        ),
    )
    assert registry(addon).readiness("sample", context(tmp_path))["status"] == "ready"
    unverified = AddonRegistration("sample", "Sample", integration=AddonIntegration())
    assert registry(unverified).readiness("sample", context(tmp_path))["status"] == "unknown"


def test_credentials_are_available_only_through_the_explicit_host_operation(tmp_path: Path) -> None:
    addon = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            credentials=(
                IntegrationCredentialProvider(
                    "login", "Login", lambda _: True, lambda ctx: {"token": ctx.directory_path}
                ),
            )
        ),
    )
    service = registry(addon)
    assert service.resolve_credential("sample", "login", context(tmp_path)) == {"token": "root"}
    with pytest.raises(ValueError, match="different add-on"):
        service.resolve_credential("sample", "login", context(tmp_path, "other"))
    with pytest.raises(KeyError):
        service.resolve_credential("disabled", "login", context(tmp_path, "disabled"))


def test_worker_factory_preserves_host_owned_lifecycle(tmp_path: Path) -> None:
    events = []

    class Worker:
        def run(self, stop):
            events.append((stop.is_set(), "ran"))

    def create(ctx):
        events.append((ctx.directory_path, "created"))
        return Worker()

    addon = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            workers=(IntegrationBackgroundWorker("poll", "Poll", create),)
        ),
    )
    service = registry(addon)
    assert events == []
    worker = service.create_worker("sample", "poll", context(tmp_path))
    assert events == [("root", "created")]
    stop = Event()
    stop.set()
    worker.run(stop)
    assert events[-1] == (True, "ran")


def test_starter_produces_a_standard_editable_workflow_and_validates_input_before_build(
    tmp_path: Path,
) -> None:
    calls = []

    def build(ctx, values):
        calls.append(values)
        return {
            "pack_id": "sample-report",
            "pack_version": "v1",
            "graphs": [
                {
                    "id": "report",
                    "name": "Report",
                    "directory_path": ctx.directory_path,
                    "node_ids": ["tick"],
                }
            ],
            "ir": {
                "nodes": [
                    {
                        "node": {
                            "id": "tick",
                            "display_name": "Scheduled report",
                            "type": "trigger:cron",
                            "config": {"expression": values["schedule"]},
                        }
                    }
                ],
                "edges": [],
            },
        }

    schema = {
        "type": "object",
        "properties": {"schedule": {"type": "string"}},
        "required": ["schedule"],
        "additionalProperties": False,
    }
    addon = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            workflow_starters=(IntegrationWorkflowStarter("report", "Report", schema, build),)
        ),
    )
    service = registry(addon)
    with pytest.raises(ValueError, match="schema validation"):
        service.build_workflow_starter(
            "sample", "report", context(tmp_path), {"unexpected": "secret-value"}
        )
    assert calls == []
    pack = service.build_workflow_starter(
        "sample",
        "report",
        context(tmp_path, directory="root/department"),
        {"schedule": "0 8 * * *"},
    )
    assert pack.graphs[0].directory_path == "root/department"
    composed = compose_workflow_ir(
        project_dir=tmp_path, workflow_packs=[], inline_ir=pack.ir.model_dump(mode="json")
    )
    assert composed.triggers[0]["config"]["expression"] == "0 8 * * *"
    assert list(tmp_path.iterdir()) == []


def test_starter_cannot_assign_nodes_to_another_directory(tmp_path: Path) -> None:
    addon = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            workflow_starters=(
                IntegrationWorkflowStarter(
                    "bad",
                    "Bad",
                    {"type": "object"},
                    lambda *_: {
                        "pack_id": "bad",
                        "pack_version": "v1",
                        "ir": {"nodes": [], "edges": []},
                        "graphs": [
                            {"id": "bad", "name": "Bad", "directory_path": "root/elsewhere"}
                        ],
                    },
                ),
            )
        ),
    )
    with pytest.raises(ValueError, match="requested directory"):
        registry(addon).build_workflow_starter("sample", "bad", context(tmp_path), {})


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array"},
        {"type": "object", "properties": {"remote": {"$ref": "https://example.test/schema"}}},
    ],
)
def test_settings_contract_rejects_non_object_or_remote_schema(schema) -> None:
    with pytest.raises(ValueError):
        IntegrationConfigSection("settings", "Settings", schema)


def test_contract_version_duplicate_ids_and_self_dependency_are_rejected() -> None:
    with pytest.raises(ValueError, match="version"):
        AddonIntegration(contract_version=2)
    section = IntegrationConfigSection("settings", "Settings", {"type": "object"})
    with pytest.raises(ValueError, match="duplicate"):
        AddonIntegration(settings=(section, section))
    with pytest.raises(ValueError, match="itself"):
        AddonRegistration(
            "sample", "Sample", integration=AddonIntegration(required_addons=("sample",))
        )


def test_settings_validation_does_not_echo_sensitive_input() -> None:
    addon = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            settings=(
                IntegrationConfigSection(
                    "settings",
                    "Settings",
                    {"type": "object", "properties": {"port": {"type": "integer"}}},
                ),
            )
        ),
    )
    service = registry(addon)
    service.validate_settings("sample", "settings", {"port": 443})
    with pytest.raises(ValueError) as caught:
        service.validate_settings("sample", "settings", {"port": "secret-value"})
    assert "secret-value" not in str(caught.value)


def test_core_runtime_with_no_addons_exposes_an_empty_integration_inventory(tmp_path: Path) -> None:
    from config_helpers import write_runtime_config
    from evander.admin.composition import build_admin_runtime

    config_path = write_runtime_config(
        tmp_path, addon_loader={"package_dirs": [], "enabled_addon_ids": []}
    )
    components = build_admin_runtime(config_path)
    assert components.integration_admin.inventory() == {
        "contract_version": 1,
        "integrations": [],
    }


def test_admin_inventory_and_readiness_require_owner_and_preserve_directory_scope(
    tmp_path: Path,
) -> None:
    calls = []
    addon = AddonRegistration(
        "sample",
        "Sample",
        integration=AddonIntegration(
            readiness_checks=(
                IntegrationReadinessCheck(
                    "scope",
                    "Scope",
                    lambda ctx: (
                        calls.append(ctx.directory_path)
                        or IntegrationCheckResult("ready", "connected")
                    ),
                ),
            )
        ),
    )
    service = IntegrationAdminService(
        catalog=LoadedAddonCatalog((addon,), ()),
        project_dir=tmp_path,
        runtime_dir=tmp_path,
        config=None,
        services=None,
        resolve_directory=lambda name: "root/department" if name == "department" else name,
    )

    class Auth:
        def require(self, request):
            if request.headers.get("x-role") is None:
                raise HTTPException(401)

        def require_permission(self, request, permission):
            assert permission == "admin.owner"
            if request.headers.get("x-role") != "owner":
                raise PermissionError("Owner required")

    app = Starlette(routes=addon_routes(SimpleNamespace(integration_admin=service), Auth()))

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/admin/integrations")).status_code == 401
            assert (
                await client.post(
                    "/api/admin/integrations/sample/readiness",
                    headers={"x-role": "viewer"},
                    json={},
                )
            ).status_code == 403
            response = await client.get("/api/admin/integrations", headers={"x-role": "owner"})
            assert response.json()["integrations"][0]["addon_id"] == "sample"
            assert calls == []
            response = await client.post(
                "/api/admin/integrations/sample/readiness",
                headers={"x-role": "owner"},
                json={"directory_path": "department"},
            )
            assert response.json()["status"] == "ready"
            assert calls == ["root/department"]
            response = await client.post(
                "/api/admin/integrations/disabled/readiness", headers={"x-role": "owner"}, json={}
            )
            assert response.status_code == 404

    asyncio.run(exercise())
