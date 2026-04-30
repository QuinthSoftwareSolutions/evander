from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import MethodType
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import _workflow_run_events, create_app
from agent_sandbox.admin.web.shared import AdminWebConfig
from agent_sandbox.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from agent_sandbox.execution.models import ExecutionBackendKind, ExecutionStatus, ExecutionStatusKind
from agent_sandbox.models import LocalIssueRecord, SessionSummary, VmActionResult, VmSummary


def write_config(tmp_path: Path, *, enable_directory_scoped_config: bool = False) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    lines = [
        f'runtime_dir: "{runtime_dir}"',
        f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
        f'session_store_path: "{runtime_dir / "sessions.json"}"',
        f'approval_store_path: "{runtime_dir / "approvals.json"}"',
        f'draft_store_path: "{runtime_dir / "drafts.json"}"',
        f'email_store_path: "{runtime_dir / "emails.json"}"',
        f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
    ]
    if enable_directory_scoped_config:
        lines.append("enable_directory_scoped_config: true")
    config_path.write_text("\n".join(lines), encoding="utf-8")
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
    agent_exec = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:exec"
    )
    assert [field["key"] for field in agent_exec["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "vm_user",
        "command",
        "working_directory",
        "environment",
        "stdin",
        "timeout_seconds",
        "check",
    ]
    vm_delete = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:delete-files"
    )
    assert [field["key"] for field in vm_delete["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "vm_user",
        "paths",
        "recursive",
        "missing_ok",
    ]
    issue_state = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:issue:update-state"
    )
    assert [field["key"] for field in issue_state["fields"]] == [
        "issue_id",
        "state",
        "sync_proxy_cache",
    ]
    agent_prompt = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:prompt"
    )
    assert any(field["key"] == "model" and field["bindable"] for field in agent_prompt["fields"])
    agent_enqueue_prompt = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:enqueue-prompt"
    )
    assert [field["key"] for field in agent_enqueue_prompt["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "prompt",
        "model",
        "vm_user",
        "resume_thread_id",
    ]
    bash_script = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:bash:script"
    )
    assert [field["key"] for field in bash_script["fields"]] == [
        "script_path",
        "args",
        "working_directory",
        "environment",
        "stdin",
        "timeout_seconds",
        "check",
    ]
    output_mode = next(field for field in agent_prompt["fields"] if field["key"] == "output_mode")
    assert output_mode["options"] == [
        {"value": "immediate", "label": "Immediate"},
        {"value": "on_completion", "label": "On completion"},
    ]
    ticket_created = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:ticket:created"
    )
    assert all(field["key"] != "outputs" for field in ticket_created["fields"])
    review_request = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:review:new-request"
    )
    assert review_request["fields"] == []
    script_run_request = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:script-run-request"
    )
    assert script_run_request["fields"] == []
    teams_message = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:teams:new-message"
    )
    assert [field["key"] for field in teams_message["fields"]] == ["targets", "user_ids"]
    await_agent = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:await-agent"
    )
    assert [field["key"] for field in await_agent["fields"]] == ["session_id", "vm_name", "outputs"]
    assert all(field["bindable"] for field in await_agent["fields"][:2])
    for_gate = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:for")
    assert for_gate["output_ports"] == [
        {"key": "body", "label": "Body", "description": "Loop body path while items remain."},
        {"key": "done", "label": "Done", "description": "Exit path after the final item."},
    ]
    issue_get = next(node for node in catalog_payload["nodes"] if node["type"] == "query:issue:get")
    assert [field["key"] for field in issue_get["fields"]] == ["issue_id"]
    vm_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:create"
    )
    assert any(field["key"] == "allowed_script_names" for field in vm_create["fields"])
    agent_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:create"
    )
    assert [field["key"] for field in agent_create["fields"]] == [
        "provider",
        "execution_backend",
        "vm_name",
        "session_id",
        "labels",
        "model",
        "vm_user",
        "ttl_hours",
        "allowed_tools",
        "allowed_script_names",
        "discord_channels",
        "email_mailboxes",
        "email_folders",
        "dotnet",
        "nuget",
        "mssql",
        "mssql_init_root",
        "playwright",
        "codex_skills",
        "directory_path",
    ]
    execution_backend = next(
        field for field in agent_create["fields"] if field["key"] == "execution_backend"
    )
    assert execution_backend["options"] == [
        {"value": "libvirt_vm", "label": "Libvirt VM"},
        {"value": "container_worker_vm", "label": "Container worker VM"},
    ]
    agent_clone_repo = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:clone-repo"
    )
    assert [field["key"] for field in agent_clone_repo["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "repo_source",
        "clone_branch",
        "vm_user",
        "directory_path",
    ]
    agent_bootstrap_frontend = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:agent:bootstrap-frontend"
    )
    assert [field["key"] for field in agent_bootstrap_frontend["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "host_checkout",
        "install_roots",
        "vm_user",
    ]
    agent_copy_files = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:copy-files"
    )
    assert [field["key"] for field in agent_copy_files["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "vm_user",
        "files",
    ]
    vm_clone_repo = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:clone-repo"
    )
    assert [field["key"] for field in vm_clone_repo["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "repo_source",
        "clone_branch",
        "vm_user",
        "directory_path",
    ]
    vm_bootstrap_python = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:bootstrap-python"
    )
    assert [field["key"] for field in vm_bootstrap_python["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "host_checkout",
        "target_dir",
        "vm_user",
    ]
    vm_bootstrap_frontend = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:bootstrap-frontend"
    )
    assert [field["key"] for field in vm_bootstrap_frontend["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "host_checkout",
        "install_roots",
        "vm_user",
    ]
    assert [field["key"] for field in vm_create["fields"]] == [
        "vm_name",
        "session_id",
        "labels",
        "model",
        "vm_user",
        "ttl_hours",
        "allowed_tools",
        "allowed_script_names",
        "discord_channels",
        "email_mailboxes",
        "email_folders",
        "dotnet",
        "nuget",
        "mssql",
        "mssql_init_root",
        "playwright",
        "codex_skills",
        "directory_path",
    ]


def test_admin_web_exposes_workflow_run_stream(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    async def read_first_event() -> str:
        stream = _workflow_run_events(service, RequestStub())  # type: ignore[arg-type]
        try:
            return await asyncio.wait_for(anext(stream), timeout=1.0)
        finally:
            await stream.aclose()

    assert asyncio.run(read_first_event()).startswith("event: snapshot\n")


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
    assert isinstance(save_response.json().get("revision"), str)
    assert isinstance(save_response.json().get("saved_at"), str)
    assert (tmp_path / "config" / "trigger-orchestrator" / "layout.json").exists()


def test_admin_web_saves_disconnected_workflow_editor_document(tmp_path: Path) -> None:
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
            "edges": [],
        },
        "layout": {
            "nodes": {
                "cron-15m": {"x": 100, "y": 50},
                "prompt-agent": {"x": 300, "y": 50},
            }
        },
    }

    save_response = client.put("/api/workflow-editor/document", json=payload)

    assert save_response.status_code == 200
    assert save_response.json()["config"]["edges"] == []


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


