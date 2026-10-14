"""Compatibility registration for the optional Teams/Graph tools."""

from evander.optional_integrations import require_teams_graph_module, teams_graph_enabled


def create_teams_tools(context, helpers):
    if not teams_graph_enabled(context.config):
        return {}
    return require_teams_graph_module(
        "evander_teams_graph_addon.tools", context.config
    ).create_teams_tools(context, helpers)
