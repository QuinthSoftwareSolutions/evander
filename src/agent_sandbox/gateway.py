"""Compatibility shim for the MCP gateway entry point.

Canonical gateway modules are:

* :mod:`agent_sandbox.gateway.server` for MCP tool registration.
* :mod:`agent_sandbox.gateway.app` for the Starlette HTTP application.
* :mod:`agent_sandbox.gateway.cli` for runtime bootstrap and the console entry point.
"""

from agent_sandbox.gateway.server import *  # noqa: F403
from agent_sandbox.gateway.app import create_app
from agent_sandbox.gateway.cli import main
from agent_sandbox.gateway.server import create_server

__all__ = [name for name in globals() if not name.startswith("_")]