def test_admin_web_can_clear_specific_workflow_run(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "trigger-orchestrator-events.json").write_text(
        """
{
  "evt-stale": {
    "event_id": "evt-stale",
    "trigger_id": "trigger-ticket",
    "trigger_type": "trigger:ticket:created",
    "trace_id": "trace-stale",
    "occurred_at": "2026-04-08T12:00:00Z",
    "payload": {"issue_number": "42"},
    "delivery_state": "inflight",
    "inflight_at": "2026-04-08T12:00:05Z",
    "delivered_at": null,
    "idempotency_key": null
  },
  "evt-keep": {
    "event_id": "evt-keep",
    "trigger_id": "trigger-ticket",
    "trigger_type": "trigger:ticket:created",
    "trace_id": "trace-keep",
    "occurred_at": "2026-04-08T12:01:00Z",
    "payload": {"issue_number": "43"},
    "delivery_state": "pending",
    "inflight_at": null,
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
  "exec-stale": {
    "execution_id": "exec-stale",
    "idempotency_key": "evt-stale::effect-prompt",
    "event_id": "evt-stale",
    "trace_id": "trace-stale",
    "source_node_id": "trigger-ticket",
    "source_trigger_type": "trigger:ticket:created",
    "effect_id": "effect-prompt",
    "effect_type": "effect:agent:prompt",
    "status": "waiting",
    "started_at": "2026-04-08T12:00:07Z",
    "finished_at": "2026-04-08T12:00:09Z",
    "resolved_config_hash": "abc",
    "resolved_config": {},
    "input_payload": {},
    "outputs": {"dispatch_id": "dispatch-stale"}
  }
}
""".strip(),
        encoding="utf-8",
    )
    (runtime_dir / "await-agent-pending.json").write_text(
        """
{
  "gate-await:dispatch-stale": {
    "gate_id": "gate-await",
    "dispatch_id": "dispatch-stale",
    "prompt_event_id": "evt-stale",
    "prompt_source_node_id": "effect-prompt",
    "trace_id": "trace-stale",
    "session_id": "session-stale",
    "vm_name": "vm-stale",
    "payload": {},
    "occurred_at": "2026-04-08T12:00:10Z"
  }
}
""".strip(),
        encoding="utf-8",
    )

    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    before_response = client.get("/api/workflow-editor/runs")
    clear_response = client.delete("/api/workflow-editor/runs/evt-stale")
    after_response = client.get("/api/workflow-editor/runs")

    assert before_response.status_code == 200
    assert [run["event_id"] for run in before_response.json()["runs"]] == ["evt-keep", "evt-stale"]
    assert clear_response.status_code == 200
    assert clear_response.json()["event_id"] == "evt-stale"
    assert clear_response.json()["queue_records_updated"] == 1
    assert clear_response.json()["effect_records_updated"] == 1
    assert clear_response.json()["await_agent_records_removed"] == 1
    assert after_response.status_code == 200
    assert [run["event_id"] for run in after_response.json()["runs"]] == ["evt-keep"]

    updated_queue = (runtime_dir / "trigger-orchestrator-events.json").read_text(encoding="utf-8")
    updated_effects = (runtime_dir / "effect-orchestrator-executions.json").read_text(
        encoding="utf-8"
    )
    updated_await = (runtime_dir / "await-agent-pending.json").read_text(encoding="utf-8")
    assert '"delivery_state": "delivered"' in updated_queue
    assert '"status": "failed"' in updated_effects
    assert "Manually cleared stale workflow run from editor state." in updated_effects
    assert '"gate-await:dispatch-stale"' not in updated_await


