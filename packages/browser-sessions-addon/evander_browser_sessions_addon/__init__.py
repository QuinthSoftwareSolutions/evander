from evander.addons import AddonAdminNavigation, AddonRegistration
from .mcp import MCP_TOOL_CATALOG, register_mcp_tools
from .providers import register_providers
from .web import admin_api_routes, admin_ui_routes


def register_addon():
    return AddonRegistration(
        addon_id="browser-sessions-addon",
        display_name="Shared Browsers",
        admin_navigation=AddonAdminNavigation(
            label="Shared Browsers",
            description="Demonstrate, troubleshoot and review changes together",
            ui_path="/addons/browser-sessions-addon/",
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        mcp_tools=register_mcp_tools,
        mcp_tool_catalog=MCP_TOOL_CATALOG,
        workflow_providers=register_providers,
        requires_gateway_restart=True,
        required_restart_services=(
            "evander-gateway",
            "evander-admin-web",
            "evander-effect-orchestrator",
            "evander-trigger-orchestrator",
        ),
        metadata={
            "description": "Authenticated hosted previews and explicitly shared local Chrome tabs."
        },
    )
