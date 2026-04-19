from __future__ import annotations

from agent_sandbox.effects.providers.default_schema import register_effect_output_schemas
from agent_sandbox.gates.providers.default_schema import register_gate_output_schemas
from agent_sandbox.queries.providers.default_schema import register_query_output_schemas
from agent_sandbox.triggers.providers.default_schema import register_trigger_output_schemas

_BOOTSTRAPPED = False


def bootstrap_default_output_schemas() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    register_trigger_output_schemas()
    register_gate_output_schemas()
    register_effect_output_schemas()
    register_query_output_schemas()
    _BOOTSTRAPPED = True


# backward compatibility
bootstrap_default_node_schemas = bootstrap_default_output_schemas
