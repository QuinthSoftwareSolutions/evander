from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.daemons.effect_orchestrator import EffectOrchestratorWorker
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.effects.providers.base import EffectProviderBase
from agent_sandbox.effects.registry import EffectProviderFactory, EffectProviderRegistry
from agent_sandbox.events.models import EffectDefinitionDiscriminated, TriggerEvent
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.triggers.queue import TriggerEventQueue

REPORT_ENV_VAR = "AGENT_SANDBOX_EFFECT_CONFORMANCE_REPORT"


@pytest.fixture(scope="module")
def report_collector() -> dict[str, Any]:
    return {"requirements": []}


def _record_result(
    report: dict[str, Any],
    *,
    requirement_id: str,
    status: str,
    coverage: list[str],
    details: str,
) -> None:
    report["requirements"].append(
        {
            "requirement_id": requirement_id,
            "status": status,
            "coverage": coverage,
            "details": details,
        }
    )


def _effect_adapter() -> TypeAdapter[EffectDefinitionDiscriminated]:
    return TypeAdapter(EffectDefinitionDiscriminated)


class _OutputProvider:
    def __init__(self, outputs: dict[str, object]) -> None:
        self.outputs = outputs

    def execute(self, *, resolved_config=None):
        return dict(self.outputs)


class _FailingProvider:
    def execute(self, *, resolved_config=None):
        raise RuntimeError("provider boom")


class _Factory:
    def __init__(self, outputs_by_effect_id: dict[str, dict[str, object]]) -> None:
        self.outputs_by_effect_id = outputs_by_effect_id

    def build(self, effect):
        return _OutputProvider(self.outputs_by_effect_id.get(effect.id, {}))


def _edge(source_id: str, target_id: str) -> dict[str, str]:
    return {"source_id": source_id, "target_id": target_id}


