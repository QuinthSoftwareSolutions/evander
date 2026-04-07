from __future__ import annotations

import pytest

from agent_sandbox.effects.binding import BindingResolutionError, resolve_effect_config_bindings
from agent_sandbox.workflow_binding import resolve_gate_map_bindings, resolve_workflow_bindings


def test_workflow_binding_resolution_matches_effect_compatibility_path() -> None:
    raw = {
        "prompt": "Ticket ${{trigger_1.ticket_id}}",
        "nested": ["${{effect_1.session_id}}", {"name": "${{gate_1.vm_name}}"}],
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
            {"prompt": "${{trigger_1.missing}}"},
            {"trigger_1": {"present": "ok"}},
            {"trigger_1": {"present"}},
        )

    with pytest.raises(BindingResolutionError, match="Malformed"):
        resolve_workflow_bindings(
            {"prompt": "${{trigger_1.missing"},
            {"trigger_1": {"missing": "x"}},
            {"trigger_1": {"missing"}},
        )


def test_workflow_binding_resolution_supports_nested_object_and_list_paths() -> None:
    raw = {
        "issue_number": "${{trigger-ticket-created-1.ticket.issue_number}}",
        "first_tag": "${{trigger-ticket-created-1.ticket.tags.0}}",
        "prompt": "Issue ${{trigger-ticket-created-1.ticket.issue_number}}",
    }
    upstream = {
        "trigger-ticket-created-1": {
            "ticket": {
                "issue_number": "42",
                "tags": ["bug", "urgent"],
            }
        }
    }
    declared = {"trigger-ticket-created-1": {"ticket"}}

    result = resolve_workflow_bindings(raw, upstream, declared)

    assert result.resolved_config == {
        "issue_number": "42",
        "first_tag": "bug",
        "prompt": "Issue 42",
    }
    assert result.trace.consumed_output_keys == (
        "trigger-ticket-created-1.ticket.issue_number",
        "trigger-ticket-created-1.ticket.tags.0",
    )


def test_workflow_binding_resolution_supports_system_values() -> None:
    seen: list[str] = []

    def system_value_resolver(path: str):
        seen.append(path)
        values = {
            "execution.event_id": "event-1",
            "execution.node_id": "effect-1",
            "now.utc_timestamp_compact": "20260406T101500Z",
            "unique.compact": "unique-1",
        }
        if path not in values:
            raise BindingResolutionError(f"Unknown system binding {path!r}")
        return values[path]

    result = resolve_workflow_bindings(
        {
            "session_id": "issue-${{trigger_1.ticket_id}}-${{system.now.utc_timestamp_compact}}-${{system.unique.compact}}",
            "metadata": "${{system.execution.event_id}}",
        },
        {"trigger_1": {"ticket_id": 42}},
        {"trigger_1": {"ticket_id"}},
        system_value_resolver=system_value_resolver,
    )

    assert result.resolved_config == {
        "session_id": "issue-42-20260406T101500Z-unique-1",
        "metadata": "event-1",
    }
    assert result.trace.consumed_output_keys == (
        "system.execution.event_id",
        "system.now.utc_timestamp_compact",
        "system.unique.compact",
        "trigger_1.ticket_id",
    )
    assert seen == [
        "now.utc_timestamp_compact",
        "unique.compact",
        "execution.event_id",
    ]
