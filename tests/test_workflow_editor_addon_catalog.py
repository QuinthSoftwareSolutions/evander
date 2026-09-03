from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from evander.admin.composition import build_admin_runtime
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from config_helpers import write_runtime_config


def _prepare_runtime_config(tmp_path: Path, *, addon_module: str | None = None) -> Path:
    if addon_module is None:
        return write_runtime_config(tmp_path)
    return write_runtime_config(
        tmp_path,
        addon_loader={"modules": [addon_module], "enabled_entry_points": False},
    )


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
    runtime = build_admin_runtime(_prepare_runtime_config(tmp_path))
    return runtime.workflow_config_admin.read_workflow_editor_catalog(
        directory_path="root"
    ).model_dump(mode="json")


def test_workflow_editor_catalog_excludes_addon_nodes_when_addon_disabled(
    tmp_path: Path,
) -> None:
    payload = _catalog_payload(tmp_path, enable_team_addon=False)

    node_types = {node["type"] for node in payload["nodes"]}
    internal_event_types = {entry["event_type"] for entry in payload.get("internal_events", [])}

    assert "effect:team:wake-due-agents" not in node_types
    assert "query:team:state" not in node_types
    assert "team.agent.wake_requested" not in internal_event_types


def test_workflow_editor_catalog_includes_agent_artifact_staged_trigger(tmp_path: Path) -> None:
    payload = _catalog_payload(tmp_path, enable_team_addon=False)

    nodes = {node["type"]: node for node in payload["nodes"]}
    trigger = nodes["trigger:agent:artifact-staged"]

    assert trigger["kind"] == "trigger"
    assert trigger["label"] == "Agent artifact staged"
    assert [field["key"] for field in trigger["fields"]] == [
        "artifact_path",
        "path_prefix",
    ]
    assert all(field["description"] for field in trigger["fields"])


def test_workflow_editor_catalog_includes_team_addon_nodes_when_enabled(
    tmp_path: Path,
) -> None:
    payload = _catalog_payload(tmp_path, enable_team_addon=True)

    nodes = {node["type"]: node for node in payload["nodes"]}
    internal_events = {entry["event_type"]: entry for entry in payload["internal_events"]}
    wake = nodes["effect:team:wake-due-agents"]
    finalize_dispatch = nodes["effect:team:finalize-agent-dispatch"]
    create_task = nodes["effect:team:create-task"]
    set_task_work_state = nodes["effect:team:set-task-work-state"]
    create_case = nodes["effect:team:create-case"]
    update_case = nodes["effect:team:update-case"]
    register_agent = nodes["effect:team:register-agent"]
    restore_agent = nodes["effect:team:restore-agent"]
    retire_agent = nodes["effect:team:retire-agent"]
    publish_review = nodes["effect:team:publish-review-branch"]
    consolidate = nodes["effect:team:consolidate-git-branches"]
    read_state = nodes["query:team:state"]
    internal_event_trigger = nodes["trigger:event"]
    internal_event_field = next(
        field for field in internal_event_trigger["fields"] if field["key"] == "event_type"
    )
    internal_event_options = {option["value"]: option for option in internal_event_field["options"]}

    assert "query:team:agent-action-artifact" not in nodes
    assert "effect:team:apply-agent-action" not in nodes
    assert "effect:team:apply-ephemeral-action" not in nodes
    assert set(internal_events) >= {
        "team.chat.message_queued",
        "team.agent.wake_requested",
        "team.ephemeral.requested",
        "team.ephemeral.completed",
        "team.operator.escalation_requested",
        "team.case.closed",
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
        "run_global_maintenance",
        "worktree_cleanup_limit",
        "worktree_cleanup_grace_seconds",
        "worktree_cleanup_retry_seconds",
        "worktree_cleanup_recovery_seconds",
        "worktree_cleanup_dry_run",
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
    assert create_task_fields["git_base_branch"]["kind"] == "string"
    assert create_task_fields["git_base_branch"]["required"] is False
    assert create_task_fields["dependency_task_ids"]["json_value_type"] == "array"
    set_task_work_state_fields = {field["key"]: field for field in set_task_work_state["fields"]}
    assert set_task_work_state["label"] == "Set Team task work state"
    assert set_task_work_state_fields["task_id"]["required"] is True
    assert set_task_work_state_fields["state"]["required"] is True
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
    register_agent_fields = {field["key"]: field for field in register_agent["fields"]}
    restore_agent_fields = {field["key"]: field for field in restore_agent["fields"]}
    retire_agent_fields = {field["key"]: field for field in retire_agent["fields"]}
    assert register_agent_fields["session_id"]["required"] is True
    assert register_agent_fields["team_id"]["required"] is True
    assert register_agent_fields["role_id"]["required"] is True
    assert restore_agent["label"] == "Restore Team agent"
    assert restore_agent_fields["agent_id"]["required"] is True
    assert restore_agent_fields["new_session_id"]["required"] is True
    assert restore_agent_fields["expected_old_session_id"]["required"] is True
    assert restore_agent_fields["expected_team_id"]["required"] is True
    assert restore_agent_fields["role_id"]["required"] is True
    assert restore_agent_fields["project_id"]["required"] is False
    assert restore_agent_fields["reports_to_agent_id"]["required"] is False
    assert retire_agent_fields["agent_id"]["required"] is True
    assert retire_agent_fields["expected_session_id"]["required"] is True
    assert retire_agent_fields["expected_team_id"]["required"] is True
    publish_review_fields = {field["key"]: field for field in publish_review["fields"]}
    consolidate_fields = {field["key"]: field for field in consolidate["fields"]}
    assert publish_review_fields["directory_path"]["kind"] == "string"
    assert publish_review_fields["directory_path"]["required"] is False
    assert consolidate_fields["directory_path"]["kind"] == "string"
    assert consolidate_fields["directory_path"]["required"] is False
    assert consolidate_fields["commit_message"]["kind"] == "string"
    assert consolidate_fields["commit_message"]["required"] is False
    state_fields = {field["key"]: field for field in read_state["fields"]}
    assert state_fields["include_records"]["kind"] == "boolean"
    assert state_fields["include_records"]["default_value"] is True


def test_workflow_editor_catalog_enriches_team_bridge_target_options(
    tmp_path: Path,
) -> None:
    _write_trigger_config(tmp_path, enable_team_addon=True)
    runtime = build_admin_runtime(_prepare_runtime_config(tmp_path))
    SqliteCommunicationTargetsRepository(
        communication_targets_db_path(runtime.config.runtime_dir)
    ).write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "bridge-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["read", "write"]},
                },
                {
                    "id": "bridge-teams-reply",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/channel",
                    "policy": {"capabilities": ["read", "reply"]},
                },
                {
                    "id": "read-only-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345679",
                    "policy": {"capabilities": ["read"]},
                },
                {
                    "id": "write-only-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345680",
                    "policy": {"capabilities": ["write"]},
                },
            ]
        },
    )

    payload = runtime.workflow_config_admin.read_workflow_editor_catalog(
        directory_path="root"
    ).model_dump(mode="json")
    nodes = {node["type"]: node for node in payload["nodes"]}
    bridge = nodes["effect:team:enqueue-operator-chat-message"]
    import_attachments_field = next(
        field for field in bridge["fields"] if field["key"] == "import_attachments"
    )
    target_field = next(
        field for field in bridge["fields"] if field["key"] == "communication_target_id"
    )
    options = {option["value"]: option for option in target_field["options"]}

    assert set(options) == {"bridge-discord", "bridge-teams-reply"}
    assert import_attachments_field["default_value"] is False
    assert options["bridge-discord"]["metadata"]["required_capabilities"] == [
        "read",
        "write or reply",
    ]
    assert options["bridge-discord"]["metadata"]["thread_id"]["top_level"]["supported"] is True
    assert options["bridge-teams-reply"]["metadata"]["thread_id"]["reply"]["supported"] is True