def test_effect_schema_validation_accepts_supported_types(report_collector: dict[str, Any]) -> None:
    adapter = _effect_adapter()
    vm = adapter.validate_python(
        {
            "id": "effect-vm",
            "display_name": "Create VM",
            "type": "effect:vm:create",
            "vm_name": "issue-${{trigger_1.ticket_number}}",
            "labels": {"workflow": "issue-worker"},
        }
    )
    vm_start = adapter.validate_python(
        {
            "id": "effect-start",
            "display_name": "Start",
            "type": "effect:vm:start",
            "session_id": "session-1",
        }
    )
    vm_stop = adapter.validate_python(
        {
            "id": "effect-stop",
            "display_name": "Stop",
            "type": "effect:vm:stop",
            "vm_name": "vm-1",
            "timeout_seconds": 90,
        }
    )
    agent_create = adapter.validate_python(
        {
            "id": "effect-agent-create",
            "display_name": "Create agent",
            "type": "effect:agent:create",
            "session_id": "session-1",
            "existing_agent_behavior": "noop",
            "teams_channel_allowlist": ["team-a/channel-1", "chat:chat-1"],
            "teams_chat_allowlist": ["chat:chat-2"],
            "communication_file_receive_enabled": True,
            "communication_file_send_enabled": True,
            "communication_file_max_bytes": 4096,
            "communication_file_require_approval_for_send": True,
            "container_image": "runner-base:latest",
            "npm_registry_url": "http://facade.test:11881/",
            "metadata": {"workflow": {"state": "active"}},
        }
    )
    agent_create_chat_only = adapter.validate_python(
        {
            "id": "effect-agent-create-chat",
            "display_name": "Create agent chat",
            "type": "effect:agent:create",
            "session_id": "session-1",
            "teams_chat_allowlist": ["chat:chat-3"],
        }
    )
    agent_destroy = adapter.validate_python(
        {
            "id": "effect-agent-destroy",
            "display_name": "Destroy agent",
            "type": "effect:agent:destroy",
            "session_id": "session-1",
        }
    )
    agent_clone = adapter.validate_python(
        {
            "id": "effect-agent-clone",
            "display_name": "Clone agent repo",
            "type": "effect:agent:clone-repo",
            "session_id": "session-1",
            "repo_source": "/tmp/repo",
        }
    )
    agent_frontend = adapter.validate_python(
        {
            "id": "effect-agent-frontend",
            "display_name": "Bootstrap frontend",
            "type": "effect:agent:bootstrap-frontend",
            "session_id": "session-1",
            "host_checkout": "/tmp/repo",
            "install_roots": ["/tmp/repo/frontend"],
        }
    )
    agent_copy = adapter.validate_python(
        {
            "id": "effect-agent-copy",
            "display_name": "Copy agent files",
            "type": "effect:agent:copy-files",
            "session_id": "session-1",
            "files": [{"destination_path": "/home/agent/repos/repo/task.yaml", "content": "task"}],
        }
    )
    prompt = adapter.validate_python(
        {
            "id": "effect-prompt",
            "display_name": "Prompt",
            "type": "effect:agent:prompt",
            "prompt": "Continue ${{effect_1.session_id}}",
        }
    )
    enqueue_prompt = adapter.validate_python(
        {
            "id": "effect-enqueue-prompt",
            "display_name": "Enqueue prompt",
            "type": "effect:agent:enqueue-prompt",
            "prompt": "Continue ${{effect_1.session_id}}",
        }
    )
    agent_exec = adapter.validate_python(
        {
            "id": "effect-agent-exec",
            "display_name": "Agent exec",
            "type": "effect:agent:exec",
            "session_id": "session-1",
            "command": ["pwd"],
        }
    )
    bash_script = adapter.validate_python(
        {
            "id": "effect-script",
            "display_name": "Run script",
            "type": "effect:bash:script",
            "script_path": "./scripts/example.sh",
            "args": ["--flag", "value"],
        }
    )
    http_request = adapter.validate_python(
        {
            "id": "effect-http",
            "display_name": "HTTP request",
            "type": "effect:http:request",
            "method": "post",
            "url": "https://example.test/api",
            "allowed_url_patterns": ["https://example.test/*"],
            "headers": {"Accept": "application/json"},
            "payload": {"message": "hello"},
        }
    )
    diagnostics_snapshot = adapter.validate_python(
        {
            "id": "effect-runtime-diagnostics",
            "display_name": "Runtime diagnostics snapshot",
            "type": "effect:runtime:diagnostics-snapshot",
            "since": "24 hours ago",
            "units": ["agent-sandbox-effect-orchestrator.service"],
        }
    )
    vm_exec = adapter.validate_python(
        {
            "id": "effect-exec",
            "display_name": "Exec",
            "type": "effect:vm:exec",
            "vm_name": "vm-1",
            "command": ["pwd"],
        }
    )
    vm_copy = adapter.validate_python(
        {
            "id": "effect-copy",
            "display_name": "Copy",
            "type": "effect:vm:copy-files",
            "vm_name": "vm-1",
            "files": [
                {
                    "destination_path": "/tmp/example.txt",
                    "content": "hello",
                }
            ],
        }
    )
    issue_state = adapter.validate_python(
        {
            "id": "effect-issue-state",
            "display_name": "Update issue state",
            "type": "effect:issue:update-state",
            "issue_id": "14084",
            "state": "In Progress",
        }
    )
    ado_pipeline = adapter.validate_python(
        {
            "id": "effect-ado-pipeline",
            "display_name": "ADO pipeline run",
            "type": "effect:ado:pipeline-run",
            "directory_path": "root/team",
            "pipeline_id": 42,
            "variables": {"resourceBaseName": "demo7"},
        }
    )
    promote = adapter.validate_python(
        {
            "id": "effect-promote",
            "display_name": "Promote branch",
            "type": "effect:git:promote-agent-branch",
            "host_repo_path": "/repo",
            "bare_repo_path": "/git-remotes/repo.git",
            "agent_branch": "agents/session-1/topic",
        }
    )
    pr_create = adapter.validate_python(
        {
            "id": "effect-pr",
            "display_name": "Create PR",
            "type": "effect:pr:create",
            "host_repo_path": "/repo",
        }
    )
    communication = adapter.validate_python(
        {
            "id": "effect-send-message",
            "display_name": "Send message",
            "type": "effect:communication:send-message",
            "communication_target_id": "teams-primary",
            "message": "Done",
        }
    )
    notification = adapter.validate_python(
        {
            "id": "effect-notify",
            "display_name": "Notify",
            "type": "effect:notification:send",
            "communication_target_id": "teams-primary",
            "message": "Done",
        }
    )
    session_renew = adapter.validate_python(
        {
            "id": "effect-session-renew",
            "display_name": "Renew expired session",
            "type": "effect:session:renew-if-expired",
            "session_id": "session-1",
            "extend_by": "8h",
        }
    )
    session_approve_tools = adapter.validate_python(
        {
            "id": "effect-session-approve-tools",
            "display_name": "Approve tools",
            "type": "effect:session:approve-mcp-tools-for-run",
            "session_id": "session-1",
            "tools": ["run_script"],
        }
    )
    session_revoke_lease = adapter.validate_python(
        {
            "id": "effect-session-revoke-lease",
            "display_name": "Revoke tool lease",
            "type": "effect:session:revoke-mcp-tool-approval-lease",
            "lease_id": "lease-1",
            "session_id": "session-1",
            "workflow_run_id": "workflow-run-1",
        }
    )
    artifact_stage = adapter.validate_python(
        {
            "id": "effect-artifact-stage",
            "display_name": "Stage artifact",
            "type": "effect:artifact:stage",
            "session_id": "session-1",
            "path": "repos/project/report.html",
        }
    )
    artifact_publish = adapter.validate_python(
        {
            "id": "effect-artifact-publish",
            "display_name": "Publish artifacts",
            "type": "effect:artifact:publish-artifacts",
            "session_id": "session-1",
            "key": "runs/session-1/report",
        }
    )
    artifact_import = adapter.validate_python(
        {
            "id": "effect-artifact-import",
            "display_name": "Import artifact",
            "type": "effect:artifact:import-published",
            "session_id": "session-1",
            "key": "runs/session-1/report",
        }
    )

    assert vm.type == "effect:vm:create"
    assert vm.labels == {"workflow": "issue-worker"}
    assert vm_start.type == "effect:vm:start"
    assert vm_stop.type == "effect:vm:stop"
    assert agent_create.type == "effect:agent:create"
    assert agent_create.existing_agent_behavior == "noop"
    assert agent_create.teams_channel_allowlist == ["team-a/channel-1", "chat:chat-1"]
    assert agent_create.teams_chat_allowlist == ["chat:chat-2"]
    assert agent_create_chat_only.type == "effect:agent:create"
    assert agent_create_chat_only.config.get("teams_channel_allowlist") is None
    assert agent_create_chat_only.teams_chat_allowlist == ["chat:chat-3"]
    assert agent_create.communication_file_receive_enabled is True
    assert agent_create.communication_file_send_enabled is True
    assert agent_create.communication_file_max_bytes == 4096
    assert agent_create.communication_file_require_approval_for_send is True
    assert agent_create.metadata == {"workflow": {"state": "active"}}
    assert agent_destroy.type == "effect:agent:destroy"
    assert agent_clone.type == "effect:agent:clone-repo"
    assert agent_frontend.type == "effect:agent:bootstrap-frontend"
    assert agent_copy.type == "effect:agent:copy-files"
    assert prompt.type == "effect:agent:prompt"
    assert enqueue_prompt.type == "effect:agent:enqueue-prompt"
    assert agent_exec.type == "effect:agent:exec"
    assert bash_script.type == "effect:bash:script"
    assert http_request.type == "effect:http:request"
    assert http_request.allowed_url_patterns == ["https://example.test/*"]
    assert diagnostics_snapshot.type == "effect:runtime:diagnostics-snapshot"
    assert diagnostics_snapshot.units == ["agent-sandbox-effect-orchestrator.service"]
    assert vm_exec.type == "effect:vm:exec"
    assert vm_copy.type == "effect:vm:copy-files"
    assert issue_state.type == "effect:issue:update-state"
    assert ado_pipeline.type == "effect:ado:pipeline-run"
    assert ado_pipeline.pipeline_id == 42
    assert communication.type == "effect:communication:send-message"
    assert notification.type == "effect:notification:send"
    assert session_renew.type == "effect:session:renew-if-expired"
    assert session_approve_tools.type == "effect:session:approve-mcp-tools-for-run"
    assert session_revoke_lease.type == "effect:session:revoke-mcp-tool-approval-lease"
    assert artifact_stage.type == "effect:artifact:stage"
    assert artifact_publish.type == "effect:artifact:publish-artifacts"
    assert artifact_import.type == "effect:artifact:import-published"
    assert promote.type == "effect:git:promote-agent-branch"
    assert pr_create.type == "effect:pr:create"
    assert prompt.prompt == "Continue ${{effect_1.session_id}}"

    _record_result(
        report_collector,
        requirement_id="schema:supported-effect-types",
        status="pass",
        coverage=["schema validation coverage"],
        details="All supported effect types validate with their required envelope fields.",
    )


