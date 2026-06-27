"""MCP gateway package compatibility exports."""

from .server import *  # noqa: F403
from .server import create_app, create_server, main

__all__ = [name for name in globals() if not name.startswith("_")]
