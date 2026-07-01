from __future__ import annotations

from evander.workflow.node_state import workflow_node_is_enabled


def test_workflow_node_is_enabled_defaults_to_true() -> None:
    assert workflow_node_is_enabled({"id": "trigger-1"}) is True


def test_workflow_node_is_enabled_honors_top_level_false() -> None:
    assert workflow_node_is_enabled({"id": "trigger-1", "enabled": False}) is False


def test_workflow_node_is_enabled_honors_nested_config_false() -> None:
    assert workflow_node_is_enabled(
        {"id": "trigger-1", "config": {"enabled": False}}
    ) is False


def test_workflow_node_is_enabled_only_boolean_false_disables() -> None:
    assert workflow_node_is_enabled({"id": "trigger-1", "enabled": "false"}) is True
