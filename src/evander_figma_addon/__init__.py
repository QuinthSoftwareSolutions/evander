from evander.addons import AddonAdminNavigation, AddonRegistration

from .api import admin_api_routes
from .mcp import MCP_TOOL_CATALOG, register_mcp_tools
from .models import ADDON_ID
from .providers import register_providers
from .ui import admin_ui_routes


def register_addon():
    return AddonRegistration(
        addon_id=ADDON_ID,
        display_name="Figma Add-on",
        admin_navigation=AddonAdminNavigation(
            label="Figma", description="Collaborative UI mockups", ui_path="/addons/figma-addon/"
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        mcp_tools=register_mcp_tools,
        mcp_tool_catalog=MCP_TOOL_CATALOG,
        workflow_providers=register_providers,
        metadata={
            "description": "Unattended native design authoring with workflow-defined collaboration."
        },
    )
