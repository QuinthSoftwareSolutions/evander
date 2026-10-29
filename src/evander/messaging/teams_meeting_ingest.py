"""Compatibility import for the separately installed Teams/Graph add-on."""

from evander.optional_integrations import require_teams_graph_module

_implementation = require_teams_graph_module('evander_teams_graph_addon.messaging.meeting_ingest')

if __name__ == "__main__":
    _implementation.main()
else:
    import sys
    sys.modules[__name__] = _implementation
