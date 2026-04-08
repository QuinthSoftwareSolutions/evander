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
    runs_response = client.get("/api/workflow-editor/runs")

    assert response.status_code == 200
    assert catalog_response.status_code == 200
    assert runs_response.status_code == 200
    payload = response.json()
    catalog_payload = catalog_response.json()
    runs_payload = runs_response.json()
    assert payload["worker_id"] == "trigger-orchestrator"
    assert payload["config"]["triggers"] == []
    assert payload["layout"] == {"nodes": {}}
    assert runs_payload["runs"] == []
    assert any(node["type"] == "effect:vm:exec" for node in catalog_payload["nodes"])
    vm_exec = next(node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:exec")
    assert any(field["key"] == "command" and field["bindable"] for field in vm_exec["fields"])
    issue_state = next(node for node in catalog_payload["nodes"] if node["type"] == "effect:issue:update-state")
    assert [field["key"] for field in issue_state["fields"]] == ["issue_id", "state", "sync_proxy_cache"]
    agent_prompt = next(node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:prompt")
    assert any(field["key"] == "model" and field["bindable"] for field in agent_prompt["fields"])
    output_mode = next(field for field in agent_prompt["fields"] if field["key"] == "output_mode")
    assert output_mode["options"] == [
        {"value": "immediate", "label": "Immediate"},
        {"value": "on_completion", "label": "On completion"},
    ]
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


def test_admin_web_exposes_active_workflow_runs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "trigger-orchestrator-events.json").write_text(
        """
{
  "evt-queued": {
    "event_id": "evt-queued",
    "trigger_id": "trigger-ticket",
    "trigger_type": "trigger:ticket:created",
    "trace_id": "trace-queued",
    "occurred_at": "2026-04-08T12:00:00Z",
    "payload": {"issue_number": "42"},
    "delivery_state": "pending",
    "inflight_at": null,
    "delivered_at": null,
    "idempotency_key": null
  },
  "evt-running": {
    "event_id": "evt-running",
    "trigger_id": "trigger-ticket",
    "trigger_type": "trigger:ticket:created",
    "trace_id": "trace-running",
    "occurred_at": "2026-04-08T12:01:00Z",
    "payload": {"issue_number": "43"},
    "delivery_state": "inflight",
    "inflight_at": "2026-04-08T12:01:05Z",
    "delivered_at": null,
    "idempotency_key": null
  }
}
""".strip(),
        encoding="utf-8",
    )
    (runtime_dir / "effect-orchestrator-executions.json").write_text(
        """
{
  "exec-running": {
    "execution_id": "exec-running",
    "idempotency_key": "evt-running::effect-prompt",
    "event_id": "evt-running",
    "trace_id": "trace-running",
    "source_node_id": "trigger-ticket",
    "source_trigger_type": "trigger:ticket:created",
    "effect_id": "effect-prompt",
    "effect_type": "effect:agent:prompt",
    "status": "running",
    "started_at": "2026-04-08T12:01:07Z",
    "finished_at": "2026-04-08T12:01:07Z",
    "resolved_config_hash": "abc",
    "resolved_config": {},
    "input_payload": {},
    "outputs": {}
  },
  "exec-waiting": {
    "execution_id": "exec-waiting",
    "idempotency_key": "evt-waiting::effect-prompt",
    "event_id": "evt-waiting",
    "trace_id": "trace-waiting",
    "source_node_id": "trigger-ticket",
    "source_trigger_type": "trigger:ticket:created",
    "effect_id": "effect-prompt",
    "effect_type": "effect:agent:prompt",
    "status": "waiting",
    "started_at": "2026-04-08T12:02:07Z",
    "finished_at": "2026-04-08T12:02:09Z",
    "resolved_config_hash": "def",
    "resolved_config": {},
    "input_payload": {},
    "outputs": {"dispatch_id": "dispatch-7"}
  }
}
""".strip(),
        encoding="utf-8",
    )
    (runtime_dir / "await-agent-pending.json").write_text(
        """
{
  "gate-await:dispatch-7": {
    "gate_id": "gate-await",
    "dispatch_id": "dispatch-7",
    "prompt_event_id": "evt-waiting",
    "prompt_source_node_id": "effect-prompt",
    "trace_id": "trace-waiting",
    "session_id": "session-7",
    "vm_name": "vm-7",
    "payload": {},
    "occurred_at": "2026-04-08T12:02:10Z"
  }
}
""".strip(),
        encoding="utf-8",
    )

    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    response = client.get("/api/workflow-editor/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["node_activity"]["effect-prompt"] == {"running": 1, "waiting": 1}
    assert payload["node_activity"]["gate-await"] == {"waiting": 1}
    run_ids = [run["event_id"] for run in payload["runs"]]
    assert run_ids == ["evt-waiting", "evt-running", "evt-queued"]
    waiting_run = next(run for run in payload["runs"] if run["event_id"] == "evt-waiting")
    assert waiting_run["active_nodes"][0]["node_id"] == "effect-prompt"
    assert waiting_run["active_nodes"][0]["status"] == "waiting"
    assert waiting_run["active_nodes"][1]["node_id"] == "gate-await"
    assert waiting_run["active_nodes"][1]["dispatch_id"] == "dispatch-7"


def test_admin_web_workflow_runs_resolve_relative_paths_independent_of_cwd(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "trigger-orchestrator-events.json").write_text(
        """
{
  "evt-running": {
    "event_id": "evt-running",
    "trigger_id": "trigger-ticket",
    "trigger_type": "trigger:ticket:created",
    "trace_id": "trace-running",
    "occurred_at": "2026-04-08T12:01:00Z",
    "payload": {"issue_number": "43"},
    "delivery_state": "inflight",
    "inflight_at": "2026-04-08T12:01:05Z",
    "delivered_at": null,
    "idempotency_key": null
  }
}
""".strip(),
        encoding="utf-8",
    )
    config_path = write_config(tmp_path)
    monkeypatch.chdir("/")

    client = TestClient(create_app(AdminService(config_path)))
    response = client.get("/api/workflow-editor/runs")

    assert response.status_code == 200
    payload = response.json()
    assert [run["event_id"] for run in payload["runs"]] == ["evt-running"]
    assert payload["runs"][0]["active_nodes"][0]["node_id"] == "trigger-ticket"


def test_admin_web_workflow_runs_resolve_project_relative_store_paths(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        """
{
  "runtime_dir": "runtime",
  "event_queue_store_path": "runtime/trigger-orchestrator-events.json",
  "effect_execution_store_path": "runtime/effect-orchestrator-executions.json",
  "gate_execution_store_path": "runtime/gate-orchestrator-executions.json",
  "triggers": [],
  "gates": [],
  "effects": [],
  "queries": [],
  "edges": []
}
""".strip(),
        encoding="utf-8",
    )
    (runtime_dir / "trigger-orchestrator-events.json").write_text(
        """
{
  "evt-running": {
    "event_id": "evt-running",
    "trigger_id": "trigger-ticket",
    "trigger_type": "trigger:ticket:created",
    "trace_id": "trace-running",
    "occurred_at": "2026-04-08T12:01:00Z",
    "payload": {},
    "delivery_state": "inflight",
    "inflight_at": "2026-04-08T12:01:05Z",
    "delivered_at": null,
    "idempotency_key": null
  }
}
""".strip(),
        encoding="utf-8",
    )

    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    response = client.get("/api/workflow-editor/runs")

    assert response.status_code == 200
    payload = response.json()
    assert [run["event_id"] for run in payload["runs"]] == ["evt-running"]


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
      "session_id": "session-1",
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
