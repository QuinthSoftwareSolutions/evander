"""MCP gateway package compatibility exports.

Canonical gateway modules are:

* :mod:`evander.gateway.server` for MCP tool registration.
* :mod:`evander.gateway.app` for the Starlette HTTP application.
* :mod:`evander.gateway.cli` for runtime bootstrap and the console entry point.
"""

from . import server as _server
from .server import *  # noqa: F403
from .app import create_app
from .cli import main
from .server import create_server

# Preserve historical access to private helpers during the gateway module migration.
for _name in dir(_server):
    if _name.startswith("_") and _name not in globals():
        globals()[_name] = getattr(_server, _name)

__all__ = [name for name in globals() if not name.startswith("_")]
