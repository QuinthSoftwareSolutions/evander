from evander.addons import AddonAdminNavigation, AddonRegistration
from evander.integration_contracts import AddonIntegration


def register_addon():
    from .api import admin_api_routes
    from .mcp import MCP_TOOL_CATALOG, register_mcp_tools
    from .providers import register_providers
    from .ui import admin_ui_routes

    return AddonRegistration(
        addon_id="test-fixtures-addon",
        display_name="Test Fixtures",
        admin_navigation=AddonAdminNavigation(
            label="Test fixtures",
            description="Task-owned SQL Server and RabbitMQ fixtures",
            ui_path="/addons/test-fixtures-addon/",
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        mcp_tools=register_mcp_tools,
        mcp_tool_catalog=MCP_TOOL_CATALOG,
        workflow_providers=register_providers,
        integration=AddonIntegration(required_addons=("team-addon",)),
        requires_gateway_restart=True,
        required_restart_services=(
            "evander-gateway",
            "evander-trigger-orchestrator",
            "evander-effect-orchestrator",
        ),
    )
