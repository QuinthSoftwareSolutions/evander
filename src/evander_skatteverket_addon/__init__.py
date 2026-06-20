from __future__ import annotations

from agent_sandbox.addons import AddonAdminNavigation, AddonRegistration

from .api import admin_api_routes
from .catalog import ADDON_ID, STATE_DB
from .mcp import register_mcp_tools
from .providers import register_providers
from .ui import admin_ui_routes


def register_addon() -> AddonRegistration:
    return AddonRegistration(
        addon_id=ADDON_ID,
        display_name="Skatteverket Add-on",
        admin_navigation=AddonAdminNavigation(
            label="Skatteverket",
            short_label="Tax",
            description="Host-side Skatteverket accounting and filing workflows.",
            ui_path="/addons/skatteverket-addon/",
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        mcp_tools=register_mcp_tools,
        workflow_providers=register_providers,
        metadata={
            "description": (
                "Skatteverket API catalog, draft filing state, approval-gated submission "
                "helpers, and host-side MCP tools."
            ),
            "state_file": STATE_DB,
        },
    )


__all__ = ["register_addon"]
