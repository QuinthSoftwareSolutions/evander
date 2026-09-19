from evander.addons import AddonAdminNavigation, AddonRegistration

from .api import admin_api_routes
from .mcp import MCP_TOOL_CATALOG, register_mcp_tools
from .providers import register_providers
from .ui import admin_ui_routes


def register_addon() -> AddonRegistration:
    return AddonRegistration(
        addon_id="miro-addon",
        display_name="Miro Add-on",
        admin_navigation=AddonAdminNavigation(
            label="Miro",
            description="Visual collaboration connections and activity.",
            ui_path="/addons/miro-addon/",
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        mcp_tools=register_mcp_tools,
        mcp_tool_catalog=MCP_TOOL_CATALOG,
        workflow_providers=register_providers,
        metadata={
            "description": "Editable diagrams and workflow-owned collaboration through host-managed Miro connections."
        },
    )
