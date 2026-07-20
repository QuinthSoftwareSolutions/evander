from __future__ import annotations

from evander.addons import AddonRegistration

from .mcp import MCP_TOOL_CATALOG, register_mcp_tools
from .models import ADDON_ID
from .providers import register_providers


def register_addon() -> AddonRegistration:
    return AddonRegistration(
        addon_id=ADDON_ID,
        display_name="ADO Activity Add-on",
        mcp_tools=register_mcp_tools,
        mcp_tool_catalog=MCP_TOOL_CATALOG,
        workflow_providers=register_providers,
        metadata={
            "description": ("Durable Azure DevOps activity indexing for exhaustive weekly scans."),
        },
    )
