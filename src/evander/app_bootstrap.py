from __future__ import annotations

from evander.config import GatewayConfig
from evander.runtime import RuntimeContext, _build_runtime_context


def build_runtime(config: GatewayConfig) -> RuntimeContext:
    """Assemble the application runtime and platform integrations."""
    return _build_runtime_context(config)
