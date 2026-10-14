"""Availability and compatibility boundary for separately installed integrations.

Importing core never installs or searches the source tree for an optional package.
An explicit enabled-add-on list also applies to legacy runtime entry points.
"""

from importlib import import_module, util
from typing import Any

from evander.integration_services.teams import ManagedTeamsService

TEAMS_GRAPH_ADDON_ID = "teams-graph"
TEAMS_GRAPH_MODULE = "evander_teams_graph_addon"


class OptionalIntegrationUnavailable(RuntimeError):
    """An operation requires an optional integration that is unavailable."""


def teams_graph_enabled(config: Any = None) -> bool:
    addons = getattr(config, "addon_loader", None)
    enabled = getattr(addons, "enabled_addon_ids", None)
    if enabled is not None and TEAMS_GRAPH_ADDON_ID not in enabled:
        return False
    return util.find_spec(TEAMS_GRAPH_MODULE) is not None


def require_teams_graph_module(module: str, config: Any = None):
    if not teams_graph_enabled(config):
        raise OptionalIntegrationUnavailable(
            "Teams/Graph requires the evander-teams-graph-addon package. Install it in "
            "Evander's Python environment and enable 'teams-graph' if an explicit "
            "enabled_addon_ids list is configured."
        )
    # A broken installed package must surface its actual error, not appear absent.
    return import_module(module)


class UnavailableTeamsGraphService(ManagedTeamsService):
    """Keep unrelated runtime services usable; reject an explicit provider call."""

    def __init__(self, config: Any = None, *, normalize_directory_reference=None):
        self.config = config
        self.normalize_directory_reference = normalize_directory_reference

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def unavailable(*args, **kwargs):
            require_teams_graph_module(TEAMS_GRAPH_MODULE, self.config)
            raise OptionalIntegrationUnavailable("Rebuild the runtime after enabling Teams/Graph.")

        return unavailable
