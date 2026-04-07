from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from agent_sandbox.admin_service import AdminService
from agent_sandbox.admin_web import create_app


def write_config(tmp_path: Path) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(
        "\n".join(
            [
                f'runtime_dir: "{runtime_dir}"',
                f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
                f'session_store_path: "{runtime_dir / "sessions.json"}"',
                f'approval_store_path: "{runtime_dir / "approvals.json"}"',
                f'draft_store_path: "{runtime_dir / "drafts.json"}"',
                f'email_store_path: "{runtime_dir / "emails.json"}"',
                f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_admin_web_exposes_workflow_editor_api(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    response = client.get("/api/workflow-editor/document")
    catalog_response = client.get("/api/workflow-editor/catalog")

    assert response.status_code == 200
    assert catalog_response.status_code == 200
    payload = response.json()
    catalog_payload = catalog_response.json()
    assert payload["worker_id"] == "trigger-orchestrator"
    assert payload["config"]["triggers"] == []
    assert payload["layout"] == {"nodes": {}}
    assert any(node["type"] == "effect:vm:exec" for node in catalog_payload["nodes"])
    vm_exec = next(node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:exec")
    assert any(field["key"] == "command" and field["bindable"] for field in vm_exec["fields"])
    ticket_created = next(node for node in catalog_payload["nodes"] if node["type"] == "trigger:ticket:created")
    assert all(field["key"] != "outputs" for field in ticket_created["fields"])


def test_admin_web_validates_and_saves_workflow_editor_document(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    payload = {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "cron-15m",
                    "display_name": "Every 15 minutes",
                    "type": "trigger:cron",
                    "expression": "*/15 * * * *",
                }
            ],
            "effects": [
                {
                    "id": "prompt-agent",
                    "display_name": "Prompt agent",
                    "type": "effect:agent:prompt",
                    "prompt": "Summarize",
                }
            ],
            "edges": [{"source_id": "cron-15m", "target_id": "prompt-agent"}],
        },
        "layout": {
            "nodes": {
                "cron-15m": {"x": 100, "y": 50},
                "prompt-agent": {"x": 300, "y": 50},
            }
        },
    }

    validate_response = client.post("/api/workflow-editor/validate", json=payload)
    save_response = client.put("/api/workflow-editor/document", json=payload)

    assert validate_response.status_code == 200
    assert save_response.status_code == 200
    assert save_response.json()["layout"]["nodes"]["prompt-agent"] == {"x": 300.0, "y": 50.0}
    assert (tmp_path / "config" / "trigger-orchestrator" / "layout.json").exists()


def test_admin_web_returns_400_for_invalid_workflow_editor_payload(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    response = client.post(
        "/api/workflow-editor/validate",
        json={
            "config": {
                "runtime_dir": str(tmp_path / "runtime"),
                "triggers": [
                    {
                        "id": "cron-15m",
                        "display_name": "Every 15 minutes",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    }
                ],
            },
            "layout": {"nodes": {"missing-node": {"x": 1, "y": 2}}},
        },
    )

    assert response.status_code == 400
    assert "unknown node IDs" in response.json()["error"]


def test_admin_web_load_normalizes_stale_filter_outputs(tmp_path: Path) -> None:
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        """
{
  "runtime_dir": "runtime",
  "triggers": [
    {
      "id": "trigger-ticket-created-1",
      "display_name": "Ticket created",
      "type": "trigger:ticket:created",
      "directories": ["root/team-a"]
    }
  ],
  "gates": [
    {
      "id": "gate-filter-1",
      "display_name": "Verify successful creation",
      "type": "gate:filter",
      "outputs": {
        "session_id": {"type": "string"},
        "vm_name": {"type": "string"},
        "labels": {"type": "object"},
        "success": {"type": "boolean"}
      },
      "when": {
        "all": [
          {"left": "${{effect-vm-create-1.success}}", "equals": true}
        ]
      }
    }
  ],
  "effects": [
    {
      "id": "effect-vm-create-1",
      "display_name": "Create Issue VM",
      "type": "effect:vm:create",
      "vm_name": "vm-1"
    }
  ],
  "queries": [],
  "edges": [
    {"source_id": "trigger-ticket-created-1", "target_id": "effect-vm-create-1"},
    {"source_id": "effect-vm-create-1", "target_id": "gate-filter-1"}
  ]
}
""".strip(),
        encoding="utf-8",
    )

    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    response = client.get("/api/workflow-editor/document")

    assert response.status_code == 200
    outputs = response.json()["config"]["gates"][0]["outputs"]
    assert outputs["target_dir"] == {"type": "string"}
    assert outputs["guest_repo_name"] == {"type": "string"}
    assert outputs["remote_url"] == {"type": "string"}


def test_admin_web_load_normalizes_await_agent_outputs_without_completion_trigger(tmp_path: Path) -> None:
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        """
{
  "runtime_dir": "runtime",
  "triggers": [
    {
      "id": "trigger-cron-1",
      "display_name": "Tick",
      "type": "trigger:cron",
      "expression": "* * * * *"
    }
  ],
  "gates": [
    {
      "id": "gate-await-agent-1",
      "display_name": "Await agent",
      "type": "gate:await-agent",
      "outputs": {}
    }
  ],
  "effects": [
    {
      "id": "effect-agent-prompt-1",
      "display_name": "Prompt agent",
      "type": "effect:agent:prompt",
      "session_id": "session-1",
      "prompt": "Continue"
    }
  ],
  "queries": [],
  "edges": [
    { "source_id": "trigger-cron-1", "target_id": "effect-agent-prompt-1" },
    { "source_id": "effect-agent-prompt-1", "target_id": "gate-await-agent-1" }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    response = client.get("/api/workflow-editor/document")

    assert response.status_code == 200
    gate = response.json()["config"]["gates"][0]
    assert gate["outputs"]["dispatch_id"] == {"type": "string"}
