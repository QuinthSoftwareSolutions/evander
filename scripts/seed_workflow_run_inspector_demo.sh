#!/usr/bin/env bash
set -Eeuo pipefail

show_help() {
  cat <<'EOF'
Usage:
  seed_workflow_run_inspector_demo.sh [--runtime-dir PATH]

Seeds a deterministic workflow run inspector demo runtime with one rich run,
one legacy run, a graph snapshot, a staged communication artifact, a session
manifest, an audit log, and an approval request. The default runtime directory is
./runtime/workflow-run-inspector-demo.
EOF
}

RUNTIME_DIR="runtime/workflow-run-inspector-demo"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime-dir)
      RUNTIME_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      show_help >&2
      exit 1
      ;;
  esac
done

if [[ -z "${RUNTIME_DIR}" ]]; then
  echo "--runtime-dir requires a value." >&2
  exit 1
fi

python3 - "${RUNTIME_DIR}" <<'PY'
import json
from hashlib import sha256
from pathlib import Path
import sys


runtime_dir = Path(sys.argv[1]).expanduser()
runtime_dir.mkdir(parents=True, exist_ok=True)

artifact_id = "comm_0123456789abcdef0123456789abcdef"
session_id = "session-detail"
rich_revision = "rev-detail-rich"
artifact_content = b"hello from the run detail fixture\n"
artifact_dir = runtime_dir / "communication-artifacts"
artifact_dir.mkdir(parents=True, exist_ok=True)
tool_call_id = "toolcall-rich-01"

artifact_metadata = {
    "artifact_id": artifact_id,
    "provider": "teams",
    "source_message_id": None,
    "source_attachment_id": None,
    "source_channel": None,
    "source_mailbox": None,
    "original_filename": "notes.txt",
    "normalized_safe_filename": "notes.txt",
    "filename": "notes.txt",
    "mime_type": "text/plain",
    "byte_count": len(artifact_content),
    "sha256": sha256(artifact_content).hexdigest(),
    "created_at": "2026-05-01T10:04:00Z",
    "expires_at": "2099-01-01T00:00:00Z",
    "staged_at": "2026-05-01T10:04:00Z",
    "session_id": session_id,
    "metadata": {"storage": "host_artifact_store", "source": "fixture"},
}

session_manifest = {
    "session_id": session_id,
    "vm_id": "vm-detail",
    "vm_provider": "libvirt",
    "vm_provider_id": "vm-detail",
    "model": "gpt-5.2-codex",
    "directory_path": "root",
    "allowed_tools": ["prompt_agent", "get_agent_logs", "approval_submit"],
    "approval_required_tools": ["approval_submit"],
    "expires_at": "2099-01-01T00:00:00Z",
    "created_at": "2026-05-01T09:55:00Z",
    "metadata": {
        "labels": {"fixture": "run-detail"},
        "capabilities": {"logs": True},
    },
}

approval_request = {
    "request_id": "approval-rich",
    "session_id": session_id,
    "action_type": "effect:agent:prompt",
    "payload_digest": "digest-rich",
    "human_summary": "Prompt the agent after review.",
    "payload": {
        "request_id": "approval-rich",
        "session_id": session_id,
        "artifact_ids": [artifact_id],
    },
    "directory_path": "root",
    "tool_name": "codex",
    "status": "approved",
    "approver": "workflow-admin",
    "decision_at": "2026-05-01T10:02:00Z",
    "decision_reason": "Approved for the run detail fixture.",
    "expires_at": "2026-05-02T10:00:00Z",
    "created_at": "2026-05-01T10:00:00Z",
}

trigger_events = {
    "evt-detail": {
        "event_id": "evt-detail",
        "trigger_id": "trigger-ticket",
        "trigger_type": "trigger:ticket:created",
        "trace_id": "trace-detail",
        "occurred_at": "2026-05-01T10:00:00Z",
        "payload": {"issue_number": "99"},
        "delivery_state": "delivered",
        "inflight_at": "2026-05-01T10:00:05Z",
        "delivered_at": "2026-05-01T10:00:06Z",
        "idempotency_key": None,
    },
    "evt-legacy": {
        "event_id": "evt-legacy",
        "trigger_id": "trigger-ticket",
        "trigger_type": "trigger:ticket:created",
        "trace_id": "trace-legacy",
        "occurred_at": "2026-05-01T09:00:00Z",
        "payload": {"issue_number": "12"},
        "delivery_state": "delivered",
        "inflight_at": "2026-05-01T09:00:04Z",
        "delivered_at": "2026-05-01T09:00:05Z",
        "idempotency_key": None,
    },
}

