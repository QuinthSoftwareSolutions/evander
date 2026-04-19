from __future__ import annotations

from agent_sandbox.effects.providers.default_schema import register_effect_node_schemas
from agent_sandbox.gates.providers.default_schema import register_gate_node_schemas
from agent_sandbox.queries.providers.default_schema import register_query_node_schemas
from agent_sandbox.triggers.providers.default_schema import register_trigger_node_schemas

_BOOTSTRAPPED = False


def bootstrap_default_node_schemas() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    register_trigger_node_schemas()
    register_gate_node_schemas()
    register_effect_node_schemas()
    register_query_node_schemas()
    _BOOTSTRAPPED = True
