from __future__ import annotations

from evander.addons import AddonAdminNavigation, AddonRegistration

from .api import admin_api_routes
from .mcp import register_mcp_tools
from .state import ADDON_ID, STATE_FILE
from .ui import admin_ui_routes


def register_addon() -> AddonRegistration:
    return AddonRegistration(
        addon_id=ADDON_ID,
        display_name="MSSQL Seed Add-on",
        admin_navigation=AddonAdminNavigation(
            label="MSSQL Seeds",
            short_label="MSSQL Seeds",
            description="Approved MSSQL .bak seed restore catalog for container agents.",
            ui_path="/addons/mssql-seed-addon/",
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        mcp_tools=register_mcp_tools,
        metadata={
            "description": (
                "Restores operator-approved MSSQL backup seeds into worker-provisioned "
                "container-agent databases."
            ),
            "state_file": STATE_FILE,
        },
    )


__all__ = ["register_addon"]
