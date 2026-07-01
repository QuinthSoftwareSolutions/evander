"""Compatibility shim for the MCP gateway entry point.

Canonical gateway modules are:

* :mod:`evander.gateway.server` for MCP tool registration.
* :mod:`evander.gateway.app` for the Starlette HTTP application.
* :mod:`evander.gateway.cli` for runtime bootstrap and the console entry point.
"""

from evander.gateway.server import *  # noqa: F403
from evander.gateway.app import create_app
from evander.gateway.cli import main
from evander.gateway.server import create_server

__all__ = [name for name in globals() if not name.startswith("_")]
