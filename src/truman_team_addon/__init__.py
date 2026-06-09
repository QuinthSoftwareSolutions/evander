from __future__ import annotations

from agent_sandbox.addons import AddonRegistration

from .api import admin_api_routes
from .repository import ADDON_ID


def register_addon() -> AddonRegistration:
    return AddonRegistration(
        addon_id=ADDON_ID,
        display_name="Team Add-on",
        admin_api_routes=admin_api_routes,
        metadata={
            "description": "Hierarchical team state model for optional agent-team orchestration.",
            "state_file": "state.json",
        },
    )


__all__ = ["register_addon"]