effect_executions = {
    "exec-detail": {
        "execution_id": "exec-detail",
        "idempotency_key": "evt-detail::effect-prompt",
        "event_id": "evt-detail",
        "trace_id": "trace-detail",
        "source_node_id": "trigger-ticket",
        "source_trigger_type": "trigger:ticket:created",
        "effect_id": "effect-prompt",
        "effect_type": "effect:agent:prompt",
        "status": "failed",
        "started_at": "2026-05-01T10:00:10Z",
        "finished_at": "2026-05-01T10:00:11Z",
        "resolved_config_hash": "hash-rich",
        "resolved_config": {
            "session_id": session_id,
            "vm_name": "vm-detail",
            "prompt": "Investigate the issue",
            "dispatch_id": "dispatch-detail",
            "thread_id": "thread-detail",
            "execution_id": "execution-detail",
            "artifact_ids": [artifact_id],
            "request_id": "approval-rich",
        },
        "input_payload": {
            "issue_number": "99",
            "session_id": session_id,
            "request_id": "approval-rich",
        },
        "outputs": {
            "dispatch_id": "dispatch-detail",
            "execution_id": "execution-detail",
            "thread_id": "thread-detail",
        },
        "binding_trace": {
            "consumed_output_keys": ["trigger-ticket.issue_number"],
            "entries": [
                {
                    "config_path": "$.prompt",
                    "expression": "${{trigger-ticket.issue_number}}",
                    "reference": "trigger-ticket.issue_number",
                    "source_node_id": "trigger-ticket",
                    "output_path": "issue_number",
                    "fallback_used": False,
                    "status": "resolved",
                    "resolved_value_preview": '"99"',
                    "value_type": "str",
                    "error": None,
                }
            ],
        },
        "binding_error": None,
        "published_event_id": "evt-detail-published",
        "completion_event_enqueued_at": "2026-05-01T10:00:12Z",
        "config_revision": rich_revision,
    },
    "exec-legacy": {
        "execution_id": "exec-legacy",
        "idempotency_key": "evt-legacy::effect-prompt",
        "event_id": "evt-legacy",
        "trace_id": "trace-legacy",
        "source_node_id": "trigger-ticket",
        "source_trigger_type": "trigger:ticket:created",
        "effect_id": "effect-prompt",
        "effect_type": "effect:agent:prompt",
        "status": "failed",
        "started_at": "2026-05-01T09:00:06Z",
        "finished_at": "2026-05-01T09:00:07Z",
        "resolved_config_hash": "hash-legacy",
        "resolved_config": {},
        "input_payload": {"issue_number": "12"},
        "outputs": {},
        "error": "legacy failure",
    },
}

gate_executions = {
    "gate-detail": {
        "execution_id": "gate-detail",
        "idempotency_key": "evt-detail::gate-await",
        "event_id": "evt-detail",
        "trace_id": "trace-detail",
        "source_node_id": "effect-prompt",
        "source_trigger_type": "trigger:ticket:created",
        "gate_id": "gate-await",
        "gate_type": "gate:await-agent",
        "status": "waiting",
        "started_at": "2026-05-01T10:00:13Z",
        "finished_at": "2026-05-01T10:00:14Z",
        "resolved_config_hash": "hash-rich",
        "resolved_config": {
            "session_id": session_id,
            "vm_name": "vm-detail",
            "dispatch_id": "dispatch-detail",
            "prompt": "Investigate the issue",
        },
        "input_payload": {
            "session_id": session_id,
            "vm_name": "vm-detail",
            "dispatch_id": "dispatch-detail",
        },
        "outputs": {
            "session_id": session_id,
            "vm_name": "vm-detail",
            "dispatch_id": "dispatch-detail",
        },
        "inputs_by_source": {"effect-prompt": {"issue_number": "99"}},
        "input_event_ids_by_source": {"effect-prompt": "evt-detail"},
        "input_occurred_at_by_source": {"effect-prompt": "2026-05-01T10:00:00Z"},
        "published_event_id": "evt-detail-gate",
        "completion_event_enqueued_at": "2026-05-01T10:00:15Z",
        "config_revision": rich_revision,
    }
}

