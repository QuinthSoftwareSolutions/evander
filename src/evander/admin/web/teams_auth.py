"""Keep existing Teams auth URLs available across optional-package installations."""

from starlette.routing import Route

from evander.admin.web.shared import json_error
from evander.optional_integrations import require_teams_graph_module, teams_graph_enabled


def teams_auth_routes(service, auth):
    config = getattr(service, "config", None)
    if teams_graph_enabled(config):
        return require_teams_graph_module(
            "evander_teams_graph_addon.admin.routes", config
        ).teams_auth_routes(service, auth)

    async def unavailable(request):
        auth.require(request)
        return json_error(
            "Teams/Graph is unavailable. Install evander-teams-graph-addon and enable teams-graph.",
            status_code=503,
        )

    return [
        Route(f"/api/teams-auth/{action}", unavailable, methods=[method])
        for action, method in (
            ("status", "GET"),
            ("identities", "GET"),
            ("channels", "GET"),
            ("login", "POST"),
            ("complete", "POST"),
            ("logout", "POST"),
        )
    ]


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(require_teams_graph_module("evander_teams_graph_addon.admin.routes"), name)
