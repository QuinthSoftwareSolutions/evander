"""Compatibility shim for the MCP gateway entry point.

The gateway implementation now lives in :mod:`agent_sandbox.gateway.server`.
"""

from agent_sandbox.gateway.server import *  # noqa: F403
from agent_sandbox.gateway.server import create_app, create_server, main

__all__ = [name for name in globals() if not name.startswith("_")]
