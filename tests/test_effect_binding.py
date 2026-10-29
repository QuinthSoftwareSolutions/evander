from __future__ import annotations

import pytest

from evander.effects.binding import BindingResolutionError, resolve_effect_config_bindings


def test_resolve_nested_object_and_list_bindings() -> None:
    raw = {
        "vm": "${{effect_1.vm_name}}",
        "metadata": {
            "ticket": "${{trigger_1.ticket_id}}",
            "labels": ["priority", "${{trigger_1.priority}}"],
            "message": "Ticket ${{trigger_1.ticket_id}}: ${{trigger_1.title}}",
        },
    }

    result = resolve_effect_config_bindings(
        raw,
        upstream_payload={
            "effect_1": {"vm_name": {"name": "vm-100"}},
            "trigger_1": {
                "ticket_id": 100,
                "priority": "high",
                "title": "Fix relay",
            },
        },
        declared_upstream_output_names={
            "effect_1": {"vm_name"},
            "trigger_1": {"ticket_id", "priority", "title"},
        },
    )

    assert result.resolved_config == {
        "vm": {"name": "vm-100"},
        "metadata": {
            "ticket": 100,
            "labels": ["priority", "high"],
            "message": "Ticket 100: Fix relay",
        },
    }
    assert result.trace.consumed_output_keys == (
        "effect_1.vm_name",
        "trigger_1.priority",
        "trigger_1.ticket_id",
        "trigger_1.title",
    )


def test_scalar_passthrough_without_bindings() -> None:
    raw = {"enabled": True, "max_retries": 3, "description": "plain text", "none": None}

    result = resolve_effect_config_bindings(
        raw,
        upstream_payload={"trigger_1": {"unused": "x"}},
        declared_upstream_output_names={"trigger_1": {"unused"}},
    )

    assert result.resolved_config == raw
    assert result.trace.consumed_output_keys == ()


def test_missing_output_reference_raises_in_strict_mode() -> None:
    with pytest.raises(BindingResolutionError, match="not found"):
        resolve_effect_config_bindings(
            {"prompt": "Hello ${{trigger_1.missing_key}}"},
            upstream_payload={"trigger_1": {"present": "ok"}},
            declared_upstream_output_names={"trigger_1": {"present", "missing_key"}},
            strict_validation=True,
        )


def test_mixed_literal_and_substitution_coerces_to_string() -> None:
    result = resolve_effect_config_bindings(
        {"prompt": "Ticket ${{trigger_1.ticket_id}} in ${{trigger_1.repo}}"},
        upstream_payload={"trigger_1": {"ticket_id": 42, "repo": "evander"}},
        declared_upstream_output_names={"trigger_1": {"ticket_id", "repo"}},
    )

    assert result.resolved_config == {"prompt": "Ticket 42 in evander"}
    assert result.trace.consumed_output_keys == ("trigger_1.repo", "trigger_1.ticket_id")


def test_malformed_reference_raises_in_strict_mode() -> None:
    with pytest.raises(BindingResolutionError, match="Malformed"):
        resolve_effect_config_bindings(
            {"prompt": "Hello ${{trigger_1.ticket_id"},
            upstream_payload={"trigger_1": {"ticket_id": 1}},
            declared_upstream_output_names={"trigger_1": {"ticket_id"}},
            strict_validation=True,
        )


def test_map_template_bindings_resolve_nested_mapping_values() -> None:
    raw = {
        "map": {
            "template": {
                "title": "Ticket ${{trigger_1.ticket_id}}",
                "summary": "Resume ${{effect_1.session_id}}",
            },
            "match": {
                "kind": "${{trigger_1.kind}}",
                "labels": ["priority", "${{trigger_1.priority}}"],
            },
        }
    }

    result = resolve_effect_config_bindings(
        raw,
        upstream_payload={
            "trigger_1": {"ticket_id": 42, "kind": "incident", "priority": "high"},
            "effect_1": {"session_id": "session-7"},
        },
        declared_upstream_output_names={
            "trigger_1": {"ticket_id", "kind", "priority"},
            "effect_1": {"session_id"},
        },
    )

    assert result.resolved_config == {
        "map": {
            "template": {
                "title": "Ticket 42",
                "summary": "Resume session-7",
            },
            "match": {
                "kind": "incident",
                "labels": ["priority", "high"],
            },
        }
    }
    assert result.trace.consumed_output_keys == (
        "effect_1.session_id",
        "trigger_1.kind",
        "trigger_1.priority",
        "trigger_1.ticket_id",
    )


def test_map_template_bindings_fail_strictly_when_output_is_missing() -> None:
    with pytest.raises(BindingResolutionError, match="not found"):
        resolve_effect_config_bindings(
            {"map": {"template": "Ticket ${{trigger_1.missing_key}}"}},
            upstream_payload={"trigger_1": {"ticket_id": 1}},
            declared_upstream_output_names={"trigger_1": {"ticket_id", "missing_key"}},
            strict_validation=True,
        )


def test_effect_binding_supports_literal_fallback_inside_interpolated_string() -> None:
    result = resolve_effect_config_bindings(
        {"prompt": 'Thread: ${{query_1.metadata.agent_thread_id ?? "new"}}'},
        upstream_payload={"query_1": {"metadata": {}}},
        declared_upstream_output_names={"query_1": {"metadata"}},
        strict_validation=True,
    )

    assert result.resolved_config == {"prompt": "Thread: new"}
    assert result.trace.consumed_output_keys == ()
