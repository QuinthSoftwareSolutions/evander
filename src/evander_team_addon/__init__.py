from __future__ import annotations

from agent_sandbox.addons import AddonAdminNavigation, AddonRegistration

from .api import admin_api_routes
from .mcp import register_mcp_tools
from .providers import register_providers
from .repository import ADDON_ID, STATE_FILE
from .ui import admin_ui_routes


def register_addon() -> AddonRegistration:
    return AddonRegistration(
        addon_id=ADDON_ID,
        display_name="Team Add-on",
        admin_navigation=AddonAdminNavigation(
            label="Teams",
            short_label="Teams",
            description="Hierarchical agent-team orchestration.",
            ui_path="/addons/team-addon/",
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        mcp_tools=register_mcp_tools,
        workflow_providers=register_providers,
        metadata={
            "description": "Hierarchical team state model for optional agent-team orchestration.",
            "state_file": STATE_FILE,
            "runtime_workflow_pack": "evander_team_addon.workflow_packs.team_runtime_v1",
        },
    )


__all__ = ["register_addon"]