def test_admin_web_includes_errored_workflow_runs_and_can_resume_them(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "trigger-orchestrator-events.json").write_text(
        """
{
  "evt-failed": {
    "event_id": "evt-failed",
    "trigger_id": "trigger-ticket",
    "trigger_type": "trigger:ticket:created",
    "trace_id": "trace-failed",
    "occurred_at": "2026-04-08T12:00:00Z",
    "payload": {"issue_number": "77"},
    "delivery_state": "delivered",
    "inflight_at": "2026-04-08T12:00:03Z",
    "delivered_at": "2026-04-08T12:00:04Z",
    "idempotency_key": null
  }
}
""".strip(),
        encoding="utf-8",
    )
    (runtime_dir / "effect-orchestrator-executions.json").write_text(
        """
{
  "exec-failed": {
    "execution_id": "exec-failed",
    "idempotency_key": "evt-failed::effect-prompt",
    "event_id": "evt-failed",
    "trace_id": "trace-failed",
    "source_node_id": "trigger-ticket",
    "source_trigger_type": "trigger:ticket:created",
    "effect_id": "effect-prompt",
    "effect_type": "effect:agent:prompt",
    "status": "failed",
    "started_at": "2026-04-08T12:00:05Z",
    "finished_at": "2026-04-08T12:00:07Z",
    "resolved_config_hash": "abc",
    "resolved_config": {},
    "input_payload": {"issue_number": "77"},
    "outputs": {},
    "error": "guest agent crashed"
  }
}
""".strip(),
        encoding="utf-8",
    )

    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    runs_response = client.get("/api/workflow-editor/runs")
    resume_response = client.post("/api/workflow-editor/runs/evt-failed/resume")

    assert runs_response.status_code == 200
    payload = runs_response.json()
    assert payload["node_activity"]["effect-prompt"] == {"failed": 1}
    assert [run["event_id"] for run in payload["runs"]] == ["evt-failed"]
    failed_run = payload["runs"][0]
    assert failed_run["run_state"] == "errored"
    assert failed_run["failed_node_id"] == "effect-prompt"
    assert failed_run["error"] == "guest agent crashed"
    assert failed_run["resumable"] is True
    assert failed_run["active_nodes"][0]["error"] == "guest agent crashed"

    assert resume_response.status_code == 200
    assert resume_response.json()["event_id"] == "evt-failed"
    assert resume_response.json()["target_node_id"] == "effect-prompt"

    queue_payload = json.loads(
        (runtime_dir / "trigger-orchestrator-events.json").read_text(encoding="utf-8")
    )
    resume_event_id = next(
        event_id for event_id in queue_payload if event_id.startswith("evt-failed:resume:")
    )
    assert queue_payload[resume_event_id]["trigger_id"] == "trigger-ticket"
    assert queue_payload[resume_event_id]["delivery_state"] == "pending"
    assert queue_payload[resume_event_id]["payload"]["__resume_target_node_id"] == "effect-prompt"
    assert queue_payload[resume_event_id]["payload"]["__resume_source_event_id"] == "evt-failed"
    assert queue_payload[resume_event_id]["payload"]["issue_number"] == "77"


def test_admin_web_can_dismiss_errored_workflow_run(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "effect-orchestrator-executions.json").write_text(
        """
{
  "exec-failed": {
    "execution_id": "exec-failed",
    "idempotency_key": "evt-failed::effect-prompt",
    "event_id": "evt-failed",
    "trace_id": "trace-failed",
    "source_node_id": "trigger-ticket",
    "source_trigger_type": "trigger:ticket:created",
    "effect_id": "effect-prompt",
    "effect_type": "effect:agent:prompt",
    "status": "failed",
    "started_at": "2026-04-08T12:00:05Z",
    "finished_at": "2026-04-08T12:00:07Z",
    "resolved_config_hash": "abc",
    "resolved_config": {},
    "input_payload": {"issue_number": "77"},
    "outputs": {},
    "error": "guest agent crashed"
  }
}
""".strip(),
        encoding="utf-8",
    )

    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    before_response = client.get("/api/workflow-editor/runs")
    dismiss_response = client.delete("/api/workflow-editor/runs/evt-failed")
    after_response = client.get("/api/workflow-editor/runs")

    assert before_response.status_code == 200
    assert [run["event_id"] for run in before_response.json()["runs"]] == ["evt-failed"]
    assert dismiss_response.status_code == 200
    assert dismiss_response.json()["effect_records_updated"] == 1
    assert after_response.status_code == 200
    assert after_response.json()["runs"] == []

    updated_effects = (runtime_dir / "effect-orchestrator-executions.json").read_text(
        encoding="utf-8"
    )
    assert '"dismissed_at"' in updated_effects
    assert '"dismiss_reason": "manual_dismiss_errored_workflow_run"' in updated_effects


def test_admin_web_workflow_runs_resolve_relative_paths_independent_of_cwd(
    tmp_path: Path, monkeypatch
) -> None:
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
    assert outputs["message"] == {"type": "string"}
    assert outputs["session_id"] == {"type": "string"}
    assert outputs["vm_name"] == {"type": "string"}


def test_admin_web_load_normalizes_await_agent_outputs_without_completion_trigger(
    tmp_path: Path,
) -> None:
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