def test_effect_schema_validation_rejects_missing_required_fields(
    report_collector: dict[str, Any],
) -> None:
    adapter = _effect_adapter()
    with pytest.raises(ValueError, match="prompt"):
        adapter.validate_python(
            {
                "id": "effect-missing-prompt",
                "display_name": "Missing prompt",
                "type": "effect:agent:prompt",
            }
        )

    _record_result(
        report_collector,
        requirement_id="schema:required-fields",
        status="pass",
        coverage=["schema validation coverage"],
        details="Missing prompt emits an explicit validation failure.",
    )


def test_effect_provider_registration_and_factory_build(
    tmp_path: Path, report_collector: dict[str, Any]
) -> None:
    class _RecordingProvider(EffectProviderBase):
        pass

    config = GatewayConfig(runtime_dir=tmp_path / "runtime")
    DirectoryService(DirectoryStore(config.directories_store_path)).create(
        parent="root",
        name="team-a",
    )
    effect = _effect_adapter().validate_python(
        {
            "id": "effect-provider",
            "display_name": "Provider",
            "type": "effect:agent:prompt",
            "prompt": "hello",
        }
    )

    registry = EffectProviderRegistry()
    registry.register("effect:agent:prompt", _RecordingProvider)
    factory = EffectProviderFactory(
        ProviderConfigResolver(config),
        admin_service=None,  # type: ignore[arg-type]
        registry=registry,
    )

    provider = factory.build(effect, directory_path="root/team-a")
    assert isinstance(provider, _RecordingProvider)
    assert provider.effect_definition.id == "effect-provider"

    _record_result(
        report_collector,
        requirement_id="provider:registration-and-factory",
        status="pass",
        coverage=["provider registration coverage"],
        details="Custom registry registration resolves and initializes through EffectProviderFactory.",
    )


