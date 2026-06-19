from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import create_app


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
            "modules": ["truman_team_addon"],
            "enabled_entry_points": False,
            "enabled_addon_ids": ["team-addon"],
        }
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload), encoding="utf-8")


def _catalog_payload(tmp_path: Path, *, enable_team_addon: bool) -> dict[str, object]:
    _write_trigger_config(tmp_path, enable_team_addon=enable_team_addon)
    client = TestClient(create_app(AdminService(_write_gateway_config(tmp_path))))
    response = client.get("/api/workflow-editor/catalog?directory_path=root")
    assert response.status_code == 200
    return response.json()


def test_workflow_editor_catalog_excludes_addon_nodes_when_addon_disabled(
    tmp_path: Path,
) -> None:
    payload = _catalog_payload(tmp_path, enable_team_addon=False)

    node_types = {node["type"] for node in payload["nodes"]}

    assert "effect:team:wake-due-agents" not in node_types
    assert "query:team:state" not in node_types


def test_workflow_editor_catalog_includes_team_addon_nodes_when_enabled(
    tmp_path: Path,
) -> None:
    payload = _catalog_payload(tmp_path, enable_team_addon=True)

    nodes = {node["type"]: node for node in payload["nodes"]}
    wake = nodes["effect:team:wake-due-agents"]
    apply_action = nodes["effect:team:apply-agent-action"]
    read_state = nodes["query:team:state"]

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
        "action_artifact_path",
        "resume_mode",
    ]
    assert all(field["description"] for field in wake["fields"])
    action_fields = {field["key"]: field for field in apply_action["fields"]}
    assert action_fields["action"]["kind"] == "json"
    assert action_fields["action"]["json_value_type"] == "object"
    assert action_fields["valid"]["kind"] == "boolean"
    assert action_fields["valid"]["bindable"]
    state_fields = {field["key"]: field for field in read_state["fields"]}
    assert state_fields["include_records"]["kind"] == "boolean"
    assert state_fields["include_records"]["default_value"] is True
