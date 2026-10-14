"""Keep personal ADO connection URLs available across optional-package changes."""
from starlette.routing import Route

from evander.admin.web.shared import json_error
from evander.optional_integrations import azure_devops_enabled, require_azure_devops_module


def ado_connection_routes(service, auth, user_store):
    config = getattr(getattr(service, "runtime", service), "config", None)
    if azure_devops_enabled(config):
        return require_azure_devops_module("evander_azure_devops_addon.web", config).ado_connection_routes(service, auth, user_store)

    async def unavailable(request):
        auth.require(request)
        return json_error(
            "Azure DevOps is unavailable. Install evander-azure-devops-addon and enable azure-devops.",
            status_code=503,
        )

    return [
        Route("/api/admin/me/ado-connections", unavailable, methods=["GET", "PUT", "DELETE"]),
        Route("/api/admin/me/ado-connections/oauth/start", unavailable, methods=["POST"]),
        Route("/api/admin/me/ado-connections/oauth/callback", unavailable, methods=["GET"]),
    ]
