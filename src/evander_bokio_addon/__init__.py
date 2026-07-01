from __future__ import annotations

from evander.addons import AddonAdminNavigation, AddonRegistration

from .api import admin_api_routes
from .mcp import register_mcp_tools
from .providers import register_providers
from .state import ADDON_ID, STATE_FILE
from .ui import admin_ui_routes


def register_addon() -> AddonRegistration:
    return AddonRegistration(
        addon_id=ADDON_ID,
        display_name="Bokio Add-on",
        admin_navigation=AddonAdminNavigation(
            label="Bokio",
            short_label="Bokio",
            description="Private Bokio bookkeeping integration.",
            ui_path="/addons/bokio-addon/",
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        mcp_tools=register_mcp_tools,
        workflow_providers=register_providers,
        metadata={
            "description": "Assisted bookkeeping integration for Bokio private integrations.",
            "state_file": STATE_FILE,
        },
    )


__all__ = ["register_addon"]
