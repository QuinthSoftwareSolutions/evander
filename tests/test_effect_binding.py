from __future__ import annotations

import pytest

from agent_sandbox.effects.binding import BindingResolutionError, resolve_effect_config_bindings


def test_resolve_nested_object_and_list_bindings() -> None:
    raw = {
        "vm": "$(vm_name)",
        "metadata": {
            "ticket": "$(ticket_id)",
            "labels": ["priority", "$(priority)"],
            "message": "Ticket $(ticket_id): $(title)",
        },
    }

    result = resolve_effect_config_bindings(
        raw,
        upstream_payload={
            "vm_name": {"name": "vm-100"},
            "ticket_id": 100,
            "priority": "high",
            "title": "Fix relay",
        },
        declared_upstream_output_names={"vm_name", "ticket_id", "priority", "title"},
    )

    assert result.resolved_config == {
        "vm": {"name": "vm-100"},
        "metadata": {
            "ticket": 100,
            "labels": ["priority", "high"],
            "message": "Ticket 100: Fix relay",
        },
    }
    assert result.trace.consumed_output_keys == ("priority", "ticket_id", "title", "vm_name")


def test_scalar_passthrough_without_bindings() -> None:
    raw = {"enabled": True, "max_retries": 3, "description": "plain text", "none": None}

    result = resolve_effect_config_bindings(
        raw,
        upstream_payload={"unused": "x"},
        declared_upstream_output_names={"unused"},
    )

    assert result.resolved_config == raw
    assert result.trace.consumed_output_keys == ()


def test_missing_output_reference_raises_in_strict_mode() -> None:
    with pytest.raises(BindingResolutionError, match="not found"):
        resolve_effect_config_bindings(
            {"prompt": "Hello $(missing_key)"},
            upstream_payload={"present": "ok"},
            declared_upstream_output_names={"present", "missing_key"},
            strict_validation=True,
        )


def test_mixed_literal_and_substitution_coerces_to_string() -> None:
    result = resolve_effect_config_bindings(
        {"prompt": "Ticket $(ticket_id) in $(repo)"},
        upstream_payload={"ticket_id": 42, "repo": "agent-orchestrator"},
        declared_upstream_output_names={"ticket_id", "repo"},
    )

    assert result.resolved_config == {"prompt": "Ticket 42 in agent-orchestrator"}
    assert result.trace.consumed_output_keys == ("repo", "ticket_id")


def test_malformed_reference_raises_in_strict_mode() -> None:
    with pytest.raises(BindingResolutionError, match="Malformed"):
        resolve_effect_config_bindings(
            {"prompt": "Hello $(ticket_id"},
            upstream_payload={"ticket_id": 1},
            declared_upstream_output_names={"ticket_id"},
            strict_validation=True,
        )
