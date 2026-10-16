from pathlib import Path
from evander.addons import AddonAdminNavigation, AddonRegistration
from evander.integration_contracts import (
    AddonIntegration,
    IntegrationConfigSection,
    IntegrationReadinessCheck,
)
from .settings import Settings
from evander.resources.surfaces import (
    admin_routes,
    catalog,
    register_calls,
    register_lifecycle,
    ui_routes,
)
from evander.resources.providers import register_providers as resource_providers
from .adapter import Adapter
from .service import service
from evander.resources.starters import maintenance
from evander.resources.readiness import check as check_readiness

PREFIX = "sqlserver"
OPERATIONS = ["inspect", "query", "execute", "manage_database", "manage_principal"]


def register_mcp(server, context):
    register_lifecycle(server, context, service, PREFIX, control="control" in OPERATIONS)
    calls = [op for op in OPERATIONS if op != "control"]
    register_calls(
        server, context, service, PREFIX, calls, Adapter.argument_model, Adapter.read_operations
    )
    return [
        f"{PREFIX}_{op}" for op in ["discover", "acquire", "get", "reset", "release", *OPERATIONS]
    ]


def register_addon():
    return AddonRegistration(
        addon_id=PREFIX + "-addon",
        display_name="SQL Server",
        admin_navigation=AddonAdminNavigation(
            label="SQL Server",
            ui_path="/addons/" + PREFIX + "-addon/",
            description="Registered services, managed resources and disposable leases",
        ),
        admin_api_routes=lambda ctx: admin_routes(ctx, service),
        admin_ui_routes=lambda ctx: ui_routes(ctx, Path(__file__).parent),
        mcp_tools=register_mcp,
        mcp_tool_catalog=catalog(PREFIX, OPERATIONS),
        workflow_providers=lambda: resource_providers(
            service,
            PREFIX,
            Adapter.read_operations,
            Adapter.operations - Adapter.read_operations,
        ),
        integration=AddonIntegration(
            workflow_starters=(maintenance(PREFIX),),
            readiness_checks=(
                IntegrationReadinessCheck(
                    "connections",
                    "Registered connections",
                    lambda ctx: check_readiness(ctx, service),
                ),
            ),
            settings=(
                IntegrationConfigSection(
                    "settings", "SQL Server settings", Settings.model_json_schema()
                ),
            ),
        ),
        required_restart_services=(
            "evander-gateway",
            "evander-trigger-orchestrator",
            "evander-effect-orchestrator",
        ),
        requires_gateway_restart=True,
    )
