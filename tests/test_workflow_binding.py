from __future__ import annotations

import pytest

from agent_sandbox.effects.binding import BindingResolutionError, resolve_effect_config_bindings
from agent_sandbox.workflow_binding import resolve_gate_map_bindings, resolve_workflow_bindings


def test_workflow_binding_resolution_matches_effect_compatibility_path() -> None:
    raw = {
        "prompt": "Ticket $(trigger_1.ticket_id)",
        "nested": ["$(effect_1.session_id)", {"name": "$(gate_1.vm_name)"}],
    }
    upstream = {
        "trigger_1": {"ticket_id": 42},
        "effect_1": {"session_id": "session-7"},
        "gate_1": {"vm_name": "vm-9"},
    }
    declared = {
        "trigger_1": {"ticket_id"},
        "effect_1": {"session_id"},
        "gate_1": {"vm_name"},
    }

    workflow_result = resolve_workflow_bindings(raw, upstream, declared)
    effect_result = resolve_effect_config_bindings(raw, upstream, declared)
    gate_result = resolve_gate_map_bindings(raw, upstream, declared)

    assert workflow_result == effect_result == gate_result
    assert workflow_result.resolved_config == {
        "prompt": "Ticket 42",
        "nested": ["session-7", {"name": "vm-9"}],
    }


def test_workflow_binding_resolution_rejects_undeclared_and_malformed_references() -> None:
    with pytest.raises(BindingResolutionError, match="not declared"):
        resolve_workflow_bindings(
            {"prompt": "$(trigger_1.missing)"},
            {"trigger_1": {"present": "ok"}},
            {"trigger_1": {"present"}},
        )

    with pytest.raises(BindingResolutionError, match="Malformed"):
        resolve_workflow_bindings(
            {"prompt": "$(trigger_1.missing"},
            {"trigger_1": {"missing": "x"}},
            {"trigger_1": {"missing"}},
        )
