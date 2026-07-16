from __future__ import annotations

import json
from pathlib import Path

from evander.admin.composition import build_admin_runtime


def _write_gateway_config(tmp_path: Path) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(
        "\n".join(
            [
                f'runtime_dir: "{runtime_dir}"',
                f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
                f'session_store_path: "{runtime_dir / "sessions.json"}"',
                f'approval_store_path: "{runtime_dir / "approvals.json"}"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _write_trigger_config(tmp_path: Path, *, enable_team_addon: bool) -> None:
    runtime_dir = tmp_path / "runtime"
    payload: dict[str, object] = {
        "runtime_dir": str(runtime_dir),
        "provider_loader": {
            "provider_entry_points_enabled": False,
        },
    }
    if enable_team_addon:
        payload["addon_loader"] = {
            "modules": ["evander_team_addon"],
            "enabled_entry_points": False,
            "enabled_addon_ids": ["team-addon"],
        }
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload), encoding="utf-8")


def _write_invalid_trigger_config_with_team_addon(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "provider_loader": {
                    "provider_entry_points_enabled": False,
                },
                "addon_loader": {
                    "modules": ["evander_team_addon"],
                    "enabled_entry_points": False,
                    "enabled_addon_ids": ["team-addon"],
                },
                "triggers": [
                    {
                        "id": "broken-cron",
                        "type": "trigger:cron",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _catalog_payload(tmp_path: Path, *, enable_team_addon: bool) -> dict[str, object]:
    _write_trigger_config(tmp_path, enable_team_addon=enable_team_addon)
    runtime = build_admin_runtime(_write_gateway_config(tmp_path))
    return runtime.workflow_config_admin.read_workflow_editor_catalog(
        directory_path="root"
    ).model_dump(mode="json")


def test_workflow_editor_catalog_excludes_addon_nodes_when_addon_disabled(
    tmp_path: Path,
) -> None:
    payload = _catalog_payload(tmp_path, enable_team_addon=False)

    node_types = {node["type"] for node in payload["nodes"]}
    internal_event_types = {
        entry["event_type"] for entry in payload.get("internal_events", [])
    }

    assert "effect:team:wake-due-agents" not in node_types
    assert "query:team:state" not in node_types
    assert "team.agent.wake_requested" not in internal_event_types


def test_workflow_editor_catalog_includes_team_addon_nodes_when_enabled(
    tmp_path: Path,
) -> None:
    payload = _catalog_payload(tmp_path, enable_team_addon=True)

    nodes = {node["type"]: node for node in payload["nodes"]}
    internal_events = {entry["event_type"]: entry for entry in payload["internal_events"]}
    wake = nodes["effect:team:wake-due-agents"]
    finalize_dispatch = nodes["effect:team:finalize-agent-dispatch"]
    create_task = nodes["effect:team:create-task"]
    create_case = nodes["effect:team:create-case"]
    update_case = nodes["effect:team:update-case"]
    publish_review = nodes["effect:team:publish-review-branch"]
    consolidate = nodes["effect:team:consolidate-git-branches"]
    read_state = nodes["query:team:state"]
    internal_event_trigger = nodes["trigger:event"]
    internal_event_field = next(
        field for field in internal_event_trigger["fields"] if field["key"] == "event_type"
    )
    internal_event_options = {
        option["value"]: option for option in internal_event_field["options"]
    }

    assert "query:team:agent-action-artifact" not in nodes
    assert "effect:team:apply-agent-action" not in nodes
    assert "effect:team:apply-ephemeral-action" not in nodes
    assert set(internal_events) >= {
        "team.chat.message_queued",
        "team.agent.wake_requested",
        "team.ephemeral.requested",
        "team.ephemeral.completed",
        "team.operator.escalation_requested",
        "team.git.promotion_requested",
        "team.git.release_recovery_requested",
    }
    assert internal_events["team.agent.wake_requested"]["label"] == "Team agent wake requested"
    assert internal_events["team.agent.wake_requested"]["source"] == "Team Add-on"
    assert internal_event_options["team.agent.wake_requested"]["label"] == (
        "Team agent wake requested"
    )
    assert internal_event_options["team.agent.wake_requested"]["metadata"] == {
        "source": "Team Add-on"
    }
    assert wake["kind"] == "effect"
    assert wake["label"] == "Wake Team agents"
    assert wake["description"]
    assert [field["key"] for field in wake["fields"]] == [
        "team_id",
        "project_id",
        "agent_id",
        "limit",
        "lease_seconds",
        "wake_interval_seconds",
        "max_no_progress_count",
        "low_priority_wake_after_cycles",
        "respect_wake_interval",
        "resume_mode",
    ]
    assert all(field["description"] for field in wake["fields"])
    finalize_fields = {field["key"]: field for field in finalize_dispatch["fields"]}
    assert finalize_dispatch["kind"] == "effect"
    assert finalize_dispatch["label"] == "Finalize Team dispatch"
    assert finalize_fields["session_id"]["required"] is True
    assert finalize_fields["dispatch_id"]["required"] is True
    assert finalize_fields["agent_id"]["kind"] == "string"
    create_task_fields = {field["key"]: field for field in create_task["fields"]}
    assert create_task["kind"] == "effect"
    assert create_task["label"] == "Create Team task"
    assert create_task_fields["team_id"]["required"] is True
    assert create_task_fields["project_id"]["required"] is True
    assert create_task_fields["owner_agent_id"]["required"] is True
    assert create_task_fields["priority"]["kind"] == "number"
    assert create_task_fields["description"]["multiline"] is True
    assert create_task_fields["dependency_task_ids"]["json_value_type"] == "array"
    create_case_fields = {field["key"]: field for field in create_case["fields"]}
    assert create_case["kind"] == "effect"
    assert create_case["label"] == "Create Team case"
    assert create_case_fields["team_id"]["required"] is True
    assert create_case_fields["title"]["required"] is True
    assert create_case_fields["metadata"]["kind"] == "json"
    assert create_case_fields["metadata"]["json_value_type"] == "object"
    assert create_case_fields["task_ids"]["json_value_type"] == "array"
    update_case_fields = {field["key"]: field for field in update_case["fields"]}
    assert update_case["label"] == "Update Team case"
    assert update_case_fields["case_id"]["required"] is True
    assert update_case_fields["description"]["multiline"] is True
    publish_review_fields = {field["key"]: field for field in publish_review["fields"]}
    consolidate_fields = {field["key"]: field for field in consolidate["fields"]}
    assert publish_review_fields["directory_path"]["kind"] == "string"
    assert publish_review_fields["directory_path"]["required"] is False
    assert consolidate_fields["directory_path"]["kind"] == "string"
    assert consolidate_fields["directory_path"]["required"] is False
    state_fields = {field["key"]: field for field in read_state["fields"]}
    assert state_fields["include_records"]["kind"] == "boolean"
    assert state_fields["include_records"]["default_value"] is True


def test_workflow_editor_catalog_uses_loader_settings_when_workflow_config_is_invalid(
    tmp_path: Path,
) -> None:
    _write_invalid_trigger_config_with_team_addon(tmp_path)
    runtime = build_admin_runtime(_write_gateway_config(tmp_path))

    catalog_payload = runtime.workflow_config_admin.read_workflow_editor_catalog(
        directory_path="root"
    ).model_dump(mode="json")
    nodes = {node["type"]: node for node in catalog_payload["nodes"]}
    assert "trigger:cron" in nodes
    assert "effect:team:wake-due-agents" in nodes