await_agent_pending = {
    "gate-await:dispatch-detail": {
        "gate_id": "gate-await",
        "dispatch_id": "dispatch-detail",
        "prompt_event_id": "evt-detail",
        "prompt_source_node_id": "effect-prompt",
        "trace_id": "trace-detail",
        "session_id": session_id,
        "vm_name": "vm-detail",
        "payload": {"artifact_ids": [artifact_id]},
        "occurred_at": "2026-05-01T10:00:16Z",
    },
    "gate-await:dispatch-legacy": {
        "gate_id": "gate-await",
        "dispatch_id": "dispatch-legacy",
        "prompt_event_id": "evt-legacy",
        "prompt_source_node_id": "effect-prompt",
        "trace_id": "trace-legacy",
        "session_id": session_id,
        "vm_name": "vm-legacy",
        "payload": {},
        "occurred_at": "2026-05-01T09:00:16Z",
    },
}

workflow_config_snapshot = {
    rich_revision: {
        "revision": rich_revision,
        "captured_at": "2026-05-01T10:00:30Z",
        "snapshot_type": "compiled_runtime_graph",
        "source": "effect_orchestrator.run_once",
        "node_count": 3,
        "edge_count": 2,
        "triggers": [
            {
                "id": "trigger-ticket",
                "type": "trigger:ticket:created",
                "display_name": "Ticket Created",
            }
        ],
        "gates": [
            {
                "id": "gate-await",
                "type": "gate:await-agent",
                "display_name": "Await Agent",
            }
        ],
        "effects": [
            {
                "id": "effect-prompt",
                "type": "effect:agent:prompt",
                "display_name": "Prompt Agent",
            }
        ],
        "queries": [],
        "edges": [
            {
                "id": "edge-1",
                "source": "trigger-ticket",
                "target": "effect-prompt",
            },
            {
                "id": "edge-2",
                "source": "effect-prompt",
                "target": "gate-await",
            },
        ],
        "workflow_graphs": [
            {
                "id": "graph-detail",
                "name": "Detail Graph",
                "description": "Workflow run detail fixture",
                "directory_path": "root",
                "user_ids": ["operator"],
                "node_ids": ["trigger-ticket", "effect-prompt", "gate-await"],
            }
        ],
    }
}

def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in entries) + "\n",
        encoding="utf-8",
    )


audit_entries = [
    {
        "event_type": "tool_call",
        "tool_call_id": tool_call_id,
        "session_id": session_id,
        "vm_id": "vm-detail",
        "directory_path": "root",
        "agent_role": "automation",
        "actor_user_id": "workflow-admin",
        "tool_name": "prompt_agent",
        "request_id": "tool-request-detail",
        "correlation_id": "tool-correlation-detail",
        "approval_request_id": "approval-rich",
        "workflow_run_id": "evt-detail",
        "trace_id": "trace-detail",
        "duration_ms": 42,
        "decision": "allowed",
        "args": {
            "approval_request_id": "approval-rich",
            "correlation_id": "tool-correlation-detail",
            "prompt": "Investigate the issue",
            "request_id": "tool-request-detail",
            "session_id": session_id,
            "token": "super-secret",
            "trace_id": "trace-detail",
            "vm_name": "vm-detail",
            "workflow_run_id": "evt-detail",
            "nested": {"password": "nested-secret"},
        },
        "result": {
            "approval_request_id": "approval-rich",
            "correlation_id": "tool-correlation-detail",
            "dispatch_id": "dispatch-detail",
            "execution_id": "execution-detail",
            "request_id": "tool-request-detail",
            "session_id": session_id,
            "status": "success",
            "thread_id": "thread-detail",
            "trace_id": "trace-detail",
            "workflow_run_id": "evt-detail",
            "secret": "result-secret",
        },
        "created_at": "2026-05-01T10:00:09+00:00",
    },
    {
        "event_type": "approval_decision",
        "session_id": session_id,
        "created_at": "2026-05-01T10:05:00+00:00",
    },
]


write_json(runtime_dir / "approvals.json", {approval_request["request_id"]: approval_request})
write_json(runtime_dir / "sessions.json", {session_manifest["session_id"]: session_manifest})
write_json(runtime_dir / "trigger-orchestrator-events.json", trigger_events)
write_json(runtime_dir / "effect-orchestrator-executions.json", effect_executions)
write_json(runtime_dir / "gate-orchestrator-executions.json", gate_executions)
write_json(runtime_dir / "await-agent-pending.json", await_agent_pending)
write_json(runtime_dir / "workflow-config-snapshots.json", workflow_config_snapshot)
write_jsonl(runtime_dir / "audit.jsonl", audit_entries)
(artifact_dir / f"{artifact_id}.json").write_text(
    json.dumps(artifact_metadata, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
(artifact_dir / f"{artifact_id}.bin").write_bytes(artifact_content)

print(f"Seeded workflow run inspector demo runtime at {runtime_dir}")
PY