def test_effect_binding_resolution_and_chained_output_propagation(
    tmp_path: Path, report_collector: dict[str, Any]
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket_number": {"type": "integer"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Provision",
                    "type": "effect:vm:create",
                    "vm_name": "vm-${{trigger-1.ticket_number}}",
                    "outputs": {"session_id": {"type": "string"}},
                },
                {
                    "id": "effect-2",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-1.session_id}}",
                    "prompt": "Resume session ${{effect-1.session_id}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {"session_id": "session-42"},
            "effect-2": {"ok": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 10, 0, tzinfo=timezone.utc),
            payload={"ticket_number": 42},
        )
    )

    for _ in range(6):
        worker.run_once()

    payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    by_effect = {entry["effect_id"]: entry for entry in payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-1"]["resolved_config"]["vm_name"] == "vm-42"
    assert by_effect["effect-2"]["resolved_config"]["session_id"] == "session-42"
    assert by_effect["effect-2"]["resolved_config"]["prompt"] == "Resume session session-42"

    _record_result(
        report_collector,
        requirement_id="binding:resolution-and-chaining",
        status="pass",
        coverage=["binding resolution coverage", "chained effect execution and output propagation"],
        details="Exact and embedded bindings resolve across a two-effect chain and propagate outputs downstream.",
    )


def test_effect_queue_and_execution_idempotency(
    tmp_path: Path, report_collector: dict[str, Any]
) -> None:
    runtime_dir = tmp_path / "runtime"
    queue = TriggerEventQueue(runtime_dir / "queue.json")
    event = TriggerEvent(
        event_id="evt-1",
        trigger_id="trigger-1",
        trigger_type="trigger:cron",
        occurred_at=datetime(2026, 4, 4, 11, 0, tzinfo=timezone.utc),
        payload={"n": 1},
    )
    first = queue.publish(event, idempotency_key="provider:1")
    second = queue.publish(
        TriggerEvent.model_validate(event.model_dump(mode="json") | {"event_id": "evt-2"}),
        idempotency_key="provider:1",
    )
    assert first.event_id == second.event_id

    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Once",
                    "type": "effect:agent:prompt",
                    "prompt": "go",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-1": {"ok": True}})
    worker.event_queue.publish(event)

    for _ in range(4):
        worker.run_once()
    worker.event_queue.publish(event)
    for _ in range(4):
        worker.run_once()

    executions = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert len(executions) == 1

    _record_result(
        report_collector,
        requirement_id="queue:idempotency",
        status="pass",
        coverage=["queue/idempotency behavior"],
        details="Queue dedupes by idempotency key and effect execution dedupes by event/effect key.",
    )


def test_effect_regression_missing_upstream_output_reference_records_failed_execution(
    tmp_path: Path,
    report_collector: dict[str, Any],
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "runtime_dir": runtime_dir,
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "triggers": [
                    {
                        "id": "trigger-1",
                        "display_name": "Tick",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                        "outputs": {"ticket_number": {"type": "integer"}},
                    }
                ],
                "effects": [
                    {
                        "id": "effect-1",
                        "display_name": "Bad binding",
                        "type": "effect:agent:prompt",
                        "prompt": "missing ${{trigger-1.session_id}}",
                    }
                ],
                "edges": [_edge("trigger-1", "effect-1")],
            }
        ),
        runtime=None,  # type: ignore[arg-type]
    )
    worker.provider_factory = _Factory({})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-missing",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 11, 30, tzinfo=timezone.utc),
            payload={"ticket_number": 7},
        )
    )

    worker.run_once()

    payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(payload.values()))
    assert record["status"] == "failed"
    assert "not declared by upstream node" in record["error"]

    _record_result(
        report_collector,
        requirement_id="regression:missing-upstream-output",
        status="pass",
        coverage=["regression tests for failure handling"],
        details="Missing output references persist a failed execution record without wedging the queue.",
    )


