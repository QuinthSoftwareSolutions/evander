from evander.addons import AddonRegistration, AddonAdminNavigation
from evander.integration_contracts import (
    AddonIntegration,
    IntegrationConfigSection,
    IntegrationReadinessCheck,
)
from .models import Settings
from .readiness import check_readiness
from .mcp import CATALOG, register_mcp
from .providers import register_providers
from .web import admin_api_routes, admin_ui_routes


def register_addon():
    return AddonRegistration(
        addon_id="application-environments-addon",
        display_name="Application Environments",
        admin_navigation=AddonAdminNavigation(
            label="Application Environments",
            description="Launch and manage applications independently of agent turns",
            ui_path="/addons/application-environments-addon/",
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        mcp_tools=register_mcp,
        mcp_tool_catalog=CATALOG,
        workflow_providers=register_providers,
        integration=AddonIntegration(
            readiness_checks=(
                IntegrationReadinessCheck("workers", "Application workers", check_readiness),
            ),
            settings=(
                IntegrationConfigSection(
                    "settings", "Application environments", Settings.model_json_schema()
                ),
            ),
        ),
        requires_gateway_restart=True,
        required_restart_services=(
            "evander-gateway",
            "evander-admin-web",
            "evander-trigger-orchestrator",
            "evander-effect-orchestrator",
        ),
    )
