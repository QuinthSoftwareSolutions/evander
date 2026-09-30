from evander.addons import AddonAdminNavigation, AddonRegistration
from .api import admin_api_routes
from .browser import WordPressBrowserAdapter
from .driver import WordPressConnectorDriver
from .providers import register_providers
from .ui import admin_ui_routes


def register_addon():
    return AddonRegistration(
        addon_id="wordpress-addon",
        display_name="WordPress Add-on",
        admin_navigation=AddonAdminNavigation(
            label="WordPress",
            description="Publish and recover website design changes",
            ui_path="/addons/wordpress-addon/",
        ),
        admin_api_routes=admin_api_routes,
        admin_ui_routes=admin_ui_routes,
        connector_drivers={"wordpress": WordPressConnectorDriver},
        website_session_adapters={"wordpress": WordPressBrowserAdapter},
        workflow_providers=register_providers,
        metadata={
            "description": "Generic WordPress design editing with durable snapshots, browser sessions and external code recovery."
        },
    )
