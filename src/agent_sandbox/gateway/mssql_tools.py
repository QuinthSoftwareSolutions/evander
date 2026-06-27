"""Tool registration helpers for the MCP gateway.

This module is intentionally a placeholder during the first package split pass.
Tool implementations remain registered from :mod:`agent_sandbox.gateway.server` to keep
function names and registration names stable while follow-up changes move each domain.
"""

from __future__ import annotations

from typing import Any


def register_mssql_tools(server: Any, context: Any, deps: Any) -> None:
    """Register mssql tools with the MCP gateway server."""

    return None
