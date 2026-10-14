"""Compatibility import for the separately installed Teams/Graph add-on."""

import sys

from evander.optional_integrations import require_teams_graph_module

_implementation = require_teams_graph_module("evander_teams_graph_addon.admin.voice")

sys.modules[__name__] = _implementation
