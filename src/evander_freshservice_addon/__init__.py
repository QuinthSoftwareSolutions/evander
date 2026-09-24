from __future__ import annotations

from evander.addons import AddonRegistration

from .connector import FreshserviceConnectorDriver


ADDON_ID = "freshservice-addon"


def register_addon() -> AddonRegistration:
    return AddonRegistration(
        addon_id=ADDON_ID,
        display_name="Freshservice",
        connector_drivers={"freshservice": FreshserviceConnectorDriver},
        metadata={
            "description": (
                "Read Freshservice tickets and stage bounded ticket attachments through the "
                "connector capability boundary."
            )
        },
    )