def test_effect_regression_provider_execution_failure_records_failed_status(
    tmp_path: Path,
    report_collector: dict[str, Any],
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "runtime_dir": runtime_dir,
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "triggers": [
                    {
                        "id": "trigger-1",
                        "display_name": "Tick",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
                "effects": [
                    {
                        "id": "effect-1",
                        "display_name": "Fail",
                        "type": "effect:agent:prompt",
                        "prompt": "boom",
                    }
                ],
                "edges": [_edge("trigger-1", "effect-1")],
            }
        ),
        runtime=None,  # type: ignore[arg-type]
    )
    worker.provider_factory = type(
        "_FailFactory", (), {"build": staticmethod(lambda _effect: _FailingProvider())}
    )()
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-fail",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 11, 45, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()
    worker.run_once()

    payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(payload.values()))
    assert record["status"] == "failed"
    assert "provider boom" in record["error"]

    _record_result(
        report_collector,
        requirement_id="regression:provider-failure",
        status="pass",
        coverage=["regression tests for failure handling"],
        details="Provider exceptions are persisted as failed execution records.",
    )


def test_effect_regression_cycle_detection_skips_execution(
    tmp_path: Path, report_collector: dict[str, Any]
) -> None:
    runtime_dir = tmp_path / "runtime"
    with pytest.raises(ValueError, match="Non-cyclic edges cannot form a cycle"):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "runtime_dir": runtime_dir,
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "effects": [
                    {
                        "id": "effect-a",
                        "display_name": "A",
                        "type": "effect:agent:prompt",
                        "prompt": "A",
                    },
                    {
                        "id": "effect-b",
                        "display_name": "B",
                        "type": "effect:agent:prompt",
                        "prompt": "B",
                    },
                ],
                "edges": [_edge("effect-b", "effect-a"), _edge("effect-a", "effect-b")],
            }
        )

    _record_result(
        report_collector,
        requirement_id="regression:cycle-detection",
        status="pass",
        coverage=["regression tests for failure handling"],
        details="Invalid non-cyclic graph cycles are rejected at config validation time.",
    )


def test_effect_conformance_traceability_matrix_and_report(
    report_collector: dict[str, Any], tmp_path: Path
) -> None:
    matrix = {
        "task_schema_validation": ["schema:supported-effect-types", "schema:required-fields"],
        "task_provider_registration": ["provider:registration-and-factory"],
        "task_binding_and_chain": ["binding:resolution-and-chaining"],
        "task_queue_idempotency": ["queue:idempotency"],
        "task_failure_regressions": [
            "regression:missing-upstream-output",
            "regression:provider-failure",
            "regression:cycle-detection",
        ],
    }

    seen = {entry["requirement_id"] for entry in report_collector["requirements"]}
    missing: dict[str, list[str]] = {}
    for task_name, required_requirements in matrix.items():
        uncovered = [
            requirement_id for requirement_id in required_requirements if requirement_id not in seen
        ]
        if uncovered:
            missing[task_name] = uncovered

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "pass" if not missing else "fail",
        "requirements": sorted(
            report_collector["requirements"], key=lambda item: item["requirement_id"]
        ),
        "traceability_matrix": matrix,
        "missing_coverage": missing,
    }

    report_path = Path(os.environ.get(REPORT_ENV_VAR, "runtime/effect-conformance-report.json"))
    if not report_path.is_absolute():
        report_path = Path.cwd() / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    local_copy = tmp_path / "effect-conformance-report.json"
    local_copy.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    assert not missing, f"Traceability matrix has missing coverage: {missing}"
    assert summary["status"] == "pass"
    assert report_path.exists()