def test_workflow_editor_catalog_uses_loader_settings_when_workflow_config_is_invalid(
    tmp_path: Path,
) -> None:
    _write_invalid_trigger_config_with_team_addon(tmp_path)
    runtime = build_admin_runtime(_prepare_runtime_config(tmp_path))

    catalog_payload = runtime.workflow_config_admin.read_workflow_editor_catalog(
        directory_path="root"
    ).model_dump(mode="json")
    nodes = {node["type"]: node for node in catalog_payload["nodes"]}
    assert "trigger:cron" in nodes
    assert "effect:team:wake-due-agents" in nodes


def test_workflow_editor_tool_selectors_include_enabled_addon_tools(tmp_path: Path) -> None:
    _write_trigger_config(tmp_path, enable_team_addon=False)
    runtime = build_admin_runtime(
        _prepare_runtime_config(tmp_path, addon_module="evander_bokio_addon")
    )

    payload = runtime.workflow_config_admin.read_workflow_editor_catalog(
        directory_path="root"
    ).model_dump(mode="json")
    nodes = {node["type"]: node for node in payload["nodes"]}
    agent_create_fields = {field["key"]: field for field in nodes["effect:agent:create"]["fields"]}
    approve_fields = {
        field["key"]: field for field in nodes["effect:session:approve-mcp-tools-for-run"]["fields"]
    }
    allowed_tools = {
        option["value"]: option for option in agent_create_fields["allowed_tools"]["options"]
    }
    approval_tools = {option["value"]: option for option in approve_fields["tools"]["options"]}

    assert allowed_tools["bokio_create_supplier_invoice"]["metadata"] == {
        "tool_group": "Bokio",
        "provider": "bokio-addon",
        "addon_id": "bokio-addon",
    }
    assert (
        approval_tools["bokio_create_supplier_invoice"]
        == allowed_tools["bokio_create_supplier_invoice"]
    )


def test_workflow_editor_agent_create_uses_fixed_enabled_worker_profile_options(
    tmp_path: Path,
) -> None:
    _write_trigger_config(tmp_path, enable_team_addon=False)
    runtime = build_admin_runtime(_prepare_runtime_config(tmp_path))
    runtime.workflow_config_admin.set_codex_auth_admin(
        SimpleNamespace(
            list_profiles=lambda directory_path: {
                "directory_path": directory_path,
                "profiles": [
                    {
                        "profile_id": "team-a",
                        "display_name": "Team A",
                        "enabled": True,
                        "signed_in": True,
                        "account_id": "account-a",
                        "email": "secret@example.com",
                        "plan_type": "business",
                    },
                    {
                        "profile_id": "team-disabled",
                        "display_name": "Disabled",
                        "enabled": False,
                    },
                ],
            }
        )
    )

    payload = runtime.workflow_config_admin.read_workflow_editor_catalog(
        directory_path="root"
    ).model_dump(mode="json")
    agent_create = next(node for node in payload["nodes"] if node["type"] == "effect:agent:create")
    field = next(
        item for item in agent_create["fields"] if item["key"] == "subscription_profile_id"
    )

    assert field["bindable"] is False
    assert field["metadata"]["immutable"] is True
    assert field["options"] == [
        {
            "value": "team-a",
            "label": "Team A",
        }
    ]