def test_admin_web_load_normalizes_for_gate_outputs(tmp_path: Path) -> None:
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
      "id": "gate-for-1",
      "display_name": "For",
      "type": "gate:for",
      "items": ["a", "b"],
      "outputs": {}
    }
  ],
  "effects": [
    {
      "id": "effect-body-1",
      "display_name": "Body",
      "type": "effect:vm:exec",
      "target": "session-1",
      "command": "echo body"
    },
    {
      "id": "effect-done-1",
      "display_name": "Done",
      "type": "effect:vm:exec",
      "target": "session-1",
      "command": "echo done"
    }
  ],
  "queries": [],
  "edges": [
    { "source_id": "trigger-cron-1", "target_id": "gate-for-1" },
    { "source_id": "gate-for-1", "source_port": "body", "target_id": "effect-body-1" },
    { "source_id": "gate-for-1", "source_port": "done", "target_id": "effect-done-1" },
    { "source_id": "effect-body-1", "target_id": "gate-for-1", "allow_cycle": true }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    response = client.get("/api/workflow-editor/document")

    assert response.status_code == 200
    gate = response.json()["config"]["gates"][0]
    assert gate["outputs"]["current_index"] == {"type": "integer"}
    assert gate["outputs"]["item_count"] == {"type": "integer"}


def test_admin_web_session_endpoint_reports_auth_disabled_by_default(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["auth_mode"] == "none"
    assert response.json()["auth_required"] is False
    assert response.json()["authenticated"] is True


def test_admin_web_requires_login_when_password_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    unauthorized = client.get("/api/workflow-editor/document")
    login = client.post("/api/auth/login", json={"username": "operator", "password": "swordfish"})
    authorized = client.get("/api/workflow-editor/document")

    assert unauthorized.status_code == 401
    assert login.status_code == 200
    assert login.json()["auth_mode"] == "local_password"
    assert login.json()["authenticated"] is True
    assert authorized.status_code == 200


def test_admin_web_config_defaults_to_none_without_password(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", raising=False)

    config = AdminWebConfig.from_env()

    assert config.auth_mode == "none"
    assert config.auth_required is False


def test_admin_web_config_maps_password_only_deployments_to_local_password(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", raising=False)
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "swordfish")

    config = AdminWebConfig.from_env()

    assert config.auth_mode == "local_password"
    assert config.auth_required is True


def test_admin_web_config_rejects_incomplete_azure_sso(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", raising=False)

    with pytest.raises(ValueError, match="Azure SSO admin auth is incomplete"):
        AdminWebConfig.from_env()


def test_admin_web_session_payload_reports_local_password_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")

    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["auth_mode"] == "local_password"
    assert response.json()["authenticated"] is False
    assert response.json()["available_login_methods"] == ["password"]


def test_admin_web_session_payload_reports_azure_sso_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")

    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["auth_mode"] == "azure_sso"
    assert response.json()["authenticated"] is False
    assert response.json()["available_login_methods"] == ["azure_sso"]
    assert response.json()["azure_sso_login_url"] == "/api/auth/azure-sso/start"


def test_admin_web_azure_mode_requires_login(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")

    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    response = client.get("/api/workflow-editor/document")

    assert response.status_code == 401


def test_admin_web_azure_start_login_redirects_and_sets_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")

    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    response = client.get("/api/auth/azure-sso/start?next=%2Fworkflow", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith(
        "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/authorize?"
    )
    assert "agent_sandbox_admin_oidc=" in response.headers.get("set-cookie", "")


def test_admin_web_azure_callback_rejects_bad_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    start = client.get("/api/auth/azure-sso/start", follow_redirects=False)
    assert start.status_code == 307

    response = client.get(
        "/api/auth/azure-sso/callback?code=test-code&state=wrong-state", follow_redirects=False
    )

    assert response.status_code == 401
    assert response.json()["error"] == "Invalid Azure sign-in state"


def test_admin_web_azure_callback_rejects_wrong_tenant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    app = create_app(AdminService(write_config(tmp_path)))
    client = TestClient(app)

    async def fake_exchange(*, code: str, code_verifier: str) -> dict[str, str]:
        return {"id_token": "stub-token"}

    async def fake_validate(id_token: str) -> dict[str, str]:
        return {
            "sub": "subject-1",
            "name": "Operator",
            "preferred_username": "operator@example.com",
            "tid": "wrong-tenant",
        }

    app.state.auth._exchange_azure_code_for_token = fake_exchange  # type: ignore[method-assign]
    app.state.auth._validate_azure_id_token = fake_validate  # type: ignore[method-assign]

    start = client.get("/api/auth/azure-sso/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&", 1)[0]

    response = client.get(
        f"/api/auth/azure-sso/callback?code=test-code&state={state}", follow_redirects=False
    )

    assert response.status_code == 401
    assert response.json()["error"] == "Azure sign-in tenant is not allowed"


def test_admin_web_azure_callback_rejects_non_allowlisted_user(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    app = create_app(AdminService(write_config(tmp_path)))
    client = TestClient(app)

    async def fake_exchange(*, code: str, code_verifier: str) -> dict[str, str]:
        return {"id_token": "stub-token"}

    async def fake_validate(id_token: str) -> dict[str, str]:
        return {
            "sub": "subject-1",
            "name": "Other User",
            "preferred_username": "other@example.com",
            "tid": "tenant-123",
        }

    app.state.auth._exchange_azure_code_for_token = fake_exchange  # type: ignore[method-assign]
    app.state.auth._validate_azure_id_token = fake_validate  # type: ignore[method-assign]

    start = client.get("/api/auth/azure-sso/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&", 1)[0]

    response = client.get(
        f"/api/auth/azure-sso/callback?code=test-code&state={state}", follow_redirects=False
    )

    assert response.status_code == 401
    assert response.json()["error"] == "Azure sign-in user is not allowed"


def test_admin_web_azure_callback_accepts_allowlisted_user_and_sets_local_session(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT", "/#workflow")
    app = create_app(AdminService(write_config(tmp_path)))
    client = TestClient(app)

    async def fake_exchange(*, code: str, code_verifier: str) -> dict[str, str]:
        return {"id_token": "stub-token"}

    async def fake_validate(id_token: str) -> dict[str, str]:
        return {
            "sub": "subject-1",
            "name": "Operator",
            "preferred_username": "operator@example.com",
            "tid": "tenant-123",
            "oid": "object-1",
            "iss": "https://login.microsoftonline.com/tenant-123/v2.0",
        }

    app.state.auth._exchange_azure_code_for_token = fake_exchange  # type: ignore[method-assign]
    app.state.auth._validate_azure_id_token = fake_validate  # type: ignore[method-assign]

    start = client.get("/api/auth/azure-sso/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&", 1)[0]
    callback = client.get(
        f"/api/auth/azure-sso/callback?code=test-code&state={state}", follow_redirects=False
    )
    session = client.get("/api/auth/session")

    assert callback.status_code == 303
    assert callback.headers["location"] == "/#workflow"
    assert "agent_sandbox_admin_session=" in callback.headers.get("set-cookie", "")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["auth_mode"] == "azure_sso"
    assert session.json()["username"] == "operator@example.com"
    assert session.json()["display_name"] == "Operator"


def test_admin_web_logout_clears_azure_local_session_cookie(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    app = create_app(AdminService(write_config(tmp_path)))
    client = TestClient(app)

    async def fake_exchange(*, code: str, code_verifier: str) -> dict[str, str]:
        return {"id_token": "stub-token"}

    async def fake_validate(id_token: str) -> dict[str, str]:
        return {
            "sub": "subject-1",
            "name": "Operator",
            "preferred_username": "operator@example.com",
            "tid": "tenant-123",
            "oid": "object-1",
            "iss": "https://login.microsoftonline.com/tenant-123/v2.0",
        }

    app.state.auth._exchange_azure_code_for_token = fake_exchange  # type: ignore[method-assign]
    app.state.auth._validate_azure_id_token = fake_validate  # type: ignore[method-assign]

    start = client.get("/api/auth/azure-sso/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&", 1)[0]
    client.get(f"/api/auth/azure-sso/callback?code=test-code&state={state}", follow_redirects=False)

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["auth_mode"] == "azure_sso"


def test_admin_web_exposes_admin_domain_smoke_endpoints(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    responses = {
        "agents": client.get("/api/admin/agents"),
        "vms": client.get("/api/admin/vms"),
        "issues": client.get("/api/admin/issues"),
        "approvals": client.get("/api/admin/approvals"),
        "sessions": client.get("/api/admin/sessions"),
        "workers": client.get("/api/workers"),
        "configuration": client.get("/api/configuration/state"),
    }

    assert responses["agents"].status_code == 200
    assert isinstance(responses["agents"].json()["items"], list)
    assert responses["vms"].status_code == 200
    assert isinstance(responses["vms"].json()["items"], list)
    assert responses["issues"].status_code == 200
    assert isinstance(responses["issues"].json()["items"], list)
    assert responses["approvals"].status_code == 200
    assert isinstance(responses["approvals"].json()["items"], list)
    assert responses["sessions"].status_code == 200
    assert isinstance(responses["sessions"].json()["items"], list)
    assert responses["workers"].status_code == 200
    assert "workers" in responses["workers"].json()
    assert responses["configuration"].status_code == 200
    assert responses["configuration"].json()["selected_directory_path"] == "root"


def test_admin_web_workers_expose_config_schema(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    workers_response = client.get("/api/workers")
    worker_id = workers_response.json()["workers"][0]["worker_id"]
    detail_response = client.get(f"/api/workers/{worker_id}")

    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["config"]["worker_id"] == worker_id
    assert payload["config_schema"]["worker_id"] == worker_id
    assert any(field["field_name"] == "runtime_dir" for field in payload["config_schema"]["fields"])


def test_admin_web_configuration_state_exposes_grouped_tabs(tmp_path: Path) -> None:
    client = TestClient(
        create_app(AdminService(write_config(tmp_path, enable_directory_scoped_config=True)))
    )

    response = client.get("/api/configuration/state")

    assert response.status_code == 200
    payload = response.json()
    communications = next(tab for tab in payload["tabs"] if tab["key"] == "communications")
    assert communications["title"] == "Communications"
    assert any(section["title"] == "Discord" for section in communications["sections"])
    assert "communications" in payload["tab_snapshots"]
    assert payload["selected_directory"]["path"] == "root"


def test_admin_web_configuration_tab_save_splits_underlying_domains(tmp_path: Path) -> None:
    client = TestClient(
        create_app(AdminService(write_config(tmp_path, enable_directory_scoped_config=True)))
    )

    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200

    response = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={
            "config": {
                "discord_backend": "stub",
                "discord_operator_channel_allowlist": ["chan-1", "chan-2"],
                "discord_resume_prompt": "Resume this session.",
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["write_result"]["ok"] is True
    snapshot = payload["tab_snapshot"]
    assert snapshot["raw"]["config"]["discord_backend"] == "stub"
    assert snapshot["raw"]["config"]["discord_operator_channel_allowlist"] == ["chan-1", "chan-2"]
    assert snapshot["raw"]["config"]["discord_resume_prompt"] == "Resume this session."


def test_admin_web_grouped_tab_save_preserves_unrelated_shared_domain_fields(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))

    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200

    provider_response = client.put(
        "/api/configuration/domains/providers?directory_path=ops",
        json={"config": {"openai_api_base_url": "http://relay.internal/v1"}},
    )
    assert provider_response.status_code == 200

    communications_response = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={"config": {"discord_backend": "stub"}},
    )
    assert communications_response.status_code == 200

    providers_result = service.read_directory_domain_config("ops", "providers")
    assert providers_result.ok is True
    assert providers_result.data is not None
    assert providers_result.data.config == {
        "discord_backend": "stub",
        "openai_api_base_url": "http://relay.internal/v1",
    }

    clear_response = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={"config": {}},
    )
    assert clear_response.status_code == 200

    providers_result = service.read_directory_domain_config("ops", "providers")
    assert providers_result.ok is True
    assert providers_result.data is not None
    assert providers_result.data.config == {"openai_api_base_url": "http://relay.internal/v1"}


def test_admin_web_can_toggle_privileged_operations_state(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    state = {"unlocked": False}

    def unlock(self):
        state["unlocked"] = True
        return "Privileged operations unlocked."

    def lock(self):
        state["unlocked"] = False

    def unlocked(self):
        return state["unlocked"]

    monkeypatch.setattr(service, "unlock_privileged_operations", MethodType(unlock, service))
    monkeypatch.setattr(service, "lock_privileged_operations", MethodType(lock, service))
    monkeypatch.setattr(service, "privileged_operations_unlocked", MethodType(unlocked, service))

    client = TestClient(create_app(service))

    locked = client.get("/api/admin/state")
    unlocked_response = client.post("/api/admin/privileged/unlock")
    unlocked_state = client.get("/api/admin/state")
    relocked_response = client.post("/api/admin/privileged/lock")

    assert locked.status_code == 200
    assert locked.json()["privileged_operations"]["unlocked"] is False
    assert unlocked_response.status_code == 200
    assert unlocked_response.json()["privileged_operations"]["unlocked"] is True
    assert unlocked_state.json()["privileged_operations"]["unlocked"] is True
    assert relocked_response.status_code == 200
    assert relocked_response.json()["privileged_operations"]["unlocked"] is False


def test_admin_web_unlocks_without_starting_helper_when_helper_is_not_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = AdminService(write_config(tmp_path))

    def fail_unlock(self):
        raise AssertionError("web unlock should not require the privileged helper when disabled")

    monkeypatch.setattr(service, "privileged_operations_unlocked", MethodType(fail_unlock, service))

    client = TestClient(create_app(service))

    initial_state = client.get("/api/admin/state")
    unlocked_response = client.post("/api/admin/privileged/unlock")
    unlocked_state = client.get("/api/admin/state")
    relocked_response = client.post("/api/admin/privileged/lock")
    relocked_state = client.get("/api/admin/state")

    assert initial_state.status_code == 200
    assert initial_state.json()["privileged_operations"]["unlocked"] is False
    assert unlocked_response.status_code == 200
    assert unlocked_response.json()["privileged_operations"]["unlocked"] is True
    assert unlocked_state.json()["privileged_operations"]["unlocked"] is True
    assert relocked_response.status_code == 200
    assert relocked_response.json()["privileged_operations"]["unlocked"] is False
    assert relocked_state.json()["privileged_operations"]["unlocked"] is False


def test_admin_web_rejects_start_when_vm_session_model_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))

    def list_vms(self, **_kwargs):
        return [
            VmSummary(
                vm_name="vm-1",
                issue_number="ISSUE-1",
                session_ids=["session-1"],
                directory_paths=["root/team-a"],
                libvirt_state="stopped",
                agent_state="idle",
            )
        ]

    def list_sessions(self, **_kwargs):
        return [
            SessionSummary(
                session_id="session-1",
                vm_id="vm-1",
                model=None,
                directory_path="root/team-a",
                allowed_tools=[],
                expires_at="2030-01-01T00:00:00+00:00",
            )
        ]

    def compute_effective(self, directory_path: str, domain: str):
        assert directory_path == "root/team-a"
        assert domain == "issue_setup"
        return SimpleNamespace(
            ok=True,
            data=SimpleNamespace(resolution=SimpleNamespace(effective_config={})),
        )

    def start_vm(self, _vm_name: str):
        raise AssertionError("start_vm should not be called when validation fails")

    monkeypatch.setattr(service, "list_vms", MethodType(list_vms, service))
    monkeypatch.setattr(service, "list_sessions", MethodType(list_sessions, service))
    monkeypatch.setattr(
        service, "compute_directory_effective_config", MethodType(compute_effective, service)
    )
    monkeypatch.setattr(service, "start_vm", MethodType(start_vm, service))

    client = TestClient(create_app(service))

    response = client.post(
        "/api/admin/vms/vm-1/actions/start?directory_path=root/team-a&direct_only=true", json={}
    )

    assert response.status_code == 400
    assert "Cannot start/resume agent" in response.json()["error"]


def test_admin_web_agents_list_includes_vms_and_container_executions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.create_directory_child("root", "team-a")
    service.create_directory_child("root", "team-b")
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-a",
            session_id="container-session-a",
            worker_job_id="job-a",
            worker_url="http://worker.test",
            target_name="target-a",
            last_status="running",
            directory_path="root/team-a",
            metadata={"image": "runner:latest"},
        )
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-b",
            session_id="container-session-b",
            worker_job_id="job-b",
            worker_url="http://worker.test",
            target_name="target-b",
            last_status="running",
            directory_path="root/team-b",
        )
    )
    list_vms_calls: list[dict[str, object]] = []

    def list_vms(self, **kwargs):
        list_vms_calls.append(kwargs)
        if kwargs["directory_path"] != "root/team-a":
            return []
        return [
            VmSummary(
                vm_name="vm-a",
                issue_number="1",
                session_ids=["vm-session-a"],
                directory_paths=["root/team-a"],
                libvirt_state="running",
                agent_state="idle",
            )
        ]

    monkeypatch.setattr(service, "list_vms", MethodType(list_vms, service))

    client = TestClient(create_app(service))
    response = client.get(
        "/api/admin/agents?directory_path=root/team-a&direct_only=true&include_inactive=true"
    )

    assert response.status_code == 200
    payload = response.json()
    assert {item["backend"] for item in payload["items"]} == {
        "libvirt_vm",
        "container_worker_vm",
    }
    assert {item["agent_id"] for item in payload["items"]} == {"vm-a", "exec-a"}
    assert payload["items"][0]["capabilities"]["logs"] is True
    assert list_vms_calls[0]["directory_path"] == "root/team-a"
    assert list_vms_calls[0]["direct_only"] is True

    root_response = client.get(
        "/api/admin/agents?directory_path=root&direct_only=true&include_inactive=true"
    )

    assert root_response.status_code == 200
    assert root_response.json()["items"] == []


def test_admin_web_container_agent_log_and_actions_do_not_require_vm_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = AdminService(write_config(tmp_path))
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="session-1",
            worker_job_id="job-1",
            worker_url="http://worker.test",
            target_name="target-1",
            last_status="running",
            directory_path="root",
        )
    )

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.inspected: list[str] = []
            self.logged: list[tuple[str, int]] = []
            self.stopped: list[str] = []
            self.destroyed: list[str] = []

        def inspect(self, target: str) -> ExecutionStatus:
            self.inspected.append(target)
            return ExecutionStatus(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                execution_id=target,
                target_name="target-1",
                status=ExecutionStatusKind.RUNNING,
                session_id="session-1",
            )

        def get_log_snapshot(self, target: str, *, lines: int = 50) -> str:
            self.logged.append((target, lines))
            return "line 2\n"

        def stop(self, target: str) -> ExecutionStatus:
            self.stopped.append(target)
            return ExecutionStatus(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                execution_id=target,
                target_name="target-1",
                status=ExecutionStatusKind.STOPPED,
                session_id="session-1",
                message="stopped",
            )

        def destroy(self, target: str) -> ExecutionStatus:
            self.destroyed.append(target)
            return ExecutionStatus(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                execution_id=target,
                target_name="target-1",
                status=ExecutionStatusKind.DESTROYED,
                session_id="session-1",
                message="destroyed",
            )

    fake_backend = FakeContainerBackend()

    def fail_list_vms(self, **_kwargs):
        raise AssertionError("container routes should not require VM summaries")

    monkeypatch.setattr(service, "list_vms", MethodType(fail_list_vms, service))
    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        MethodType(lambda self: fake_backend, service),
    )

    client = TestClient(create_app(service))

    log_response = client.get(
        "/api/admin/agents/exec-1/log?backend=container_worker_vm&lines=2"
    )
    stop_response = client.post(
        "/api/admin/agents/exec-1/actions/stop?backend=container_worker_vm", json={}
    )
    destroy_response = client.post(
        "/api/admin/agents/exec-1/actions/destroy?backend=container_worker_vm", json={}
    )

    assert log_response.status_code == 200
    assert log_response.json()["text"] == "line 2\n"
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "stopped"
    assert destroy_response.status_code == 200
    assert destroy_response.json()["status"] == "destroyed"
    assert fake_backend.inspected == ["exec-1"]
    assert fake_backend.logged == [("exec-1", 2)]
    assert fake_backend.stopped == ["exec-1"]
    assert fake_backend.destroyed == ["exec-1"]


def test_admin_web_vm_agent_action_uses_existing_vm_path(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    called: list[str] = []

    def start_vm(self, target: str) -> VmActionResult:
        called.append(target)
        return VmActionResult(
            success=True,
            vm_name=target,
            action="start",
            message="started",
        )

    monkeypatch.setattr(service, "start_vm", MethodType(start_vm, service))

    client = TestClient(create_app(service))
    response = client.post(
        "/api/admin/agents/vm-1/actions/start?backend=libvirt_vm", json={}
    )

    assert response.status_code == 200
    assert response.json()["backend"] == "libvirt_vm"
    assert called == ["vm-1"]


def test_admin_web_issue_detail_preserves_local_editability(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    created_issue = service.create_local_issue(
        title="Editable issue",
        body="body",
        directory_path="root/team-a",
    )
    client = TestClient(create_app(service))

    response = client.get(f"/api/admin/issues/{created_issue.issue_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["issue_id"] == created_issue.issue_id
    assert payload["source_type"] == "local_manual"
    assert payload["detail_source_type"] == "local"
    assert payload["can_edit"] is True
    assert payload["can_delete"] is True


def test_admin_web_reads_issue_yaml_by_canonical_issue_id(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    created_issue = service.create_local_issue(
        title="YAML issue",
        body="body",
        directory_path="root/team-a",
    )
    client = TestClient(create_app(service))

    response = client.get(f"/api/admin/issues/{created_issue.issue_id}/yaml")

    assert response.status_code == 200
    payload = response.json()
    assert payload["issue_id"] == created_issue.issue_id
    assert payload["issue_number"] == created_issue.issue_number
    assert "title:" in payload["yaml_text"]


def test_admin_web_reads_issue_detail_for_scoped_issue_id_with_slash(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    yaml_path = tmp_path / "team-issue.yaml"
    yaml_path.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")
    issue = LocalIssueRecord(
        issue_id="root/team::local::7",
        issue_number="7",
        title="Team issue",
        body="Team body",
        yaml_path=str(yaml_path),
        directory_path="root/team",
    )
    service.local_issue_store.put(issue.issue_id, issue)
    client = TestClient(create_app(service))

    response = client.get(f"/api/admin/issues/{issue.issue_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["issue_id"] == issue.issue_id
    assert payload["directory_path"] == "root/team"


def test_admin_web_reads_issue_yaml_for_scoped_issue_id_with_slash(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    yaml_path = tmp_path / "team-issue.yaml"
    yaml_path.write_text("issue_number: 9\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")
    issue = LocalIssueRecord(
        issue_id="root/team::local::9",
        issue_number="9",
        title="Team issue",
        body="Team body",
        yaml_path=str(yaml_path),
        directory_path="root/team",
    )
    service.local_issue_store.put(issue.issue_id, issue)
    client = TestClient(create_app(service))

    response = client.get(f"/api/admin/issues/{issue.issue_id}/yaml")

    assert response.status_code == 200
    payload = response.json()
    assert payload["issue_id"] == issue.issue_id
    assert "title:" in payload["yaml_text"]


def test_admin_web_issue_detail_uses_cached_proxy_data_without_remote_credentials(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    yaml_path = tmp_path / "proxy-issue.yaml"
    yaml_path.write_text(
        "issue_number: 14378\ntitle: Proxy issue\nbody: Local body\n", encoding="utf-8"
    )
    issue = LocalIssueRecord(
        issue_id="root::azure_devops::14378",
        issue_number="14378",
        title="Proxy issue",
        body="Local body",
        yaml_path=str(yaml_path),
        directory_path="root",
        source_type="remote_proxy",
        source="azure_devops",
        metadata={
            "remote": {"provider": "azure_devops", "issue_id": "14378"},
            "proxy_cache": {
                "title": "Cached remote title",
                "description": "Cached remote description",
                "state": "In Progress",
                "url": "https://example.invalid/work-items/14378",
                "comments": [{"text": "cached comment"}],
            },
        },
    )
    service.local_issue_store.put(issue.issue_id, issue)

    class _Adapter:
        provider_name = "azure_devops"

        def read_issue(self, **kwargs):
            raise AssertionError("web detail view should not fetch remote issue data")

    service.issue_service.adapters["azure_devops"] = _Adapter()
    client = TestClient(create_app(service))

    response = client.get(f"/api/admin/issues/{issue.issue_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_type"] == "remote_proxy"
    assert payload["detail_source_type"] == "proxy"
    assert payload["provider"] == "azure_devops"
    assert payload["remote_issue_id"] == "14378"
    assert payload["title"] == "Cached remote title"
    assert payload["body"] == "Cached remote description"
    assert payload["state"] == "In Progress"


def test_admin_web_creates_standalone_vm(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))

    def create_vm(self, **payload):
        assert payload["vm_name"] == "lab-vm-1"
        assert payload["session_id"] == "session-123"
        assert payload["model"] == "gpt-5.2-codex"
        assert payload["dotnet"] is True
        assert payload["nuget"] is True
        assert payload["mssql"] is True
        assert payload["mssql_init_root"] == "/tmp/init"
        assert payload["playwright"] is True
        assert payload["directory_path"] == "root/team-a"
        return VmActionResult(
            success=True,
            vm_name="lab-vm-1",
            action="create-vm",
            message="created",
        )

    monkeypatch.setattr(service, "create_vm", MethodType(create_vm, service))

    client = TestClient(create_app(service))

    response = client.post(
        "/api/admin/vms",
        json={
            "vm_name": "lab-vm-1",
            "session_id": "session-123",
            "model": "gpt-5.2-codex",
            "dotnet": True,
            "nuget": True,
            "mssql": True,
            "mssql_init_root": "/tmp/init",
            "playwright": True,
            "directory_path": "root/team-a",
        },
    )

    assert response.status_code == 201
    assert response.json()["vm_name"] == "lab-vm-1"


def test_admin_web_create_issue_can_start_vm_immediately(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    created_issue = service.create_local_issue(
        title="Draft fix",
        body="body",
        directory_path="root/team-a",
    )

    def create_local_issue(self, **_payload):
        return created_issue

    def create_issue_vm(self, issue_identifier: str, **payload):
        assert issue_identifier == created_issue.issue_id
        assert payload["model"] == "gpt-5.2-codex"
        assert payload["dotnet"] is True
        assert payload["nuget"] == "/tmp/project.slnx"
        assert payload["playwright"] is True
        return VmActionResult(
            success=True,
            vm_name="issue-vm-1",
            action="create-issue-vm",
            message="vm created",
        )

    monkeypatch.setattr(service, "create_local_issue", MethodType(create_local_issue, service))
    monkeypatch.setattr(
        service, "create_issue_vm_from_local_issue", MethodType(create_issue_vm, service)
    )

    client = TestClient(create_app(service))

    response = client.post(
        "/api/admin/issues",
        json={
            "title": "Draft fix",
            "body": "body",
            "directory_path": "root/team-a",
            "start_vm": True,
            "model": "gpt-5.2-codex",
            "dotnet": True,
            "nuget": "/tmp/project.slnx",
            "playwright": True,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["issue"]["issue_id"] == created_issue.issue_id
    assert payload["vm_action"]["vm_name"] == "issue-vm-1"
