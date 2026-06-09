from __future__ import annotations

from agent_sandbox.addons import AddonAdminNavigation, AddonRegistration

from .api import admin_api_routes
from .providers import register_providers
from .repository import ADDON_ID
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
        workflow_providers=register_providers,
        metadata={
            "description": "Hierarchical team state model for optional agent-team orchestration.",
            "state_file": "state.json",
            "runtime_workflow_pack": "truman_team_addon.workflow_packs.team_runtime_v1",
        },
    )


__all__ = ["register_addon"]
