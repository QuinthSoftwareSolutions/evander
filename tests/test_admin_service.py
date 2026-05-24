from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from types import MethodType, SimpleNamespace
from typing import Any

import agent_sandbox.admin.service as admin_service
import httpx
import pytest
from agent_sandbox.capacity_health import HealthState, TelemetryStatus
from agent_sandbox.admin.facades import WorkerAdminDependencies, WorkerAdminFacade
from agent_sandbox.admin.service import AdminService
from agent_sandbox.config import (
    DiscordIngestWorkerGlobalConfig,
    DiscordWakeWorkerGlobalConfig,
    ScheduledWakeWorkerGlobalConfig,
    TeamsIngestWorkerGlobalConfig,
)
from agent_sandbox.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from agent_sandbox.execution.container_worker_client import ContainerWorkerClient
from agent_sandbox.execution.container_worker_client import ContainerWorkerClientError
from agent_sandbox.communication_artifacts import CommunicationArtifactStore
from agent_sandbox.events.internal import InternalEventBus
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.messaging.agent_prompt_queue import AgentPromptQueueStore
from agent_sandbox.models import (
    ApprovalNotificationDeliveryRecord,
    ApprovalNotificationDeliveryStatus,
    ApprovalNotificationStatus,
    ApprovalRequest,
    ApprovalStatus,
    AgentRecord,
    ContainerSessionLifecycleState,
    SessionManifest,
    ToolName,
    VmActionResult,
    VmSummary,
)
from agent_sandbox.pr_feedback import FeedbackEnvelope, PrFeedbackStore
from agent_sandbox.messaging.agent_prompt_store import AgentPromptRecord
from agent_sandbox.triggers.queue import SqliteTriggerEventQueue
from agent_sandbox.triggers.queue import TriggerEventQueue
from agent_sandbox.daemons.effect_orchestrator import (
    GateExecutionRecord,
    EffectExecutionRecord,
    JsonExecutionStore,
    JsonGateExecutionStore,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from agent_sandbox.repositories.chat_assistant_sessions import JsonChatAssistantSessionRepository
from agent_sandbox.repositories.orchestration import SqliteWorkflowConfigSnapshotRepository
from agent_sandbox.worker.models import (
    WorkerFileWriteRecord,
    WorkerFileWriteRequest,
    WorkerSessionPrepareRequest,
    WorkerSessionRecord,
    WorkerTelemetryPayload,
)

UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[1]


def write_config(tmp_path: Path, *, enable_directory_scoped_config: bool = False) -> Path:
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
                f"enable_directory_scoped_config: {str(enable_directory_scoped_config).lower()}",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _write_run_detail_audit_log(path: Path, *, session_id: str, vm_id: str) -> None:
    audit_entries = [
        {
            "event_type": "tool_call",
            "session_id": session_id,
            "vm_id": vm_id,
            "directory_path": "root",
            "tool_name": "prompt_agent",
            "request_id": "request-sqlite-node",
            "correlation_id": "correlation-sqlite-node",
            "approval_request_id": "approval-sqlite",
            "workflow_run_id": "evt-sqlite",
            "trace_id": "trace-sqlite",
            "decision": "allowed",
            "duration_ms": 19,
            "args": {
                "session_id": session_id,
                "request_id": "request-sqlite-node",
                "approval_request_id": "approval-sqlite",
                "prompt": "Run the sqlite flow",
                "token": "sqlite-secret-token",
            },
            "result": {
                "request_id": "request-sqlite-node",
                "status": "success",
                "response_token": "response-token-sqlite",
                "secret": "sqlite-result-secret",
            },
            "created_at": "2026-05-18T11:00:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": session_id,
            "vm_id": vm_id,
            "directory_path": "root",
            "tool_name": "make_phone_call",
            "correlation_id": "correlation-sqlite-run-level",
            "workflow_run_id": "evt-sqlite",
            "trace_id": "trace-sqlite",
            "decision": "denied",
            "reason": "blocked",
            "args": {
                "session_id": session_id,
                "correlation_id": "correlation-sqlite-run-level",
                "number": "+1-555-0199",
            },
            "result": {
                "status": "denied",
                "reason": "blocked",
            },
            "created_at": "2026-05-18T11:01:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": session_id,
            "vm_id": vm_id,
            "directory_path": "root",
            "tool_name": "make_phone_call",
            "workflow_run_id": "evt-sqlite",
            "trace_id": "trace-sqlite",
            "decision": "allowed",
            "reason": "trace-only match",
            "args": {
                "session_id": session_id,
                "note": "trace-only",
            },
            "result": {
                "status": "success",
                "summary": "trace-only",
            },
            "created_at": "2026-05-18T11:00:30+00:00",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in audit_entries) + "\n",
        encoding="utf-8",
    )


def test_create_local_issue_persists_yaml_and_store(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    issue = service.create_local_issue(
        title="Fix VM dashboard",
        body="Build a desktop dashboard for VM state.",
        repo_source="/src/repo",
    )

    assert issue.issue_number == "1"
    assert issue.issue_id == "root::local::1"
    assert issue.title == "Fix VM dashboard"
    yaml_path = Path(issue.yaml_path)
    assert yaml_path.exists()
    yaml_text = yaml_path.read_text(encoding="utf-8")
    assert "issue_number: 1" in yaml_text
    assert '"Fix VM dashboard"' in yaml_text

    payload = json.loads((tmp_path / "runtime" / "local-issues.json").read_text(encoding="utf-8"))
    assert payload[issue.issue_id]["directory_path"] == "root"
    assert payload[issue.issue_id]["repo_source"] == "/src/repo"


def test_list_agents_includes_session_only_container_worker_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            directory_path=team_path,
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
                "labels": {"role": "demo"},
            },
        )
    )
    monkeypatch.setattr(service, "list_vms", MethodType(lambda self, **kwargs: [], service))

    agents = service.list_agents(
        directory_path=team_path,
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
    )

    assert len(agents) == 1
    assert agents[0].agent_id == "container-session"
    assert agents[0].target_name == "container-target"
    assert agents[0].backend == "container_worker_vm"
    assert agents[0].status == "prepared"
    assert agents[0].host["labels"] == {"role": "demo"}


def test_list_agents_descendant_scope_includes_child_directory_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    team = service.create_directory_child("root", "team")
    assert team.ok is True and team.data is not None
    team_path = team.data.details.directory.path
    child = service.create_directory_child(team_path, "child")
    assert child.ok is True and child.data is not None
    child_path = child.data.details.directory.path
    expires_at = datetime.now(UTC) + timedelta(hours=1)

    for session_id, directory_path in [
        ("vm-root", "root"),
        ("vm-team", team_path),
        ("vm-child", child_path),
    ]:
        service.runtime.session_repository.upsert_session(
            SessionManifest(
                session_id=session_id,
                vm_id=session_id,
                directory_path=directory_path,
                allowed_tools=[],
                expires_at=expires_at,
            )
        )

    for session_id, directory_path in [
        ("container-root", "root"),
        ("container-team", team_path),
        ("container-child", child_path),
    ]:
        service.runtime.session_repository.upsert_session(
            SessionManifest(
                session_id=session_id,
                vm_provider="container_worker_vm",
                vm_provider_id=f"{session_id}-target",
                directory_path=directory_path,
                allowed_tools=[],
                expires_at=expires_at,
                metadata={
                    "execution_backend": "container_worker_vm",
                    "target_name": f"{session_id}-target",
                },
            )
        )

    monkeypatch.setattr(service, "list_vms", MethodType(lambda self, **kwargs: [], service))

    descendant_agents = service.list_agents(
        directory_path=team_path,
        direct_only=False,
        include_inactive=True,
        reconcile_containers=False,
    )
    assert {agent.session_id for agent in descendant_agents} == {
        "vm-team",
        "vm-child",
        "container-team",
        "container-child",
    }

    direct_agents = service.list_agents(
        directory_path=team_path,
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
    )
    assert {agent.session_id for agent in direct_agents} == {"vm-team", "container-team"}


def test_read_session_artifact_text_routes_container_sessions_to_worker_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "worker_url": "http://worker.test",
            },
        )
    )
    calls: list[dict[str, object]] = []

    class FakeContainerBackend:
        def read_session_artifact_text(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return "daily summary\n"

    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        lambda **kwargs: calls.append({"backend_kwargs": kwargs}) or FakeContainerBackend(),
    )

    result = service.read_session_artifact_text(
        session_id="container-session",
        path="reports/summary.txt",
    )

    assert calls == [
        {
            "backend_kwargs": {
                "directory_path": "root",
                "worker_url": "http://worker.test",
            }
        },
        {"session_id": "container-session", "relative_path": "reports/summary.txt"},
    ]
    assert result["content"] == "daily summary\n"
    assert result["execution_backend"] == "container_worker_vm"


def test_read_session_artifact_text_routes_vm_sessions_to_vm_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="vm-session",
            vm_id="vm-1",
            vm_provider="libvirt",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    calls: list[dict[str, object]] = []

    def fake_vm_exec(target: str, **kwargs: object) -> dict[str, object]:
        calls.append({"target": target, **kwargs})
        return {"success": True, "stdout": "vm summary\n", "vm_name": "vm-1"}

    monkeypatch.setattr(service, "vm_exec", fake_vm_exec)

    result = service.read_session_artifact_text(
        session_id="vm-session",
        path="reports/summary.txt",
    )

    assert calls[0]["target"] == "vm-session"
    assert calls[0]["environment"] == {
        "AGENT_SANDBOX_ARTIFACT_RELATIVE_PATH": "reports/summary.txt"
    }
    assert "/artifacts" in str(calls[0]["command"])
    assert result["content"] == "vm summary\n"
    assert result["execution_backend"] == "libvirt_vm"


def test_list_agents_groups_container_executions_under_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-old",
            session_id="container-session",
            worker_job_id="job-old",
            worker_url="http://worker.test",
            target_name="container-target",
            last_status="removed",
            cleanup_status="cleanup_succeeded",
            cleanup_completed_at=datetime.now(UTC) - timedelta(minutes=5),
            metadata={
                "image": "runner:old",
                "cleanup_status": "cleanup_succeeded",
            },
        )
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-current",
            session_id="container-session",
            worker_job_id="job-current",
            worker_url="http://worker.test",
            target_name="container-target",
            last_status="running",
            metadata={"image": "runner:latest"},
        )
    )
    monkeypatch.setattr(service, "list_vms", MethodType(lambda self, **kwargs: [], service))

    agents = service.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
    )

    assert [agent.agent_id for agent in agents] == ["container-session"]
    assert agents[0].status == "running"
    assert agents[0].capabilities["logs"] is True
    assert agents[0].capabilities["stop"] is True
    assert agents[0].host["execution_count"] == 2
    assert agents[0].host["active_execution_count"] == 1
    assert agents[0].host["latest_execution_id"] == "exec-current"
    assert [item["execution_id"] for item in agents[0].host["executions"]] == [
        "exec-current",
        "exec-old",
    ]
    sessions = {item.session_id: item for item in service.list_sessions(include_expired=True)}
    lifecycle = sessions["container-session"].container_lifecycle
    assert lifecycle is not None
    assert lifecycle.state == ContainerSessionLifecycleState.RUNNING


def test_list_agents_ignores_execution_cleanup_success_for_session_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-old",
            session_id="container-session",
            worker_job_id="job-old",
            worker_url="http://worker.test",
            target_name="container-target",
            last_status="removed",
            cleanup_status="cleanup_succeeded",
            cleanup_completed_at=datetime.now(UTC) - timedelta(minutes=5),
            metadata={
                "image": "runner:old",
                "cleanup_status": "cleanup_succeeded",
            },
        )
    )
    monkeypatch.setattr(service, "list_vms", MethodType(lambda self, **kwargs: [], service))

    agents = service.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
    )

    assert [(agent.agent_id, agent.status) for agent in agents] == [
        ("container-session", "idle")
    ]
    sessions = {item.session_id: item for item in service.list_sessions(include_expired=True)}
    lifecycle = sessions["container-session"].container_lifecycle
    assert lifecycle is not None
    assert lifecycle.state == ContainerSessionLifecycleState.STOPPED
    assert lifecycle.actions.cleanup.supported is True


def test_list_agents_still_returns_container_sessions_when_vm_inventory_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        )
    )

    def fail_list_vms(self: AdminService, **_kwargs: object) -> list[object]:
        raise RuntimeError("sudo: a password is required")

    monkeypatch.setattr(service, "list_vms", MethodType(fail_list_vms, service))

    agents = service.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
    )

    assert [(agent.agent_id, agent.backend, agent.status) for agent in agents] == [
        ("container-session", "container_worker_vm", "prepared")
    ]
    container_agents = service.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
        backend="container_worker_vm",
    )
    assert [(agent.agent_id, agent.backend, agent.status) for agent in container_agents] == [
        ("container-session", "container_worker_vm", "prepared")
    ]
    with pytest.raises(RuntimeError, match="sudo: a password is required"):
        service.list_agents(
            directory_path="root",
            direct_only=True,
            include_inactive=True,
            reconcile_containers=False,
            backend="libvirt_vm",
        )


def test_list_agents_surfaces_stale_vm_session_when_inventory_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-shared",
            vm_id="assistant-shared",
            vm_provider="libvirt",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"labels": {"system": "chat_assistant"}},
        )
    )

    def fail_list_vms(self: AdminService, **_kwargs: object) -> list[object]:
        raise RuntimeError("virsh: command not found")

    monkeypatch.setattr(service, "list_vms", MethodType(fail_list_vms, service))

    agents = service.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
        backend="libvirt_vm",
    )

    assert len(agents) == 1
    assert agents[0].agent_id == "assistant-shared"
    assert agents[0].target_name == "assistant-shared"
    assert agents[0].session_id == "assistant-shared"
    assert agents[0].backend == "libvirt_vm"
    assert agents[0].status == "stale"
    assert agents[0].capabilities["destroy"] is True
    assert agents[0].capabilities["start"] is False
    assert agents[0].host["inventory_status"] == "missing"
    assert agents[0].host["inventory_error"] == "virsh: command not found"
    assert agents[0].host["session_policy"]["session_id"] == "assistant-shared"


def test_read_capacity_health_aggregates_sessions_workers_queue_and_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(admin_service, "_utc_now", lambda: now)

    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="vm-active",
            vm_id="vm-active",
            directory_path="root",
            allowed_tools=[],
            created_at=now - timedelta(hours=2),
            expires_at=now + timedelta(hours=2),
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="vm-retained",
            vm_id="vm-retained",
            directory_path="root",
            allowed_tools=[],
            created_at=now - timedelta(hours=4),
            expires_at=now - timedelta(hours=1),
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-active",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            directory_path="root",
            allowed_tools=[],
            created_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=4),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
                "worker_url": "http://worker-ok.test",
            },
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="vm-stale",
            vm_id="vm-stale",
            directory_path=team_path,
            allowed_tools=[],
            created_at=now - timedelta(hours=6),
            expires_at=now - timedelta(hours=2),
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-missing",
            vm_provider="container_worker_vm",
            vm_provider_id="container-missing-target",
            directory_path=team_path,
            allowed_tools=[],
            created_at=now - timedelta(minutes=30),
            expires_at=now + timedelta(hours=3),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-missing-target",
                "worker_url": "http://worker-missing.test",
            },
        )
    )

    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-ok",
            session_id="container-active",
            worker_job_id="job-ok",
            worker_url="http://worker-ok.test",
            target_name="container-target",
            started_at=now - timedelta(minutes=45),
            last_status="running",
            directory_path="root",
            metadata={"image": "runner:latest"},
        )
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-missing",
            session_id="container-missing",
            worker_job_id="job-missing",
            worker_url="http://worker-missing.test",
            target_name="container-missing-target",
            started_at=now - timedelta(minutes=15),
            last_status="failed",
            directory_path=team_path,
            metadata={"image": "runner:latest"},
        )
    )

    service.runtime.agent_prompt_store.create_prompt_message(
        AgentPromptRecord(
            message_id="prompt-1",
            sender_session_id="vm-active",
            sender_vm_id="vm-active",
            target_session_id="container-active",
            target_vm_id="container-target",
            prompt_payload={"prompt": "check-in"},
            issued_token_hash="prompt-1-token",
            issued_token_claims={"aud": "container-active"},
            status="unread",
            created_at=now - timedelta(hours=5),
            updated_at=now - timedelta(hours=5),
        )
    )

    queue_path = service.config.runtime_dir / "workflow-events.json"
    queue = TriggerEventQueue(queue_path)
    queue.publish(
        TriggerEvent(
            event_id="evt-old",
            trigger_id="trigger-1",
            trigger_type="trigger:test",
            occurred_at=now - timedelta(hours=3),
            payload={"name": "old"},
        )
    )
    queue.publish(
        TriggerEvent(
            event_id="evt-new",
            trigger_id="trigger-2",
            trigger_type="trigger:test",
            occurred_at=now - timedelta(minutes=10),
            payload={"name": "new"},
        )
    )
    queue.mark_inflight("evt-new")

    workflow_paths = {
        "orchestration_store_backend": "json",
        "orchestration_store_path": service.config.runtime_dir / "orchestration.db",
        "event_queue_store_path": queue_path,
        "effect_execution_store_path": service.config.runtime_dir / "effect-executions.json",
        "gate_execution_store_path": service.config.runtime_dir / "gate-executions.json",
        "workflow_config_snapshot_store_path": service.config.runtime_dir
        / "workflow-config-snapshots.json",
        "await_agent_store_path": service.config.runtime_dir / "await-agent.json",
        "legacy_await_agent_store_path": service.config.runtime_dir / "legacy-await-agent.json",
    }
    monkeypatch.setattr(
        service,
        "_workflow_run_store_paths",
        MethodType(lambda self: workflow_paths, service),
    )

    class FakeContainerWorkerClient:
        def __init__(
            self,
            *,
            base_url: str,
            token_env: str = "AGENT_SANDBOX_WORKER_TOKEN",
            timeout_seconds: float = 30.0,
            **_kwargs: object,
        ) -> None:
            self.base_url = base_url.rstrip("/")
            self.token_env = token_env
            self.timeout_seconds = timeout_seconds

        def health(self) -> dict[str, object]:
            return {"status": "ok", "worker_url": self.base_url}

        def telemetry(self) -> WorkerTelemetryPayload:
            if self.base_url == "http://worker-missing.test":
                raise ContainerWorkerClientError(
                    "container worker bearer token is not configured in env var "
                    "AGENT_SANDBOX_WORKER_TOKEN"
                )
            return WorkerTelemetryPayload.model_validate(
                {
                    "generated_at": "2026-05-21T12:00:00Z",
                    "disk": {
                        "worker_root": {
                            "scope": "worker_root",
                            "status": "available",
                            "health": "ok",
                            "total_bytes": 100,
                            "used_bytes": 40,
                            "free_bytes": 60,
                            "used_percent": 40.0,
                            "free_percent": 60.0,
                            "error_count": 0,
                            "errors": [],
                        }
                    },
                    "session_storage": {
                        "total": {
                            "scope": "worker_sessions",
                            "status": "available",
                            "health": "ok",
                            "size_bytes": 2048,
                            "file_count": 2,
                            "directory_count": 1,
                            "symlink_count": 0,
                            "error_count": 0,
                            "errors": [],
                        },
                        "session_count": 1,
                        "largest_sessions": [
                            {
                                "session_id": "container-active",
                                "directory_path": "sessions/container-active",
                                "backend": "container_worker_vm",
                                "size_bytes": 2048,
                                "created_at": "2026-05-21T11:00:00Z",
                                "updated_at": "2026-05-21T11:30:00Z",
                            }
                        ],
                        "oldest_sessions": [
                            {
                                "session_id": "container-active",
                                "directory_path": "sessions/container-active",
                                "backend": "container_worker_vm",
                                "size_bytes": 2048,
                                "created_at": "2026-05-21T11:00:00Z",
                                "updated_at": "2026-05-21T11:30:00Z",
                            }
                        ],
                    },
                    "jobs": {
                        "status": "available",
                        "health": "ok",
                        "total_jobs": 1,
                        "counts_by_status": {"running": 1},
                        "running_job_count": 1,
                        "failed_start_count": 0,
                        "cleanup_failure_status": "unsupported",
                        "cleanup_failure_message": "cleanup failures are not currently persisted",
                        "error_count": 0,
                        "errors": [],
                    },
                }
            )

    monkeypatch.setattr("agent_sandbox.admin.service.ContainerWorkerClient", FakeContainerWorkerClient)

    def fake_list_agents(self: AdminService, **_kwargs: object) -> list[AgentRecord]:
        return [
            AgentRecord(
                agent_id="vm-active",
                backend="libvirt_vm",
                target_name="vm-active",
                session_id="vm-active",
                directory_path="root",
                status="running",
                host={"kind": "vm"},
            ),
            AgentRecord(
                agent_id="vm-retained",
                backend="libvirt_vm",
                target_name="vm-retained",
                session_id="vm-retained",
                directory_path="root",
                status="stopped",
                host={"kind": "vm_session"},
            ),
            AgentRecord(
                agent_id="container-active",
                backend="container_worker_vm",
                target_name="container-target",
                session_id="container-active",
                directory_path="root",
                status="running",
                host={"kind": "container_worker_session"},
            ),
            AgentRecord(
                agent_id="vm-stale",
                backend="libvirt_vm",
                target_name="vm-stale",
                session_id="vm-stale",
                directory_path=team_path,
                status="stale",
                host={"kind": "vm_session"},
            ),
            AgentRecord(
                agent_id="container-missing",
                backend="container_worker_vm",
                target_name="container-missing-target",
                session_id="container-missing",
                directory_path=team_path,
                status="prepared",
                host={"kind": "container_worker_session"},
            ),
        ]

    monkeypatch.setattr(service, "list_agents", MethodType(fake_list_agents, service))

    health = service.read_capacity_health()
    worker_entries = {entry.worker_url: entry for entry in health.workers.entries}
    missing_scopes = {entry.scope for entry in health.sessions.storage.missing_telemetry}
    payload = json.dumps(health.model_dump(mode="json"))

    assert health.control_plane.disk.status is TelemetryStatus.AVAILABLE
    assert health.control_plane.storage.total.status is TelemetryStatus.AVAILABLE
    assert health.sessions.total_sessions == 5
    assert health.sessions.total_agents == 5
    assert health.sessions.backend_counts == {
        "container_worker_vm": 2,
        "libvirt_vm": 3,
    }
    assert health.sessions.directory_counts == {"root": 3, team_path: 2}
    assert health.sessions.directory_breakdowns["root"]["total_sessions"] == 3
    assert health.sessions.directory_breakdowns["root"]["lifecycle_counts"] == {
        "active": 2,
        "retained": 1,
    }
    assert health.sessions.directory_breakdowns[team_path]["total_sessions"] == 2
    assert health.sessions.directory_breakdowns[team_path]["lifecycle_counts"] == {
        "failed_stale": 1,
        "waiting": 1,
    }
    assert health.sessions.lifecycle_counts == {
        "active": 2,
        "failed_stale": 1,
        "retained": 1,
        "waiting": 1,
    }
    assert health.sessions.status_counts == {
        "running": 2,
        "stopped": 1,
        "stale": 1,
        "prepared": 1,
    }
    assert health.sessions.storage.measured_total.status is TelemetryStatus.AVAILABLE
    assert health.sessions.storage.measured_total.size_bytes == 2048
    assert health.sessions.storage.measured_session_count == 1
    assert health.sessions.storage.largest_sessions[0].session_id == "container-active"
    assert health.sessions.storage.oldest_sessions[0].session_id == "container-active"
    assert "session_storage:libvirt_vm" in missing_scopes
    assert "session_storage:worker:http://worker-missing.test" in missing_scopes
    assert health.workers.total_workers == 2
    assert health.workers.reachable_workers == 2
    assert health.workers.telemetry_available_workers == 1
    assert worker_entries["http://worker-ok.test"].reachable is True
    assert worker_entries["http://worker-ok.test"].telemetry_status is TelemetryStatus.AVAILABLE
    assert worker_entries["http://worker-ok.test"].telemetry is not None
    assert worker_entries["http://worker-ok.test"].last_heartbeat_source == (
        "worker_telemetry_live_probe"
    )
    assert worker_entries["http://worker-missing.test"].reachable is True
    assert worker_entries["http://worker-missing.test"].telemetry_status is TelemetryStatus.PERMISSION_DENIED
    assert worker_entries["http://worker-missing.test"].healthz_status is TelemetryStatus.AVAILABLE
    assert worker_entries["http://worker-missing.test"].last_heartbeat_source == (
        "healthz_live_probe"
    )
    assert health.queue.status is TelemetryStatus.AVAILABLE
    assert health.queue.backend == "json"
    assert health.queue.pending_count == 1
    assert health.queue.inflight_count == 1
    assert health.queue.oldest_event_id == "evt-old"
    assert health.queue.oldest_age_seconds is not None and health.queue.oldest_age_seconds > 10000
    assert health.prompts.status is TelemetryStatus.AVAILABLE
    assert health.prompts.unread_count == 1
    assert health.prompts.oldest_message_id == "prompt-1"
    assert health.prompts.health is HealthState.WARNING
    assert str(tmp_path) not in payload


def test_capacity_worker_descriptors_ignore_historical_execution_only_worker_urls(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-current",
            vm_provider="container_worker_vm",
            vm_provider_id="container-current-target",
            directory_path="root",
            allowed_tools=[],
            expires_at=expires_at,
            metadata={
                "execution_backend": "container_worker_vm",
                "worker_url": "http://worker-current.test",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-stale",
            session_id="container-old",
            worker_job_id="job-stale",
            worker_url="http://worker-stale.test",
            target_name="container-old-target",
            started_at=expires_at - timedelta(days=2),
            last_status="removed",
            directory_path="root",
        )
    )

    descriptors = service._capacity_worker_descriptors(
        service.runtime.session_repository.list_sessions()
    )

    assert [descriptor["worker_url"] for descriptor in descriptors] == [
        "http://worker-current.test"
    ]
    assert descriptors[0]["discovered_from"] == ["session:container-current"]


def test_list_agents_all_directories_includes_vm_and_container_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-root",
            vm_provider="container_worker_vm",
            vm_provider_id="container-root-target",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-root-target",
            },
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-team",
            vm_provider="container_worker_vm",
            vm_provider_id="container-team-target",
            directory_path=team_path,
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-team-target",
            },
        )
    )
    list_vms_kwargs: dict[str, Any] = {}

    def fake_list_vms(self: AdminService, **kwargs: Any) -> list[VmSummary]:
        del self
        list_vms_kwargs.update(kwargs)
        return [
            VmSummary(
                vm_name="vm-root",
                directory_paths=["root"],
                power_state="running",
                agent_state="running",
            ),
            VmSummary(
                vm_name="vm-team",
                directory_paths=[team_path],
                power_state="running",
                agent_state="running",
            ),
        ]

    monkeypatch.setattr(service, "list_vms", MethodType(fake_list_vms, service))

    agents = service.list_agents(
        directory_path="root",
        direct_only=False,
        include_inactive=True,
        reconcile_containers=False,
        all_directories=True,
    )

    assert list_vms_kwargs["directory_path"] is None
    assert list_vms_kwargs["direct_only"] is False
    assert {agent.target_name for agent in agents} == {
        "vm-root",
        "vm-team",
        "container-root-target",
        "container-team-target",
    }
    assert {agent.directory_path for agent in agents} == {"root", team_path}


def test_create_container_agent_uses_directory_scoped_worker_provider_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "providers",
        {
            "container_worker_base_url": "http://team-worker.test:11888",
            "container_worker_token_env": "TEAM_WORKER_TOKEN",
        },
    )
    assert write_result.ok is True
    service.create_skill({"name": "demo.skill", "content": "# Demo\n"})
    captured: list[tuple[str, str, str, dict[str, object]]] = []
    written_paths: list[str] = []

    def prepare_session(
        self: ContainerWorkerClient, request: WorkerSessionPrepareRequest
    ) -> WorkerSessionRecord:
        captured.append(
            (
                self.base_url,
                self.token_env,
                request.session_id,
                dict(request.metadata),
            )
        )
        return WorkerSessionRecord(
            session_id=request.session_id,
            root_path=f"/worker/{request.session_id}",
            agent_home_path=f"/worker/{request.session_id}/agent_home",
            repo_root_path=f"/worker/{request.session_id}/agent_home/repos",
            artifacts_path=f"/worker/{request.session_id}/artifacts",
            logs_path=f"/worker/{request.session_id}/logs",
            metadata_path=f"/worker/{request.session_id}/metadata.json",
            metadata=dict(request.metadata),
        )

    monkeypatch.setattr(ContainerWorkerClient, "prepare_session", prepare_session)

    def write_files(
        self: ContainerWorkerClient,
        session_id: str,
        request: WorkerFileWriteRequest,
    ) -> WorkerFileWriteRecord:
        assert session_id == "container-session"
        written_paths.extend(file.destination_path for file in request.files)
        return WorkerFileWriteRecord(
            session_id=session_id,
            copied_count=len(request.files),
            copied_paths=[file.destination_path for file in request.files],
        )

    monkeypatch.setattr(ContainerWorkerClient, "write_files", write_files)

    result = service.create_admin_agent(
        {
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
            "directory_path": team_path,
            "dotnet": True,
            "nuget": True,
            "playwright": True,
            "codex_skills": ["demo.skill"],
        }
    )

    assert result["success"] is True
    assert captured == [
        (
            "http://team-worker.test:11888",
            "TEAM_WORKER_TOKEN",
            "container-session",
            {
                "execution_backend": "container_worker_vm",
                "directory_path": team_path,
                "target_name": "container-target",
                "labels": {},
                "worker_url": "http://team-worker.test:11888",
            },
        )
    ]
    assert "/home/agent/.agent-sandbox/session.env" in written_paths
    assert "/home/agent/.profile" in written_paths
    assert "/home/agent/.codex/config.toml" in written_paths
    assert "/home/agent/.nuget/NuGet/NuGet.Config" in written_paths
    assert "/home/agent/.config/agent-sandbox/codex/skills/demo.skill/SKILL.md" in written_paths
    manifest = service.runtime.session_repository.get_session("container-session")
    assert manifest is not None
    assert manifest.metadata["worker_url"] == "http://team-worker.test:11888"


def test_create_container_agent_rejects_mssql(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(
        ValueError,
        match="create_admin_agent with container_worker_vm does not support MSSQL yet",
    ):
        service.create_admin_agent(
            {
                "backend": "container_worker_vm",
                "session_id": "container-session",
                "mssql": True,
            }
        )


def test_create_container_agent_rejects_unknown_codex_skill_before_prepare(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))

    def fail_prepare(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("container session should not be prepared")

    monkeypatch.setattr(ContainerWorkerClient, "prepare_session", fail_prepare)

    with pytest.raises(ValueError, match="Unknown Codex skill: missing.skill"):
        service.create_admin_agent(
            {
                "backend": "container_worker_vm",
                "session_id": "container-session",
                "codex_skills": ["missing.skill"],
            }
        )


def test_create_container_agent_does_not_persist_session_when_write_files_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "providers",
        {
            "container_worker_base_url": "http://team-worker.test:11888",
            "container_worker_token_env": "TEAM_WORKER_TOKEN",
        },
    )
    assert write_result.ok is True
    captured: list[tuple[str, dict[str, object]]] = []

    def prepare_session(
        self: ContainerWorkerClient, request: WorkerSessionPrepareRequest
    ) -> WorkerSessionRecord:
        captured.append((request.session_id, dict(request.metadata)))
        return WorkerSessionRecord(
            session_id=request.session_id,
            root_path=f"/worker/{request.session_id}",
            agent_home_path=f"/worker/{request.session_id}/agent_home",
            repo_root_path=f"/worker/{request.session_id}/agent_home/repos",
            artifacts_path=f"/worker/{request.session_id}/artifacts",
            logs_path=f"/worker/{request.session_id}/logs",
            metadata_path=f"/worker/{request.session_id}/metadata.json",
            metadata=dict(request.metadata),
        )

    monkeypatch.setattr(ContainerWorkerClient, "prepare_session", prepare_session)

    def write_files(
        self: ContainerWorkerClient,
        session_id: str,
        request: WorkerFileWriteRequest,
    ) -> WorkerFileWriteRecord:
        assert session_id == "container-session"
        raise RuntimeError("bootstrap write failed")

    monkeypatch.setattr(ContainerWorkerClient, "write_files", write_files)

    with pytest.raises(RuntimeError, match="bootstrap write failed"):
        service.create_admin_agent(
            {
                "backend": "container_worker_vm",
                "session_id": "container-session",
                "target_name": "container-target",
                "directory_path": team_path,
            }
        )

    assert captured == [
        (
            "container-session",
            {
                "execution_backend": "container_worker_vm",
                "directory_path": team_path,
                "target_name": "container-target",
                "labels": {},
                "worker_url": "http://team-worker.test:11888",
            },
        )
    ]
    assert service.runtime.session_repository.get_session("container-session") is None


def test_install_admin_agent_skills_copies_managed_skill_to_container_session(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    service.create_skill({"name": "demo.skill", "content": "# Demo\n"})
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=expires_at,
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        )
    )
    written: list[tuple[str, list[dict[str, object]]]] = []

    class FakeContainerBackend:
        def write_files(
            self,
            *,
            session_id: str,
            files: list[dict[str, object]],
        ) -> WorkerFileWriteRecord:
            written.append((session_id, files))
            return WorkerFileWriteRecord(
                session_id=session_id,
                copied_count=len(files),
                copied_paths=[str(file["destination_path"]) for file in files],
            )

    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        lambda **_kwargs: FakeContainerBackend(),
    )

    result = service.run_admin_agent_action(
        "container-session",
        "install-skills",
        backend="container_worker_vm",
        payload={"codex_skills": ["demo.skill"]},
    )

    assert result["success"] is True
    assert result["session_id"] == "container-session"
    assert written == [
        (
            "container-session",
            [
                {
                    "destination_path": (
                        "/home/agent/.config/agent-sandbox/codex/skills/"
                        "demo.skill/SKILL.md"
                    ),
                    "mode": "0644",
                    "content": "# Demo\n",
                }
            ],
        )
    ]
    updated = service.runtime.session_repository.get_session("container-session")
    assert updated is not None
    assert updated.metadata["codex_skills"] == ["demo.skill"]


def test_install_admin_agent_skills_copies_managed_skill_to_vm_codex_home(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    service.create_skill({"name": "demo.skill", "content": "# Demo\n"})
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            allowed_tools=[],
            expires_at=expires_at,
        )
    )
    copied: list[tuple[str, list[dict[str, object]], str]] = []

    def fake_vm_exec(
        self: AdminService, target: str, **kwargs: object
    ) -> dict[str, object]:
        assert target == "lab-session"
        assert (
            kwargs["command"]
            == 'printf "%s" "${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"'
        )
        return {"success": True, "stdout": "/custom/codex\n"}

    def fake_vm_copy_files(
        self: AdminService,
        target: str,
        *,
        files: list[dict[str, object]],
        vm_user: str = "agent",
    ) -> dict[str, object]:
        copied.append((target, files, vm_user))
        return {
            "success": True,
            "copied_paths": [str(file["destination_path"]) for file in files],
        }

    monkeypatch.setattr(service, "vm_exec", MethodType(fake_vm_exec, service))
    monkeypatch.setattr(service, "vm_copy_files", MethodType(fake_vm_copy_files, service))

    result = service.run_admin_agent_action(
        "lab-vm",
        "install-skills",
        backend="libvirt_vm",
        payload={"codex_skills": ["demo.skill"], "vm_user": "agent"},
    )

    assert result["success"] is True
    assert result["session_id"] == "lab-session"
    assert copied == [
        (
            "lab-session",
            [
                {
                    "destination_path": "/custom/codex/skills/demo.skill/SKILL.md",
                    "mode": "0644",
                    "content": "# Demo\n",
                    "overwrite": True,
                }
            ],
            "agent",
        )
    ]
    updated = service.runtime.session_repository.get_session("lab-session")
    assert updated is not None
    assert updated.metadata["codex_skills"] == ["demo.skill"]


def test_run_admin_agent_action_prompt_agent_queues_running_vm_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setattr(
        service,
        "list_vms",
        MethodType(
            lambda self, **kwargs: [
                VmSummary(
                    vm_name="agent-sandbox-receptionist-vm",
                    power_state="running",
                    agent_state="running",
                    session_ids=["receptionist-session"],
                )
            ],
            service,
        ),
    )

    result = service.run_admin_agent_action(
        "agent-sandbox-receptionist-vm",
        "prompt-agent",
        backend="libvirt_vm",
        payload={
            "prompt": "Summarize the latest changes",
            "resume_thread_id": "thread-keep-1",
        },
    )

    queue_key = "admin-agent-prompt:libvirt_vm:receptionist-session"
    queued = AgentPromptQueueStore(
        tmp_path / "runtime" / "queued-agent-prompts.json"
    ).get(queue_key)

    assert result["success"] is True
    assert result["queued"] is True
    assert result["backend"] == "libvirt_vm"
    assert result["agent_id"] == "agent-sandbox-receptionist-vm"
    assert result["target_name"] == "agent-sandbox-receptionist-vm"
    assert result["session_id"] == "receptionist-session"
    assert result["queue_key"] == queue_key
    assert result["status"] == "queued"
    assert queued is not None
    assert queued.queue_key == queue_key
    assert queued.effect_id == queue_key
    assert queued.target == "receptionist-session"
    assert queued.vm_name == "agent-sandbox-receptionist-vm"
    assert queued.session_id == "receptionist-session"
    assert queued.prompt == "Summarize the latest changes"
    assert queued.resume_thread_id == "thread-keep-1"


def test_run_admin_agent_action_prompt_agent_dispatches_via_enqueue_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setattr(
        service,
        "list_vms",
        MethodType(
            lambda self, **kwargs: [
                VmSummary(
                    vm_name="agent-sandbox-receptionist-vm",
                    power_state="shut off",
                    agent_state="stopped",
                    session_ids=["receptionist-session"],
                )
            ],
            service,
        ),
    )

    build_calls: list[dict[str, object]] = []
    resume_calls: list[dict[str, object]] = []
    original_build = admin_service.EffectProviderFactory.build

    def recording_build(self, effect_definition, *, directory_path=None):
        build_calls.append(
            {
                "effect": effect_definition.model_dump(mode="json"),
                "directory_path": directory_path,
            }
        )
        return original_build(self, effect_definition, directory_path=directory_path)

    def fake_resume_agent(
        self: AdminService,
        target: str,
        prompt: str,
        *,
        model: str | None = None,
        vm_user: str = "agent",
        dispatch_id: str | None = None,
        resume_thread_id: str | None = None,
        resume_mode: str = "new_context",
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ) -> VmActionResult:
        resume_calls.append(
            {
                "target": target,
                "prompt": prompt,
                "model": model,
                "vm_user": vm_user,
                "dispatch_id": dispatch_id,
                "resume_thread_id": resume_thread_id,
                "resume_mode": resume_mode,
                "persist_thread_id": persist_thread_id,
                "mcp_gateway_url": mcp_gateway_url,
            }
        )
        return VmActionResult(
            success=True,
            vm_name="agent-sandbox-receptionist-vm",
            action="resume_issue_agent",
            message="ok",
            thread_id="thread-new-1",
        )

    monkeypatch.setattr(admin_service.EffectProviderFactory, "build", recording_build)
    monkeypatch.setattr(service, "resume_agent", MethodType(fake_resume_agent, service))

    result = service.run_admin_agent_action(
        "receptionist-session",
        "prompt-agent",
        backend="libvirt_vm",
        payload={
            "prompt": "Continue the investigation",
            "resume_thread_id": "thread-keep-2",
        },
    )

    assert len(build_calls) == 1
    assert build_calls[0]["directory_path"] == "root"
    assert build_calls[0]["effect"]["type"] == "effect:agent:enqueue-prompt"
    assert build_calls[0]["effect"]["id"] == "admin-agent-prompt:libvirt_vm:receptionist-session"
    assert build_calls[0]["effect"]["target"] == "receptionist-session"
    assert build_calls[0]["effect"]["session_id"] == "receptionist-session"
    assert build_calls[0]["effect"]["vm_name"] == "agent-sandbox-receptionist-vm"
    assert build_calls[0]["effect"]["prompt"] == "Continue the investigation"
    assert build_calls[0]["effect"]["resume_thread_id"] == "thread-keep-2"
    assert len(resume_calls) == 1
    assert resume_calls[0]["target"] == "receptionist-session"
    assert resume_calls[0]["prompt"] == "Continue the investigation"
    assert resume_calls[0]["model"] is None
    assert resume_calls[0]["vm_user"] == "agent"
    assert str(resume_calls[0]["dispatch_id"]).startswith(
        "dispatch-admin-agent-prompt:libvirt_vm:receptionist-session-"
    )
    assert resume_calls[0]["resume_thread_id"] == "thread-keep-2"
    assert resume_calls[0]["resume_mode"] == "new_context"
    assert resume_calls[0]["mcp_gateway_url"] is None
    assert result["success"] is True
    assert result["queued"] is False
    assert result["backend"] == "libvirt_vm"
    assert result["agent_id"] == "agent-sandbox-receptionist-vm"
    assert result["target_name"] == "agent-sandbox-receptionist-vm"
    assert result["session_id"] == "receptionist-session"
    assert result["thread_id"] == "thread-new-1"
    assert result["status"] == "dispatched"


def test_run_admin_agent_action_prompt_agent_returns_queued_when_dispatch_fails_after_queueing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setattr(
        service,
        "list_vms",
        MethodType(
            lambda self, **kwargs: [
                VmSummary(
                    vm_name="agent-sandbox-receptionist-vm",
                    power_state="shut off",
                    agent_state="stopped",
                    session_ids=["receptionist-session"],
                )
            ],
            service,
        ),
    )

    def failing_resume_agent(
        self: AdminService,
        target: str,
        prompt: str,
        *,
        model: str | None = None,
        vm_user: str = "agent",
        dispatch_id: str | None = None,
        resume_thread_id: str | None = None,
        resume_mode: str = "new_context",
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ) -> VmActionResult:
        del self, target, prompt, model, vm_user, dispatch_id, resume_thread_id
        del resume_mode, persist_thread_id, mcp_gateway_url
        return VmActionResult(
            success=False,
            vm_name="agent-sandbox-receptionist-vm",
            action="resume_issue_agent",
            message="resume failed",
        )

    monkeypatch.setattr(service, "resume_agent", MethodType(failing_resume_agent, service))

    result = service.run_admin_agent_action(
        "receptionist-session",
        "prompt-agent",
        backend="libvirt_vm",
        payload={"prompt": "Continue the investigation"},
    )

    queue_key = "admin-agent-prompt:libvirt_vm:receptionist-session"
    queued = AgentPromptQueueStore(
        tmp_path / "runtime" / "queued-agent-prompts.json"
    ).get(queue_key)

    assert result["success"] is True
    assert result["queued"] is True
    assert result["status"] == "queued"
    assert result["queue_key"] == queue_key
    assert result["target_name"] == "agent-sandbox-receptionist-vm"
    assert result["session_id"] == "receptionist-session"
    assert "queued prompt for retry" in result["dispatch_error"]
    assert queued is not None
    assert queued.prompt == "Continue the investigation"


def test_run_admin_agent_action_prompt_agent_records_direct_container_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_sandbox.effects.providers.agent_enqueue_prompt import (
        AgentEnqueuePromptEffectProvider,
    )
    from agent_sandbox.execution import ExecutionStatusKind

    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        )
    )

    class DirectContainerBackend:
        def __init__(self) -> None:
            self.repository = SimpleNamespace(read_all=lambda: {})
            self.started_specs: list[object] = []

        def prepare(self, spec):
            return spec

        def start(self, spec):
            self.started_specs.append(spec)
            return SimpleNamespace(execution_id=spec.target_name)

    backend = DirectContainerBackend()
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )

    result = service.run_admin_agent_action(
        "container-session",
        "prompt-agent",
        backend="container_worker_vm",
        payload={"prompt": "Continue the container workflow"},
    )

    stores = service._workflow_run_store_paths()
    if service._workflow_uses_sqlite_store(stores):
        execution_store = SqliteExecutionStore(Path(stores["orchestration_store_path"]))
    else:
        execution_store = JsonExecutionStore(Path(stores["effect_execution_store_path"]))
    records = list(execution_store.read_all().values())

    assert result["success"] is True
    assert result["queued"] is False
    assert result["status"] == "dispatched"
    assert result["execution_id"] == backend.started_specs[0].target_name
    assert len(records) == 1
    assert records[0].status == "waiting"
    assert records[0].effect_type == "effect:agent:enqueue-prompt"
    assert records[0].resolved_config["execution_backend"] == "container_worker_vm"
    assert records[0].resolved_config["directory_path"] == "root"
    assert records[0].outputs["output_mode"] == "on_completion"
    assert records[0].outputs["execution_id"] == backend.started_specs[0].target_name
    assert str(records[0].outputs["thread_id"]).startswith("dispatch-")
    assert (
        records[0].outputs["queue_key"]
        == "admin-agent-prompt:container_worker_vm:container-session"
    )

    class ReconcileContainerBackend:
        def inspect(self, execution_id: str) -> SimpleNamespace:
            assert execution_id == backend.started_specs[0].target_name
            return SimpleNamespace(
                status=ExecutionStatusKind.RUNNING,
                message=None,
                metadata={},
            )

        def get_log_snapshot(self, execution_id: str, *, lines: int = 400) -> str:
            assert execution_id == backend.started_specs[0].target_name
            assert lines in {80, 400}
            return "prompt complete\nsession id: thread-real-container-1\n"

    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_prompt.build_execution_backend",
        lambda config, admin_service: ReconcileContainerBackend(),
    )
    worker = SimpleNamespace(
        execution_store=execution_store,
        provider_config_resolver=service.runtime.provider_config,
        provider_factory=SimpleNamespace(admin_service=service),
        config=service.config,
    )

    reconciled = AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker)
    refreshed_record = execution_store.get_execution(records[0].execution_id)
    refreshed_session = service.runtime.session_repository.get_session("container-session")

    assert reconciled == 0
    assert refreshed_record is not None
    assert refreshed_record.outputs["thread_id"] == "thread-real-container-1"
    assert refreshed_session is not None
    assert refreshed_session.metadata["agent_thread_id"] == "thread-real-container-1"


def test_run_admin_agent_action_prompt_agent_returns_queued_when_container_dispatch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        )
    )

    class FailingContainerBackend:
        def __init__(self) -> None:
            self.repository = SimpleNamespace(read_all=lambda: {})

        def prepare(self, spec):
            return spec

        def start(self, spec):
            del spec
            raise RuntimeError("container start failed")

    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: FailingContainerBackend(),
    )

    result = service.run_admin_agent_action(
        "container-session",
        "prompt-agent",
        backend="container_worker_vm",
        payload={"prompt": "Continue the container workflow"},
    )

    queue_key = "admin-agent-prompt:container_worker_vm:container-session"
    queued = AgentPromptQueueStore(
        tmp_path / "runtime" / "queued-agent-prompts.json"
    ).get(queue_key)

    assert result["success"] is True
    assert result["queued"] is True
    assert result["status"] == "queued"
    assert result["queue_key"] == queue_key
    assert result["target_name"] == "container-target"
    assert result["session_id"] == "container-session"
    assert "container dispatch failed" in result["dispatch_error"]
    assert queued is not None
    assert queued.prompt == "Continue the container workflow"


def test_run_admin_agent_action_prompt_agent_queues_busy_container_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        )
    )

    class BusyContainerBackend:
        def __init__(self) -> None:
            self.repository = SimpleNamespace(
                read_all=lambda: {
                    "container-exec-1": SimpleNamespace(
                        execution_id="container-exec-1",
                        session_id="container-session",
                        last_status="running",
                    )
                }
            )

        def inspect(self, execution_id: str) -> SimpleNamespace:
            assert execution_id == "container-exec-1"
            return SimpleNamespace(status="running")

    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: BusyContainerBackend(),
    )

    result = service.run_admin_agent_action(
        "container-session",
        "prompt-agent",
        backend="container_worker_vm",
        payload={
            "prompt": "Re-run the container workflow",
            "resume_thread_id": "thread-container-1",
        },
    )

    queue_key = "admin-agent-prompt:container_worker_vm:container-session"
    queued = AgentPromptQueueStore(
        tmp_path / "runtime" / "queued-agent-prompts.json"
    ).get(queue_key)

    assert result["success"] is True
    assert result["queued"] is True
    assert result["backend"] == "container_worker_vm"
    assert result["agent_id"] == "container-session"
    assert result["target_name"] == "container-target"
    assert result["session_id"] == "container-session"
    assert result["queue_key"] == queue_key
    assert result["status"] == "queued"
    assert queued is not None
    assert queued.queue_key == queue_key
    assert queued.effect_id == queue_key
    assert queued.target == "container-session"
    assert queued.vm_name == "container-target"
    assert queued.session_id == "container-session"
    assert queued.prompt == "Re-run the container workflow"
    assert queued.resume_thread_id == "thread-container-1"


def test_run_admin_agent_action_prompt_agent_rejects_blank_prompt(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with pytest.raises(ValueError, match="prompt-agent requires a non-empty prompt"):
        service.run_admin_agent_action(
            "receptionist-session",
            "prompt-agent",
            backend="libvirt_vm",
            payload={"prompt": "   "},
        )


def test_run_admin_agent_action_prompt_agent_supports_vm_without_linked_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(
        service,
        "list_vms",
        MethodType(
            lambda self, **kwargs: [
                VmSummary(
                    vm_name="standalone-vm",
                    power_state="running",
                    agent_state="running",
                    session_ids=[],
                )
            ],
            service,
        ),
    )

    result = service.run_admin_agent_action(
        "standalone-vm",
        "prompt-agent",
        backend="libvirt_vm",
        payload={"prompt": "Check whether you are idle"},
    )

    queue_key = "admin-agent-prompt:libvirt_vm:standalone-vm"
    queued = AgentPromptQueueStore(
        tmp_path / "runtime" / "queued-agent-prompts.json"
    ).get(queue_key)

    assert result["success"] is True
    assert result["queued"] is True
    assert result["agent_id"] == "standalone-vm"
    assert result["target_name"] == "standalone-vm"
    assert result["session_id"] is None
    assert result["queue_key"] == queue_key
    assert queued is not None
    assert queued.target == "standalone-vm"
    assert queued.vm_name == "standalone-vm"
    assert queued.session_id is None
    assert queued.prompt == "Check whether you are idle"


def test_run_admin_agent_action_prompt_agent_requires_resolvable_container_session(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(
        ValueError, match="Unknown container worker agent target: missing-session"
    ):
        service.run_admin_agent_action(
            "missing-session",
            "prompt-agent",
            backend="container_worker_vm",
            payload={"prompt": "hello"},
        )


def test_run_admin_agent_action_prompt_agent_rejects_ambiguous_container_session(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session-a",
            vm_provider="container_worker_vm",
            vm_provider_id="shared-target",
            allowed_tools=[],
            expires_at=expires_at,
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "shared-target",
            },
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session-b",
            vm_provider="container_worker_vm",
            vm_provider_id="shared-target",
            allowed_tools=[],
            expires_at=expires_at,
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "shared-target",
            },
        )
    )

    with pytest.raises(ValueError, match="multiple sessions"):
        service.run_admin_agent_action(
            "shared-target",
            "prompt-agent",
            backend="container_worker_vm",
            payload={"prompt": "hello"},
        )


def test_teams_auth_list_channels_resolves_exact_channel_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "example-industry")
    assert created.ok is True and created.data is not None
    directory_path = created.data.details.directory.path
    write_result = service.write_directory_domain_config(
        directory_path,
        "providers",
        {
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": "example-teams",
            "teams_delegated_client_id": "client-123",
            "teams_delegated_tenant_id": "tenant-123",
        },
    )
    assert write_result.ok is True

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer access-token"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "19:truman@thread.skype",
                        "displayName": "Truman",
                        "membershipType": "standard",
                        "webUrl": "https://teams.example/truman",
                    },
                    {
                        "id": "19:other@thread.skype",
                        "displayName": "General",
                        "membershipType": "standard",
                    },
                ]
            },
            request=request,
        )

    real_client = httpx.Client

    def fake_client(*args: object, **kwargs: object) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(admin_service.httpx, "Client", fake_client)
    monkeypatch.setattr(
        admin_service.TeamsDeviceCodeTokenProvider,
        "get_bearer_token",
        lambda self, force_refresh=False: "access-token",
    )

    result = service.teams_auth_list_channels(
        directory=directory_path,
        team_id="c85e4de5-e0d7-4240-8302-8bc944f2360d",
        channel_name="Truman",
    )

    assert result["directory_path"] == directory_path
    assert result["match_count"] == 1
    assert result["channels"] == [
        {
            "team_id": "c85e4de5-e0d7-4240-8302-8bc944f2360d",
            "channel_id": "19:truman@thread.skype",
            "display_name": "Truman",
            "membership_type": "standard",
            "web_url": "https://teams.example/truman",
            "allowlist_entry": "c85e4de5-e0d7-4240-8302-8bc944f2360d/19:truman@thread.skype",
        }
    ]
    assert requests[0].url.path == "/v1.0/teams/c85e4de5-e0d7-4240-8302-8bc944f2360d/channels"


def test_teams_auth_list_channels_can_list_all_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "example-industry")
    assert created.ok is True and created.data is not None
    directory_path = created.data.details.directory.path
    write_result = service.write_directory_domain_config(
        directory_path,
        "providers",
        {
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": "example-teams",
            "teams_delegated_client_id": "client-123",
            "teams_delegated_tenant_id": "tenant-123",
        },
    )
    assert write_result.ok is True

    real_client = httpx.Client

    def fake_client(*args: object, **kwargs: object) -> httpx.Client:
        return real_client(
            *args,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "value": [
                            {"id": "19:a@thread.skype", "displayName": "General"},
                            {"id": "19:b@thread.skype", "displayName": "Truman"},
                        ]
                    },
                    request=request,
                )
            ),
            **kwargs,
        )

    monkeypatch.setattr(admin_service.httpx, "Client", fake_client)
    monkeypatch.setattr(
        admin_service.TeamsDeviceCodeTokenProvider,
        "get_bearer_token",
        lambda self, force_refresh=False: "access-token",
    )

    result = service.teams_auth_list_channels(
        directory=directory_path,
        team_id="team-123",
        include_all=True,
    )

    assert result["channel_name"] is None
    assert result["match_count"] == 2
    assert [item["display_name"] for item in result["channels"]] == ["General", "Truman"]


def test_create_local_issue_reuses_scoped_id_within_directory(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    source_path = tmp_path / "team-issue.yaml"
    source_path.write_text(
        "issue_number: 7\ntitle: Team issue\nbody: First body\n", encoding="utf-8"
    )
    first = service.create_local_issue(yaml_path=str(source_path), directory_path=team_path)

    source_path.write_text(
        "issue_number: 7\ntitle: Team issue\nbody: Updated body\n", encoding="utf-8"
    )
    second = service.create_local_issue(yaml_path=str(source_path), directory_path=team_path)

    assert first.issue_id == "root/team::local::7"
    assert second.issue_id == first.issue_id
    assert second.body == "Updated body"

    records = service.local_issue_store.read_all()
    assert list(records) == ["root/team::local::7"]


def test_create_local_issue_serializes_concurrent_issue_number_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path)

    def slow_next_issue_number(self: AdminService) -> str:
        current = 0
        if self.local_issue_counter_path.exists():
            try:
                current = int(
                    self.local_issue_counter_path.read_text(encoding="utf-8").strip() or "0"
                )
            except ValueError:
                current = 0
        time.sleep(0.05)
        current += 1
        self.local_issue_counter_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_issue_counter_path.write_text(f"{current}\n", encoding="utf-8")
        return str(current)

    monkeypatch.setattr(AdminService, "_next_local_issue_number", slow_next_issue_number)
    services = [AdminService(config_path) for _ in range(6)]

    def create_issue(args: tuple[AdminService, int]) -> str:
        service, index = args
        issue = service.create_local_issue(title=f"Issue {index}", body="parallel")
        return issue.issue_number

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        issue_numbers = list(pool.map(create_issue, zip(services, range(6), strict=True)))

    assert sorted(issue_numbers) == ["1", "2", "3", "4", "5", "6"]
    payload = json.loads((tmp_path / "runtime" / "local-issues.json").read_text(encoding="utf-8"))
    assert sorted(payload) == [f"root::local::{number}" for number in range(1, 7)]
    for number in range(1, 7):
        assert (tmp_path / "runtime" / "issues" / f"{number}.yaml").exists()


def test_create_local_issue_imports_block_scalar_body(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    source_path = tmp_path / "block-issue.yaml"
    source_path.write_text(
        "issue_number: 7\ntitle: Block body\nbody: |\n  line one\n  line two\n",
        encoding="utf-8",
    )

    issue = service.create_local_issue(yaml_path=str(source_path))

    assert issue.body == "line one\nline two"


def test_create_local_issue_imports_block_scalar_title(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    source_path = tmp_path / "block-title-issue.yaml"
    source_path.write_text(
        "issue_number: 8\ntitle: |\n  line one\n  line two\nbody: ok\n",
        encoding="utf-8",
    )

    issue = service.create_local_issue(yaml_path=str(source_path))

    assert issue.title == "line one\nline two"


def test_update_local_issue_updates_store_and_yaml(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Old title", body="Old body", repo_source="/old/repo")

    updated = service.update_local_issue(
        issue.issue_number,
        title="New title",
        body="New body",
        state="In Progress",
        repo_source="/new/repo",
    )

    assert updated.title == "New title"
    assert updated.body == "New body"
    assert updated.state == "In Progress"
    assert updated.repo_source == "/new/repo"
    yaml_text = Path(updated.yaml_path).read_text(encoding="utf-8")
    assert "title: New title" in yaml_text
    assert "body: New body" in yaml_text
    assert "state: In Progress" in yaml_text


def test_update_local_issue_rejects_proxy_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(yaml_path=str(_write_proxy_issue_yaml(tmp_path)))

    with pytest.raises(ValueError, match="Only local manual issues"):
        service.update_local_issue(
            issue.issue_number,
            title="Ignore",
            body="Ignore",
            state="New",
            repo_source=None,
        )


def test_list_vms_collects_session_and_agent_state(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-123-session",
            vm_id="vm-123",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    monkeypatch.setattr(
        "agent_sandbox.admin.service._virsh_lines", lambda args: ["vm-123", "vm-999"]
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field",
        lambda vm_name, field_name: "running" if vm_name == "vm-123" else "shut off",
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip",
        lambda vm_name: "192.168.122.10" if vm_name == "vm-123" else None,
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._probe_agent_state",
        lambda vm_name, vm_ip, vm_user: ("running", ["repo-one"], datetime.now(UTC), None, 4321),
    )

    summaries = service.list_vms(include_inactive=True)

    assert [summary.vm_name for summary in summaries] == ["vm-123", "vm-999"]
    assert summaries[0].issue_number == "123"
    assert summaries[0].session_ids == ["issue-123-session"]
    assert summaries[0].agent_state == "running"
    assert summaries[0].agent_pid == 4321
    assert summaries[0].agent_repo_names == ["repo-one"]
    assert summaries[1].agent_state == "inactive_vm"
    assert summaries[1].agent_pid is None


def test_list_vms_marks_inactive_vm_disabled_from_host_control_state(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.agent_control_store.set_status(
        vm_name="vm-999", status="disabled", phase="operator-disable"
    )

    monkeypatch.setattr("agent_sandbox.admin.service._virsh_lines", lambda args: ["vm-999"])
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: "shut off"
    )

    summaries = service.list_vms(include_inactive=True)

    assert summaries[0].agent_state == "disabled"


def test_run_virsh_prefers_explicit_system_uri_before_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd == ["virsh", "-c", "qemu:///system", "list", "--all", "--name"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="vm-issue-14724\n", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)

    result = admin_service._run_virsh(["list", "--all", "--name"])

    assert result.returncode == 0
    assert result.stdout == "vm-issue-14724\n"
    assert calls == [["virsh", "-c", "qemu:///system", "list", "--all", "--name"]]


def test_run_virsh_falls_back_to_sudo_with_explicit_system_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd == ["virsh", "-c", "qemu:///system", "dominfo", "vm-issue-14724"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="permission denied")
        if cmd == ["sudo", "-n", "virsh", "-c", "qemu:///system", "dominfo", "vm-issue-14724"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="State: running\n", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)

    result = admin_service._run_virsh(["dominfo", "vm-issue-14724"])

    assert result.returncode == 0
    assert "running" in result.stdout
    assert calls == [
        ["virsh", "-c", "qemu:///system", "dominfo", "vm-issue-14724"],
        ["sudo", "-n", "virsh", "-c", "qemu:///system", "dominfo", "vm-issue-14724"],
    ]


def test_resume_agent_accepts_vm_name_without_vm_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="agent-sandbox-receptionist-vm-20260330T112127Z-1353036",
            vm_id="agent-sandbox-receptionist-vm",
            model="gpt-5.4",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    captured: dict[str, object] = {}
    watcher: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(
            cmd, 0, stdout="thread_id=thread-abc-123\nok\n", stderr=""
        )

    class _PopenStub:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            watcher["cmd"] = cmd
            watcher["kwargs"] = kwargs

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)
    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.Popen", _PopenStub)

    result = service.resume_agent("agent-sandbox-receptionist-vm", "Resume now")

    assert result.success is True
    assert result.thread_id == "thread-abc-123"
    assert captured["cmd"] == [
        str(Path(__file__).resolve().parents[1] / "scripts" / "resume_issue_agent.sh"),
        "--prompt",
        "Resume now",
        "--vm-name",
        "agent-sandbox-receptionist-vm",
        "--vm-user",
        "agent",
        "--host-control-state-path",
        str(service.config.host_agent_control_state_path),
        "--resume-mode",
        "new_context",
    ]
    assert captured["env"]["AGENT_MODEL"] == "gpt-5.4"
    assert captured["timeout"] == 120.0
    assert watcher == {}


def test_resume_agent_passes_explicit_resume_thread_id(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            expires_at=datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )

    captured: dict[str, object] = {}
    watcher: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="thread_id=thread-keep-1\nok\n", stderr=""
        )

    class _PopenStub:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            watcher["cmd"] = cmd
            watcher["kwargs"] = kwargs

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)
    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.Popen", _PopenStub)

    result = service.resume_agent(
        "agent-sandbox-receptionist-vm",
        "Resume now",
        dispatch_id="dispatch-keep-1",
        resume_thread_id="thread-keep-1",
    )

    assert result.success is True
    assert result.thread_id == "thread-keep-1"
    assert "--resume-thread-id" in captured["cmd"]
    assert "thread-keep-1" in captured["cmd"]
    assert "--dispatch-id" in captured["cmd"]
    assert "dispatch-keep-1" in captured["cmd"]
    assert watcher["cmd"][0:6] == [
        admin_service.sys.executable,
        "-m",
        "agent_sandbox.daemons.agent_completion_watcher",
        "--runtime-dir",
        str(service.config.runtime_dir),
        "--vm-name",
    ]
    assert "--dispatch-id" in watcher["cmd"]
    assert "dispatch-keep-1" in watcher["cmd"]
    assert "--event-queue-store-path" in watcher["cmd"]
    assert "--orchestration-store-backend" in watcher["cmd"]
    backend_index = watcher["cmd"].index("--orchestration-store-backend")
    assert watcher["cmd"][backend_index + 1] == "sqlite"
    assert "--orchestration-store-path" in watcher["cmd"]
    store_index = watcher["cmd"].index("--orchestration-store-path")
    assert watcher["cmd"][store_index + 1] == str(
        service.config.runtime_dir / "orchestration.db"
    )
    assert "--session-id" in watcher["cmd"]
    assert "receptionist-session" in watcher["cmd"]


def test_resume_agent_passes_mcp_gateway_url(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            expires_at=datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )
    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="thread_id=thread-mcp-1\nok\n", stderr=""
        )

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)

    result = service.resume_agent(
        "agent-sandbox-receptionist-vm",
        "Resume now",
        mcp_gateway_url=(
            "http://gateway.test/mcp?"
            "session_id=receptionist-session&mcp_tool_approval_lease_id=lease-1"
        ),
    )

    assert result.success is True
    assert "--mcp-gateway-url" in captured["cmd"]
    gateway_url_index = captured["cmd"].index("--mcp-gateway-url")
    assert captured["cmd"][gateway_url_index + 1] == (
        "http://gateway.test/mcp?"
        "session_id=receptionist-session&mcp_tool_approval_lease_id=lease-1"
    )


def test_resume_agent_does_not_reuse_stored_session_thread_id(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            expires_at=datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
            allowed_tools=[],
            model="gpt-5.4",
            metadata={"agent_thread_id": "thread-stored-1"},
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)

    result = service.resume_agent("agent-sandbox-receptionist-vm", "Resume now")

    assert result.success is True
    assert result.thread_id is None
    assert "--resume-thread-id" not in captured["cmd"]
    assert "--resume-mode" in captured["cmd"]
    assert "new_context" in captured["cmd"]


def test_resume_agent_ignores_synthetic_chat_thread_id(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-session",
            vm_id="assistant-shared",
            expires_at=datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
            allowed_tools=[],
            model="gpt-5.4",
            metadata={"agent_thread_id": "chat-thread-alice"},
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="thread_id=thread-real-1\nok\n", stderr=""
        )

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)

    result = service.resume_agent("assistant-shared", "Resume now")

    assert result.success is True
    assert result.thread_id == "thread-real-1"
    assert "--resume-thread-id" not in captured["cmd"]
    assert "--resume-mode" in captured["cmd"]
    assert "new_context" in captured["cmd"]


def test_resume_agent_persists_returned_thread_id_to_session_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            expires_at=datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="thread_id=thread-fresh-1\nok\n", stderr=""
        )

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)

    result = service.resume_agent("agent-sandbox-receptionist-vm", "Resume now")

    assert result.success is True
    assert result.thread_id == "thread-fresh-1"
    stored = service.runtime.session_repository.get_session("receptionist-session")
    assert stored is not None
    assert stored.metadata["agent_thread_id"] == "thread-fresh-1"


def test_resume_agent_supports_same_context_mode(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            expires_at=datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)

    result = service.resume_agent(
        "agent-sandbox-receptionist-vm",
        "Resume now",
        resume_mode="same_context",
    )

    assert result.success is True
    assert "--resume-mode" in captured["cmd"]
    assert "same_context" in captured["cmd"]


def test_resume_agent_uses_explicit_model_override(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            expires_at=datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
            allowed_tools=[],
            model="gpt-5.2-codex",
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            cmd, 0, stdout="thread_id=thread-model-1\nok\n", stderr=""
        )

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)

    result = service.resume_agent(
        "agent-sandbox-receptionist-vm",
        "Resume now",
        model="gpt-5.4",
    )

    assert result.success is True
    assert result.thread_id == "thread-model-1"
    assert captured["env"]["AGENT_MODEL"] == "gpt-5.4"


def test_resume_agent_times_out_cleanly(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="agent-sandbox-receptionist-vm",
            expires_at=datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_RESUME_TIMEOUT_SECONDS", "7")

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", fake_run_subprocess)

    result = service.resume_agent("agent-sandbox-receptionist-vm", "Resume now")

    assert result.success is False
    assert result.action == "resume_issue_agent"
    assert result.message == "resume_issue_agent.sh timed out after 7s"


def test_probe_agent_state_accepts_codex_resume_state(tmp_path: Path, monkeypatch) -> None:
    write_config(tmp_path)
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_private_key_path", lambda: Path("/tmp/test-key")
    )

    captured: dict[str, object] = {}

    def _fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout='{"state":"running","repos":["codex-heartbeat"],"updated_at":"2026-03-29T12:06:53+00:00"}',
            stderr="",
        )

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", _fake_run_subprocess)

    state, repos, updated_at, dispatch_id, pid = admin_service._probe_agent_state(
        "bug-finder-vm", "192.168.122.11", "agent"
    )

    assert state == "running"
    assert repos == ["codex-heartbeat"]
    assert updated_at == datetime(2026, 3, 29, 12, 6, 53, tzinfo=UTC)
    assert dispatch_id is None
    assert pid is None
    remote_script = str(captured["input"])
    assert 'codex_state_path="${LOG_DIR}/codex-resume-last.state"' in remote_script
    assert 'active_entries+=("codex-heartbeat")' in remote_script


def test_get_agent_log_snapshot_accepts_codex_heartbeat_log(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: "running"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.122.11"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_private_key_path", lambda: Path("/tmp/test-key")
    )

    captured: dict[str, object] = {}

    def _fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="Repo: codex-heartbeat\nLog: /var/log/agent-sandbox/agent-runs/codex-heartbeat-run.log\n",
            stderr="",
        )

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", _fake_run_subprocess)

    output = service.get_agent_log_snapshot("bug-finder-vm")

    assert "Repo: codex-heartbeat" in output
    assert "codex-heartbeat-run.log" in output
    remote_script = str(captured["input"])
    assert 'candidates+=("codex-heartbeat")' in remote_script
    assert 'LOG_PATH="${RUN_LOG_DIR}/codex-heartbeat-run.log"' in remote_script
    assert 'LEGACY_LOG_PATH="${LOG_DIR}/codex-heartbeat-run.log"' in remote_script
    assert "agent-admin@192.168.122.11" in " ".join(str(part) for part in captured["cmd"])


def test_get_agent_log_snapshot_falls_back_to_legacy_agent_user(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: "running"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.122.11"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_private_key_path", lambda: Path("/tmp/test-key")
    )

    calls: list[list[str]] = []

    def _fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if "agent-admin@192.168.122.11" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=255,
                stdout="",
                stderr="agent-admin@192.168.122.11: Permission denied (publickey).",
            )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="Repo: ExampleIndustryAPP\nLog: /home/agent/.agent-sandbox/agent-ExampleIndustryAPP.log\nlegacy line\n",
            stderr="",
        )

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", _fake_run_subprocess)

    output = service.get_agent_log_snapshot("bug-finder-vm", repo_name="ExampleIndustryAPP")

    assert "legacy line" in output
    assert any("agent-admin@192.168.122.11" in cmd for cmd in calls)
    assert any("agent@192.168.122.11" in cmd for cmd in calls)


def test_get_agent_log_snapshot_returns_unavailable_when_ssh_times_out(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: "running"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.122.11"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_private_key_path", lambda: Path("/tmp/test-key")
    )

    calls: list[list[str]] = []

    def _fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, timeout=20.0)

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", _fake_run_subprocess)

    output = service.get_agent_log_snapshot("bug-finder-vm", repo_name="codex-heartbeat")

    assert output == (
        "Agent log unavailable: agent@192.168.122.11: log read timed out after 20 seconds"
    )
    assert any("agent-admin@192.168.122.11" in cmd for cmd in calls)
    assert any("agent@192.168.122.11" in cmd for cmd in calls)


def test_get_admin_agent_log_page_pages_libvirt_tail(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))

    def fake_snapshot(
        target: str,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        assert target == "bug-finder-vm"
        assert lines == 4
        assert repo_name == "repo-a"
        assert vm_user == "agent"
        return "one\ntwo\nthree\nfour\n"

    monkeypatch.setattr(service, "get_agent_log_snapshot", fake_snapshot)

    page = service.get_admin_agent_log_page(
        "bug-finder-vm",
        backend="libvirt_vm",
        limit=2,
        offset=2,
        repo_name="repo-a",
    )

    assert page["text"] == "one\ntwo\n"
    assert page["returned_lines"] == 2
    assert page["has_more_before"] is True


def test_get_admin_agent_log_page_container_session_pages_across_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_provider="container_worker_vm",
            vm_provider_id="target-1",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-1",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-old",
            session_id="session-1",
            worker_job_id="job-old",
            worker_url="http://worker.test",
            target_name="target-1",
            started_at=datetime(2026, 5, 18, 7, 0, tzinfo=UTC),
            last_status="removed",
            directory_path="root",
        )
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-new",
            session_id="session-1",
            worker_job_id="job-new",
            worker_url="http://worker.test",
            target_name="target-1",
            started_at=datetime(2026, 5, 18, 8, 0, tzinfo=UTC),
            last_status="removed",
            directory_path="root",
        )
    )

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, str | None]] = []

        def get_log_snapshot(
            self,
            target: str,
            *,
            lines: int = 50,
            mode: str | None = None,
        ) -> str:
            self.calls.append((target, lines, mode))
            if target == "exec-new":
                return "new line 1\nnew line 2\n"
            return "old line 1\nold line 2\n"

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service),
    )

    latest_page = service.get_admin_agent_log_page(
        "session-1",
        backend="container_worker_vm",
        limit=50,
    )

    assert "execution=exec-new" in latest_page["text"]
    assert "new line 2" in latest_page["text"]
    assert "exec-old" not in latest_page["text"]
    assert latest_page["has_more_before"] is True
    assert latest_page["total_lines"] is None
    assert fake_backend.calls == [("exec-new", 0, "augmented")]

    older_page = service.get_admin_agent_log_page(
        "session-1",
        backend="container_worker_vm",
        limit=10,
        offset=int(latest_page["returned_lines"]),
    )

    assert "execution=exec-old" in older_page["text"]
    assert "old line 2" in older_page["text"]
    assert "exec-new" not in older_page["text"]
    assert older_page["has_more_before"] is False
    assert fake_backend.calls[-2:] == [
        ("exec-new", 0, "augmented"),
        ("exec-old", 0, "raw"),
    ]


def test_list_sessions_filters_active_and_expired(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-123-active",
            vm_id="vm-123",
            model="gpt-5.4",
            allowed_tools=[],
            created_at=datetime(2026, 3, 25, 9, 0, tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-123-expired",
            vm_id="vm-123",
            allowed_tools=[],
            created_at=datetime(2026, 3, 25, 8, 0, tzinfo=UTC),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        ),
    )

    active = service.list_sessions()
    expired = service.list_sessions(expired_only=True)
    all_sessions = service.list_sessions(include_expired=True)

    assert [summary.session_id for summary in active] == ["issue-123-active"]
    assert [summary.session_id for summary in expired] == ["issue-123-expired"]
    assert [summary.session_id for summary in all_sessions] == [
        "issue-123-expired",
        "issue-123-active",
    ]
    assert active[0].issue_number == "123"
    assert active[0].model == "gpt-5.4"
    assert active[0].is_expired is False
    assert expired[0].is_expired is True


@pytest.mark.parametrize(
    "session_id, record_status, expected_state, expired, cleanup_supported, recovery_supported",
    [
        (
            "container-running",
            "running",
            ContainerSessionLifecycleState.RUNNING,
            False,
            False,
            False,
        ),
        (
            "container-stopped",
            "removed",
            ContainerSessionLifecycleState.STOPPED,
            False,
            True,
            False,
        ),
        (
            "container-failed",
            "failed",
            ContainerSessionLifecycleState.FAILED,
            False,
            True,
            False,
        ),
        (
            "container-abandoned",
            None,
            ContainerSessionLifecycleState.ABANDONED,
            True,
            True,
            True,
        ),
        (
            "container-cleanup-failed",
            "removed",
            ContainerSessionLifecycleState.CLEANUP_FAILED,
            False,
            True,
            False,
        ),
        (
            "container-cleaned-up",
            None,
            ContainerSessionLifecycleState.CLEANED_UP,
            False,
            False,
            False,
        ),
    ],
)
def test_list_sessions_computes_container_lifecycle_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_id: str,
    record_status: str | None,
    expected_state: ContainerSessionLifecycleState,
    expired: bool,
    cleanup_supported: bool,
    recovery_supported: bool,
) -> None:
    service = AdminService(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(admin_service, "_utc_now", lambda: now)
    monkeypatch.setattr(
        "agent_sandbox.execution.container_worker_repository.now_utc",
        lambda: now,
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="libvirt-session",
            vm_id="vm-libvirt",
            allowed_tools=[],
            expires_at=now + timedelta(hours=1),
        ),
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id=session_id,
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            created_at=now - timedelta(hours=2),
            expires_at=now - timedelta(minutes=5) if expired else now + timedelta(hours=2),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        ),
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    if record_status is not None:
        record_metadata: dict[str, object] = {"image": "runner:latest"}
        if expected_state == ContainerSessionLifecycleState.CLEANUP_FAILED:
            record_metadata = {
                "cleanup_status": "cleanup_failed",
                "cleanup_attempted_at": (now - timedelta(minutes=20)).isoformat(),
                "cleanup_error": "cleanup exploded",
            }
        repository.put(
            ContainerWorkerExecutionRecord(
                execution_id=f"{session_id}-execution",
                session_id=session_id,
                worker_job_id=f"{session_id}-job",
                worker_url="http://worker.test",
                target_name="container-target",
                last_status=record_status,
                started_at=now - timedelta(hours=1),
                finished_at=(
                    now - timedelta(minutes=30)
                    if record_status in {"removed", "failed", "stopped", "exited"}
                    else None
                ),
                metadata=record_metadata,
            )
        )
    elif expected_state == ContainerSessionLifecycleState.CLEANED_UP:
        service.runtime.session_repository.upsert_session(
            SessionManifest(
                session_id=session_id,
                vm_provider="container_worker_vm",
                vm_provider_id="container-target",
                allowed_tools=[],
                created_at=now - timedelta(hours=2),
                expires_at=now + timedelta(hours=2),
                metadata={
                    "execution_backend": "container_worker_vm",
                    "target_name": "container-target",
                    "container_lifecycle": {
                        "state": "cleaned_up",
                        "cleanup_status": "cleanup_succeeded",
                        "cleanup_completed_at": (now - timedelta(minutes=10)).isoformat(),
                    },
                },
            ),
        )

    summaries = {item.session_id: item for item in service.list_sessions(include_expired=True)}
    lifecycle = summaries[session_id].container_lifecycle
    assert lifecycle is not None
    assert lifecycle.state == expected_state
    assert lifecycle.actions.cleanup.supported is cleanup_supported
    assert lifecycle.actions.recovery.supported is recovery_supported
    assert summaries["libvirt-session"].container_lifecycle is None
    if record_status == "running":
        assert lifecycle.last_heartbeat_at is not None
        assert lifecycle.last_completion_at is None
        assert lifecycle.last_signal_at == lifecycle.last_heartbeat_at
    elif expected_state in {
        ContainerSessionLifecycleState.STOPPED,
        ContainerSessionLifecycleState.FAILED,
    }:
        assert lifecycle.last_completion_at is not None
        assert lifecycle.last_signal_at == lifecycle.last_completion_at
    elif expected_state == ContainerSessionLifecycleState.CLEANUP_FAILED:
        assert lifecycle.cleanup_error == "cleanup exploded"
        assert lifecycle.cleanup_attempted_at is not None
    elif expected_state == ContainerSessionLifecycleState.CLEANED_UP:
        assert lifecycle.cleanup_completed_at is not None
        assert lifecycle.last_signal_at is None
    elif expected_state == ContainerSessionLifecycleState.ABANDONED:
        assert lifecycle.last_heartbeat_at is None
        assert lifecycle.last_completion_at is None
        assert lifecycle.actions.recovery.kind == "renew_session"


def test_directory_filtered_lists_use_direct_owned_items_only(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    root_dir = service.create_directory_child("root", "team")
    assert root_dir.ok is True and root_dir.data is not None
    team_path = root_dir.data.details.directory.path

    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="team-session",
            vm_id="vm-101",
            directory_path=team_path,
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"issue_number": "101"},
        ),
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="root-session",
            vm_id="vm-root",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"issue_number": "1"},
        ),
    )
    service.create_local_issue(title="Root issue", body="a", directory_path="root")
    service.create_local_issue(title="Team issue", body="b", directory_path=team_path)

    monkeypatch.setattr(
        "agent_sandbox.admin.service._virsh_lines", lambda args: ["vm-101", "vm-root", "vm-free"]
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: "shut off"
    )

    team_sessions = service.list_sessions(
        include_expired=True, directory_path=team_path, direct_only=True
    )
    team_issues = service.list_local_issues(directory_path=team_path, direct_only=True)
    team_vms = service.list_vms(include_inactive=True, directory_path=team_path, direct_only=True)
    root_vms = service.list_vms(include_inactive=True, directory_path="root", direct_only=True)

    assert [summary.session_id for summary in team_sessions] == ["team-session"]
    assert [issue.title for issue in team_issues] == ["Team issue"]
    assert [summary.vm_name for summary in team_vms] == ["vm-101"]
    assert {summary.vm_name for summary in root_vms} == {"vm-root", "vm-free"}


def test_list_issues_sorts_issue_numbers_numerically(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    low_path = tmp_path / "issue-2.yaml"
    low_path.write_text("issue_number: 2\ntitle: Low issue\nbody: body\n", encoding="utf-8")
    high_path = tmp_path / "issue-10.yaml"
    high_path.write_text("issue_number: 10\ntitle: High issue\nbody: body\n", encoding="utf-8")

    service.create_local_issue(yaml_path=str(high_path))
    service.create_local_issue(yaml_path=str(low_path))

    issues = service.list_issues()

    assert [issue.issue_number for issue in issues] == ["2", "10"]


def test_list_local_issues_supports_state_filters(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    active_issue = service.create_local_issue(title="Active", body="body")
    completed_issue = service.create_local_issue(title="Completed", body="body")
    closed_issue = service.create_local_issue(title="Closed", body="body")

    service.update_issue(active_issue.issue_number, state="In Progress")
    service.update_issue(completed_issue.issue_number, state="Completed")
    service.update_issue(closed_issue.issue_number, state="Closed")

    active_only = service.list_local_issues(exclude_states={"Completed", "Closed"})
    completed_only = service.list_local_issues(states={"Completed"})

    assert [issue.title for issue in active_only] == ["Active"]
    assert [issue.title for issue in completed_only] == ["Completed"]


def test_list_approval_requests_filters_health_and_redacts_payload(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="team-session",
            vm_id="vm-42",
            directory_path=team_path,
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.approvals.put(
        "approval-pending",
        ApprovalRequest(
            request_id="approval-pending",
            session_id="team-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="abc",
            human_summary="send email",
            intended_effect="Send the approved email payload to the recipient.",
            payload={"token": "secret", "body": "ok"},
            directory_path=team_path,
            vm_id="vm-42",
            notification_status=ApprovalNotificationStatus.PARTIAL,
            notification_target_ids=["ops-approvals"],
            notification_attempted_at=datetime.now(UTC),
            notification_error="notification relay unavailable",
            notification_deliveries=[
                ApprovalNotificationDeliveryRecord(
                    target_id="ops-approvals",
                    status=ApprovalNotificationDeliveryStatus.FAILED,
                    provider="teams",
                    message_id="msg-123",
                    result_summary="status=failed",
                    error="notification relay unavailable",
                )
            ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.approvals.put(
        "approval-denied",
        ApprovalRequest(
            request_id="approval-denied",
            session_id="team-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="def",
            human_summary="already denied",
            payload={"body": "blocked"},
            directory_path=team_path,
            vm_id="vm-42",
            status=ApprovalStatus.DENIED,
            approver="ops",
            decision_at=datetime.now(UTC),
            decision_reason="unsafe",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    pending = service.list_approval_requests(
        statuses={ApprovalStatus.PENDING},
        directory_path=team_path,
        direct_only=True,
        tool_name="email_send_approved",
    )
    health = service.approval_queue_health(directory_path=team_path, direct_only=True)
    review = service.get_approval_review("approval-pending")

    assert [request.request_id for request in pending] == ["approval-pending"]
    assert health.total == 2
    assert health.pending == 1
    assert health.denied == 1
    assert review["request"]["intended_effect"] == "Send the approved email payload to the recipient."
    assert review["redacted_payload"]["token"] == "<redacted>"
    assert review["request"]["payload"]["token"] == "<redacted>"
    assert review["request"]["notification_status"] == "partial"
    assert review["request"]["notification_target_ids"] == ["ops-approvals"]
    assert review["request"]["notification_error"] == "notification relay unavailable"
    assert review["request"]["notification_deliveries"][0]["target_id"] == "ops-approvals"
    assert review["request"]["notification_deliveries"][0]["status"] == "failed"
    assert review["request"]["notification_deliveries"][0]["message_id"] == "msg-123"


def test_decide_approval_request_records_reason_history_and_audit(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.approvals.put(
        "approval-1",
        ApprovalRequest(
            request_id="approval-1",
            session_id="session-1",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="abc",
            human_summary="send email",
            payload={"body": "hello"},
            vm_id="vm-1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    decided = service.decide_approval_request(
        "approval-1", approver="alice", approve=False, reason="unsafe"
    )
    audit_lines = (tmp_path / "runtime" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    audit_payload = json.loads(audit_lines[-1])

    assert decided.status == ApprovalStatus.DENIED
    assert decided.decision_reason == "unsafe"
    assert decided.decision_history[0].reason == "unsafe"
    assert audit_payload["event_type"] == "approval_decision"
    assert audit_payload["approval_request_id"] == "approval-1"
    assert audit_payload["args"]["request_id"] == "approval-1"
    assert audit_payload["reason"] == "unsafe"
    assert audit_payload["result"]["status"] == "denied"
    assert audit_payload["result"]["tool_name"] == "email_send_approved"
    assert audit_payload["result"]["payload_digest"] == "abc"
    assert audit_payload["result"]["request_id"] == "approval-1"


def test_get_approval_review_includes_lifecycle_audit_entries_by_request_id_and_digest(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.approvals.put(
        "approval-1",
        ApprovalRequest(
            request_id="approval-1",
            session_id="session-1",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="digest-1",
            human_summary="send email",
            payload={"body": "hello"},
            vm_id="vm-1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.config.audit_log_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "tool_call",
                        "session_id": "session-1",
                        "vm_id": "vm-1",
                        "tool_name": "email_send_approved",
                        "approval_request_id": "approval-1",
                        "decision": "pending_approval",
                        "args": {"session_id": "session-1", "request_id": "approval-1"},
                        "result": {
                            "status": "pending_approval",
                            "request_id": "approval-1",
                        },
                        "created_at": "2026-05-18T10:00:00+00:00",
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "event_type": "approval_decision",
                        "session_id": "session-1",
                        "vm_id": "vm-1",
                        "approval_request_id": "approval-1",
                        "decision": "denied",
                        "reason": "unsafe",
                        "args": {
                            "request_id": "approval-1",
                            "tool_name": "email_send_approved",
                            "approver": "alice",
                            "payload_digest": "digest-1",
                        },
                        "result": {
                            "status": "denied",
                            "tool_name": "email_send_approved",
                            "payload_digest": "digest-1",
                            "request_id": "approval-1",
                        },
                        "created_at": "2026-05-18T10:01:00+00:00",
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "event_type": "tool_call",
                        "session_id": "session-1",
                        "vm_id": "vm-1",
                        "tool_name": "email_send_approved",
                        "approval_request_id": "approval-1",
                        "decision": "allowed",
                        "args": {"session_id": "session-1", "request_id": "approval-1"},
                        "result": {
                            "status": "success",
                            "request_id": "approval-1",
                        },
                        "created_at": "2026-05-18T10:02:00+00:00",
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    review = service.get_approval_review("approval-1")

    assert [entry["event_type"] for entry in review["audit_context"]] == [
        "tool_call",
        "approval_decision",
        "tool_call",
    ]
    assert {entry["approval_request_id"] for entry in review["audit_context"]} == {
        "approval-1"
    }
    decision_entry = review["audit_context"][1]
    assert decision_entry["args"]["request_id"] == "approval-1"
    assert decision_entry["result"]["status"] == "denied"
    assert decision_entry["result"]["tool_name"] == "email_send_approved"
    assert decision_entry["result"]["payload_digest"] == "digest-1"


def test_admin_service_exposes_directory_config_for_selected_directory(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    write_result = service.write_directory_domain_config(
        team_path,
        "policy",
        {"default_session_ttl_seconds": 120},
    )
    assert write_result.ok is True

    read_result = service.read_directory_domain_config(team_path, "policy")
    assert read_result.ok is True
    assert read_result.data is not None
    assert read_result.data.config == {"default_session_ttl_seconds": 120}

    effective_result = service.compute_directory_effective_config(team_path, "policy")
    assert effective_result.ok is True
    assert effective_result.data is not None
    assert effective_result.data.resolution.effective_config["default_session_ttl_seconds"] == 120


def test_admin_service_reads_and_writes_ado_worker_global_config(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("ado")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "ado-worker" / "config.json")
    assert (
        read_result.config["ado_work_items_azure_devops_pat_env"]
        == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    )
    assert read_result.config["directories"] == []

    write_result = service.write_worker_global_config(
        "ado",
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "poll_interval_seconds": 12.5,
            "directories": ["root/team-a", "root/team-b"],
            "ado_work_items_azure_devops_pat_env": "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT",
            "include_comments": False,
            "comments_max_count": 7,
        },
    )
    assert write_result.config["poll_interval_seconds"] == 12.5
    assert write_result.config["directories"] == ["root/team-a", "root/team-b"]
    assert (
        write_result.config["ado_work_items_azure_devops_pat_env"]
        == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    )

    config_path = tmp_path / "config" / "ado-worker" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["poll_interval_seconds"] == 12.5
    assert payload["directories"] == ["root/team-a", "root/team-b"]
    assert payload["ado_work_items_azure_devops_pat_env"] == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    assert payload["include_comments"] is False
    assert payload["comments_max_count"] == 7

    reread = service.read_worker_global_config("ado")
    assert reread.config["include_comments"] is False


def test_admin_service_reads_and_writes_discord_wake_worker_global_config(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("discord-wake")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "discord-wake" / "config.json")
    assert (
        read_result.config["poll_interval_seconds"]
        == DiscordWakeWorkerGlobalConfig().poll_interval_seconds
    )
    assert read_result.config["directories"] == []
    assert read_result.config["backoff_seconds"] == DiscordWakeWorkerGlobalConfig().backoff_seconds

    write_result = service.write_worker_global_config(
        "discord-wake",
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "directories": ["root/team-a", "root/team-b"],
            "poll_interval_seconds": 12.5,
            "jitter_seconds": 1.5,
            "backoff_seconds": 45.0,
            "cooldown_seconds": 90.0,
            "lock_path": None,
            "session_cooldown_path": None,
            "busy_check_script_path": "scripts/check_session_vm_busy.sh",
            "resume_script_path": "scripts/resume_issue_agent.sh",
            "wake_state_path": None,
            "vm_user": "worker",
            "resume_timeout_seconds": 600.0,
        },
    )
    assert write_result.config["directories"] == ["root/team-a", "root/team-b"]
    assert write_result.config["vm_user"] == "worker"

    config_path = tmp_path / "config" / "discord-wake" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["directories"] == ["root/team-a", "root/team-b"]
    assert payload["poll_interval_seconds"] == 12.5
    assert payload["backoff_seconds"] == 45.0
    assert payload["vm_user"] == "worker"

    reread = service.read_worker_global_config("discord-wake")
    assert reread.config["directories"] == ["root/team-a", "root/team-b"]
    assert reread.config["backoff_seconds"] == 45.0
    assert reread.config["vm_user"] == "worker"


def test_admin_service_reads_and_writes_scheduled_wake_worker_global_config(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("scheduled-wake")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "scheduled-wake" / "config.json")
    assert (
        read_result.config["poll_interval_seconds"]
        == ScheduledWakeWorkerGlobalConfig().poll_interval_seconds
    )
    assert read_result.config["directories"] == []
    assert read_result.config["backoff_seconds"] == ScheduledWakeWorkerGlobalConfig().backoff_seconds

    write_result = service.write_worker_global_config(
        "scheduled-wake",
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "directories": ["root/team-a"],
            "poll_interval_seconds": 20.0,
            "jitter_seconds": 2.0,
            "backoff_seconds": 90.0,
            "cooldown_seconds": 90.0,
            "lock_path": None,
            "session_cooldown_path": None,
            "busy_check_script_path": "scripts/check_session_vm_busy.sh",
            "resume_script_path": "scripts/resume_issue_agent.sh",
            "wake_state_path": None,
            "vm_user": "worker",
            "resume_timeout_seconds": 600.0,
            "scheduled_wake_prefix_text": "Scheduled wake prefix.",
        },
    )
    assert write_result.config["directories"] == ["root/team-a"]
    assert write_result.config["scheduled_wake_prefix_text"] == "Scheduled wake prefix."

    config_path = tmp_path / "config" / "scheduled-wake" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["directories"] == ["root/team-a"]
    assert payload["scheduled_wake_prefix_text"] == "Scheduled wake prefix."
    assert payload["backoff_seconds"] == 90.0

    reread = service.read_worker_global_config("scheduled-wake")
    assert reread.config["directories"] == ["root/team-a"]
    assert reread.config["backoff_seconds"] == 90.0


def test_admin_service_reads_and_writes_discord_ingest_worker_global_config(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("discord-ingest")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "discord-ingest" / "config.json")
    assert (
        read_result.config["poll_interval_seconds"]
        == DiscordIngestWorkerGlobalConfig().poll_interval_seconds
    )
    assert read_result.config["directories"] == []
    assert read_result.config["backoff_seconds"] == 60.0

    write_result = service.write_worker_global_config(
        "discord-ingest",
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "directories": ["root/team-a", "root/team-b"],
            "poll_interval_seconds": 12.5,
            "jitter_seconds": 1.5,
            "backoff_seconds": 45.0,
            "lock_path": None,
        },
    )
    assert write_result.config["directories"] == ["root/team-a", "root/team-b"]
    assert write_result.config["poll_interval_seconds"] == 12.5

    config_path = tmp_path / "config" / "discord-ingest" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["directories"] == ["root/team-a", "root/team-b"]
    assert payload["poll_interval_seconds"] == 12.5
    assert payload["backoff_seconds"] == 45.0

    reread = service.read_worker_global_config("discord-ingest")
    assert reread.config["directories"] == ["root/team-a", "root/team-b"]
    assert reread.config["backoff_seconds"] == 45.0


def test_admin_service_reads_and_writes_teams_ingest_worker_global_config(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("teams-ingest")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "teams-ingest" / "config.json")
    assert (
        read_result.config["poll_interval_seconds"]
        == TeamsIngestWorkerGlobalConfig().poll_interval_seconds
    )
    assert read_result.config["directories"] == []
    assert read_result.config["backoff_seconds"] == 60.0

    write_result = service.write_worker_global_config(
        "teams-ingest",
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "directories": ["root/team-a", "root/team-b"],
            "poll_interval_seconds": 15.0,
            "jitter_seconds": 2.0,
            "backoff_seconds": 90.0,
            "lock_path": None,
        },
    )
    assert write_result.config["directories"] == ["root/team-a", "root/team-b"]
    assert write_result.config["poll_interval_seconds"] == 15.0

    config_path = tmp_path / "config" / "teams-ingest" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["directories"] == ["root/team-a", "root/team-b"]
    assert payload["poll_interval_seconds"] == 15.0
    assert payload["backoff_seconds"] == 90.0

    reread = service.read_worker_global_config("teams-ingest")
    assert reread.config["directories"] == ["root/team-a", "root/team-b"]
    assert reread.config["backoff_seconds"] == 90.0


def test_admin_service_can_read_and_mutate_trigger_orchestrator_triggers_by_id(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("trigger-orchestrator")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "trigger-orchestrator" / "config.json")
    assert read_result.config["triggers"] == []
    assert read_result.config["effects"] == []

    added = service.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 15 minutes",
            "type": "trigger:cron",
            "expression": "*/15 * * * *",
        },
    )
    assert added.operation == "added"
    assert added.config["triggers"][0]["id"] == "cron-15m"

    updated = service.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 30 minutes",
            "type": "trigger:cron",
            "expression": "*/30 * * * *",
        },
    )
    assert updated.operation == "updated"
    assert updated.config["triggers"] == [
        {
            "id": "cron-15m",
            "display_name": "Every 30 minutes",
            "type": "trigger:cron",
            "expression": "*/30 * * * *",
        }
    ]

    removed = service.delete_trigger_config("cron-15m")
    assert removed.operation == "deleted"
    assert removed.config["triggers"] == []


def test_trigger_mutations_do_not_reenter_admin_config_mutation_lock(
    tmp_path: Path,
) -> None:
    class NonReentrantLock:
        def __init__(self) -> None:
            self.depth = 0
            self.enter_count = 0

        def __call__(self):
            return self

        def __enter__(self):
            if self.depth:
                raise AssertionError("admin config mutation lock was re-entered")
            self.depth += 1
            self.enter_count += 1

        def __exit__(self, exc_type, exc, traceback):
            self.depth -= 1

    service = AdminService(write_config(tmp_path))
    lock = NonReentrantLock()
    service.admin_config_mutation_lock = lock

    service.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 15 minutes",
            "type": "trigger:cron",
            "expression": "*/15 * * * *",
        },
    )
    service.delete_trigger_config("cron-15m")
    service.ensure_chat_assistant_workflow()

    assert lock.enter_count == 2


def test_admin_service_can_read_and_mutate_trigger_orchestrator_effects_by_id(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 15 minutes",
            "type": "trigger:cron",
            "expression": "*/15 * * * *",
        },
    )

    added = service.upsert_effect_config(
        "prompt-agent",
        {
            "display_name": "Prompt agent",
            "type": "effect:agent:prompt",
            "source_id": "cron-15m",
            "prompt": "Summarize",
        },
    )
    assert added.operation == "added"
    assert added.config["effects"][0]["id"] == "prompt-agent"
    assert added.config["edges"] == [{"source_id": "cron-15m", "target_id": "prompt-agent"}]

    updated = service.upsert_effect_config(
        "prompt-agent",
        {
            "display_name": "Prompt agent updated",
            "type": "effect:agent:prompt",
            "source_id": "cron-15m",
            "prompt": "Summarize latest",
        },
    )
    assert updated.operation == "updated"
    assert updated.config["effects"] == [
        {
            "id": "prompt-agent",
            "display_name": "Prompt agent updated",
            "type": "effect:agent:prompt",
            "prompt": "Summarize latest",
        }
    ]
    assert updated.config["edges"] == [{"source_id": "cron-15m", "target_id": "prompt-agent"}]

    removed = service.delete_effect_config("prompt-agent")
    assert removed.operation == "deleted"
    assert removed.config["effects"] == []
    assert removed.config["edges"] == []


def test_admin_service_rejects_duplicate_trigger_ids_with_safe_error_message(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(
        ValueError,
        match="Node IDs must be globally unique across triggers, gates, effects, and queries: dup",
    ):
        service.write_worker_global_config(
            "trigger-orchestrator",
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "triggers": [
                    {
                        "id": "dup",
                        "display_name": "First",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    },
                    {
                        "id": "dup",
                        "display_name": "Second",
                        "type": "trigger:cron",
                        "expression": "*/30 * * * *",
                    },
                ],
            },
        )

    with pytest.raises(ValueError) as excinfo:
        service.upsert_trigger_config(
            "email-invalid",
            {
                "display_name": "Bad email provider",
                "type": "trigger:email:new-message",
                "provider": "not-real",
                "account_identifier": "acct",
                "mailbox_ids": ["inbox"],
                "auth_secret_refs": {"token": "VERY_SECRET_TOKEN"},
            },
        )
    message = str(excinfo.value)
    assert "Invalid trigger config:" in message
    assert "Unsupported email provider" in message
    assert "VERY_SECRET_TOKEN" not in message


def test_admin_service_rejects_system_managed_trigger_mutations(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(ValueError, match="system-managed"):
        service.upsert_trigger_config(
            "system.chat_assistant",
            {
                "display_name": "System Chat Assistant",
                "type": "trigger:manual",
            },
        )

    with pytest.raises(ValueError, match="system-managed"):
        service.delete_trigger_config("system.chat_assistant")

    with pytest.raises(ValueError, match="system-managed"):
        service.upsert_effect_config(
            "system.chat_assistant.enqueue_prompt",
            {
                "display_name": "System enqueue",
                "type": "effect:agent:enqueue-prompt",
                "prompt": "hello",
                "target": "assistant",
            },
        )

    with pytest.raises(ValueError, match="system-managed"):
        service.delete_effect_config("system.chat_assistant.enqueue_prompt")


def test_admin_service_chat_post_publishes_hidden_trigger_event(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    assert not config_path.exists()

    record = service.publish_chat_assistant_message(
        user_id="alice",
        thread_id="thread-1",
        user_message_id="msg-1",
        content="hello",
        backend="container_worker_vm",
    )

    assert record["event_id"] == "chat:msg-1"
    assert not config_path.exists()
    queued = SqliteTriggerEventQueue(tmp_path / "runtime" / "orchestration.db").read_all()[
        "chat:msg-1"
    ].model_dump(mode="json")
    assert queued["trigger_id"] == "system.chat_assistant"
    assert queued["payload"] == {
        "user_id": "alice",
        "thread_id": "thread-1",
        "user_message_id": "msg-1",
        "content": "hello",
        "backend": "container_worker_vm",
        "assistant_capability_mode": "read-only",
    }
    assert not (tmp_path / "runtime" / "queued-agent-prompts.json").exists()


def test_admin_service_chat_post_does_not_mutate_existing_trigger_config(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    original_config = {
        "runtime_dir": str(tmp_path / "runtime"),
        "poll_interval_seconds": 7,
        "workflow_packs": ["agent-container-worker.v1.json"],
    }
    config_path.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

    service.publish_chat_assistant_message(
        user_id="alice",
        thread_id="thread-1",
        user_message_id="msg-1",
        content="hello",
        backend="container_worker_vm",
    )

    assert json.loads(config_path.read_text(encoding="utf-8")) == original_config


def test_admin_service_publish_external_workflow_event_preserves_payload_and_idempotency(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"

    first = service.publish_external_workflow_event(
        event_type="deploy_requested",
        payload={"target": "demo", "options": {"dry_run": True}},
        correlation_key="deploy:demo",
        idempotency_key="request-1",
        source={
            "source_type": "workflow_event_api",
            "user_id": "subject-1",
            "user_login": "operator@example.com",
        },
    )
    second = service.publish_external_workflow_event(
        event_type="deploy_requested",
        payload={"target": "demo", "options": {"dry_run": True}},
        correlation_key="deploy:demo",
        idempotency_key="request-1",
    )

    events = InternalEventBus(tmp_path / "runtime" / "internal-events.json").list_events()
    assert first["event_id"] == second["event_id"]
    assert [event.event_id for event in events] == [first["event_id"]]
    assert events[0].event_type == "external.deploy_requested"
    assert events[0].payload == {"target": "demo", "options": {"dry_run": True}}
    assert events[0].correlation_key == "deploy:demo"
    assert events[0].source is not None
    assert events[0].source.source_type == "workflow_event_api"
    assert events[0].source.user_login == "operator@example.com"
    assert not config_path.exists()


def test_admin_service_publish_external_workflow_event_rejects_reserved_prefix(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(ValueError, match="reserved 'external\\.' prefix"):
        service.publish_external_workflow_event(
            event_type="external.deploy_requested",
            payload={},
        )


def test_workflow_editor_hides_and_does_not_save_system_managed_workflow_nodes(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "triggers": [
                    {
                        "id": "system.chat_assistant",
                        "display_name": "System Chat Assistant",
                        "type": "trigger:manual",
                    },
                    {
                        "id": "cron-15m",
                        "display_name": "Every 15 minutes",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    },
                ],
                "effects": [
                    {
                        "id": "system.chat_assistant.ensure_session",
                        "display_name": "Resolve chat assistant session",
                        "type": "effect:chat-assistant:ensure-session",
                        "user_id": "${{system.chat_assistant.user_id}}",
                        "thread_id": "${{system.chat_assistant.thread_id}}",
                        "user_message_id": "${{system.chat_assistant.user_message_id}}",
                        "content": "${{system.chat_assistant.content}}",
                    },
                    {
                        "id": "system.chat_assistant.enqueue_prompt",
                        "display_name": "Enqueue chat assistant prompt",
                        "type": "effect:agent:enqueue-prompt",
                        "target": "${{system.chat_assistant.ensure_session.session_id}}",
                        "prompt": "${{system.chat_assistant.ensure_session.prompt}}",
                    },
                    {
                        "id": "prompt-agent",
                        "display_name": "Prompt agent",
                        "type": "effect:agent:prompt",
                        "prompt": "Summarize",
                    },
                ],
                "edges": [{"source_id": "system.chat_assistant", "target_id": "prompt-agent"}],
            }
        ),
        encoding="utf-8",
    )

    document = service.read_workflow_editor_document()
    assert [trigger["id"] for trigger in document.config["triggers"]] == ["cron-15m"]
    assert [effect["id"] for effect in document.config["effects"]] == ["prompt-agent"]
    assert document.config["edges"] == []

    save_result = service.save_workflow_editor_document(
        config=document.config, layout={"nodes": {}}
    )
    assert [trigger["id"] for trigger in save_result.config["triggers"]] == ["cron-15m"]

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert all(node.get("id") != "system.chat_assistant" for node in persisted["triggers"])
    assert all(
        not str(node.get("id") or "").startswith("system.chat_assistant")
        for node in persisted["effects"]
    )
    assert persisted["edges"] == []


def test_workflow_editor_document_round_trips_config_and_separate_layout(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    save_result = service.save_workflow_editor_document(
        config={
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
        layout={
            "nodes": {
                "cron-15m": {"x": 120, "y": 80},
                "prompt-agent": {"x": 440, "y": 80},
            }
        },
    )

    assert Path(save_result.config_path).exists()
    assert Path(save_result.layout_path).exists()
    assert save_result.saved_at is not None
    assert save_result.revision is not None
    persisted_layout = json.loads(Path(save_result.layout_path).read_text(encoding="utf-8"))
    assert persisted_layout == {
        "nodes": {
            "cron-15m": {"x": 120.0, "y": 80.0},
            "prompt-agent": {"x": 440.0, "y": 80.0},
        }
    }

    reread = service.read_workflow_editor_document()
    assert reread.config["edges"] == [{"source_id": "cron-15m", "target_id": "prompt-agent"}]
    assert reread.layout == persisted_layout
    assert reread.revision == save_result.revision
    assert reread.saved_at is not None

    normalized_config = json.loads(Path(save_result.config_path).read_text(encoding="utf-8"))
    expected_revision = hashlib.sha256(
        json.dumps(
            {
                "config": normalized_config,
                "layout": save_result.layout,
                "graphs": [graph.model_dump(mode="json") for graph in save_result.graphs],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert save_result.revision == expected_revision


def test_workflow_editor_revision_changes_for_layout_only_edit(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    config = {
        "runtime_dir": str(tmp_path / "runtime"),
        "triggers": [
            {
                "id": "cron-15m",
                "display_name": "Every 15 minutes",
                "type": "trigger:cron",
                "expression": "*/15 * * * *",
            }
        ],
        "effects": [],
        "edges": [],
    }

    first = service.save_workflow_editor_document(
        config=config,
        layout={"nodes": {"cron-15m": {"x": 100, "y": 50}}},
    )
    second = service.save_workflow_editor_document(
        config=config,
        layout={"nodes": {"cron-15m": {"x": 200, "y": 50}}},
    )

    assert second.revision != first.revision


def test_workflow_editor_revision_changes_for_graph_metadata_only_edit(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    config = {
        "runtime_dir": str(tmp_path / "runtime"),
        "triggers": [
            {
                "id": "cron-15m",
                "display_name": "Every 15 minutes",
                "type": "trigger:cron",
                "expression": "*/15 * * * *",
            }
        ],
        "effects": [],
        "edges": [],
    }
    first = service.save_workflow_editor_document(config=config, layout={"nodes": {}})
    edited_graphs = [graph.model_dump(mode="json") for graph in first.graphs]
    edited_graphs[0]["name"] = "Renamed graph"

    second = service.save_workflow_editor_document(
        config=config,
        layout={"nodes": {}},
        graphs=edited_graphs,
    )

    assert second.revision != first.revision


def test_worker_global_config_revision_changes_for_config_edit(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    first = service.read_worker_global_config("trigger-orchestrator")
    config = dict(first.config)
    config["poll_interval_seconds"] = int(config.get("poll_interval_seconds") or 5) + 1

    second = service.write_worker_global_config("trigger-orchestrator", config)

    assert second.revision != first.revision


def test_workflow_editor_document_uses_runtime_parent_as_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "triggers": [
                    {
                        "id": "cron-15m",
                        "display_name": "Every 15 minutes",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    }
                ],
                "gates": [],
                "effects": [],
                "queries": [],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    (config_root / "layout.json").write_text(
        json.dumps({"nodes": {"cron-15m": {"x": 42, "y": 12}}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(REPO_ROOT.parent)

    service = AdminService(runtime_dir)
    document = service.read_workflow_editor_document()

    assert document.config_exists is True
    assert document.layout_exists is True
    assert document.config_path == str(config_root / "config.json")
    assert document.layout_path == str(config_root / "layout.json")
    assert document.config["triggers"][0]["id"] == "cron-15m"
    assert document.layout["nodes"]["cron-15m"] == {"x": 42.0, "y": 12.0}


def test_workflow_editor_document_composes_workflow_pack_config(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "demo.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "demo",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "cron-15m",
                            "display_name": "Every 15 minutes",
                            "type": "trigger:cron",
                            "expression": "*/15 * * * *",
                        },
                        {
                            "id": "prompt-agent",
                            "display_name": "Prompt agent",
                            "type": "effect:agent:prompt",
                            "prompt": "Summarize",
                        },
                    ],
                    "edges": [{"source_id": "cron-15m", "target_id": "prompt-agent"}],
                },
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "demo.v1.layout.json").write_text(
        json.dumps(
            {
                "nodes": {
                    "cron-15m": {"x": 120, "y": 80},
                    "prompt-agent": {"x": 440, "y": 80},
                }
            }
        ),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps({"runtime_dir": str(tmp_path / "runtime"), "workflow_packs": ["demo.v1.json"]}),
        encoding="utf-8",
    )

    document = AdminService(write_config(tmp_path)).read_workflow_editor_document()

    assert document.config["workflow_packs"] == ["demo.v1.json"]
    assert [trigger["id"] for trigger in document.config["triggers"]] == ["cron-15m"]
    assert [effect["id"] for effect in document.config["effects"]] == ["prompt-agent"]
    assert document.config["edges"] == [{"source_id": "cron-15m", "target_id": "prompt-agent"}]
    assert document.layout_path == str(packs_dir / "demo.v1.layout.json")
    assert document.layout == {
        "nodes": {
            "cron-15m": {"x": 120.0, "y": 80.0},
            "prompt-agent": {"x": 440.0, "y": 80.0},
        }
    }


def test_workflow_editor_save_updates_single_workflow_pack_without_flattening_wrapper(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_path = packs_dir / "demo.v1.json"
    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "demo",
                "pack_version": "v1",
                "description": "Demo workflow",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "cron-15m",
                            "display_name": "Every 15 minutes",
                            "type": "trigger:cron",
                            "expression": "*/15 * * * *",
                        },
                        {
                            "id": "prompt-agent",
                            "display_name": "Prompt agent",
                            "type": "effect:agent:prompt",
                            "prompt": "Summarize",
                        },
                    ],
                    "edges": [{"source_id": "cron-15m", "target_id": "prompt-agent"}],
                },
            }
        ),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "poll_interval_seconds": 12,
                "workflow_packs": ["demo.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    service = AdminService(write_config(tmp_path))
    document = service.read_workflow_editor_document()
    edited_config = dict(document.config)
    effects = [dict(effect) for effect in edited_config["effects"]]
    effects[0]["display_name"] = "Prompt agent with context"
    edited_config["effects"] = effects

    save_result = service.save_workflow_editor_document(
        config=edited_config,
        layout={"nodes": {"cron-15m": {"x": 100, "y": 50}, "prompt-agent": {"x": 300, "y": 50}}},
    )

    layout_path = packs_dir / "demo.v1.layout.json"
    wrapper_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert wrapper_config["workflow_packs"] == ["demo.v1.json"]
    assert wrapper_config["poll_interval_seconds"] == 12.0
    for graph_key in ("triggers", "gates", "effects", "queries", "edges"):
        assert graph_key not in wrapper_config

    pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
    assert pack_payload["description"] == "Demo workflow"
    assert all(
        not str(node.get("id") or "").startswith("system.chat_assistant")
        for node in pack_payload["ir"]["nodes"]
    )
    saved_effect = next(
        node for node in pack_payload["ir"]["nodes"] if node["id"] == "prompt-agent"
    )
    assert saved_effect["display_name"] == "Prompt agent with context"
    assert save_result.config["effects"][0]["display_name"] == "Prompt agent with context"
    assert save_result.layout_path == str(layout_path)
    assert json.loads(layout_path.read_text(encoding="utf-8")) == {
        "nodes": {
            "cron-15m": {"x": 100.0, "y": 50.0},
            "prompt-agent": {"x": 300.0, "y": 50.0},
        }
    }

    reread = service.read_workflow_editor_document()
    assert reread.config["effects"][0]["display_name"] == "Prompt agent with context"
    assert reread.layout_path == str(layout_path)
    assert reread.layout["nodes"]["prompt-agent"] == {"x": 300.0, "y": 50.0}


def test_workflow_editor_reads_and_saves_named_graphs_inside_single_pack(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_path = packs_dir / "demo.v1.json"
    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "demo",
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "graph-ticket",
                        "name": "Ticket intake",
                        "description": "Handles new tickets.",
                        "directory_paths": ["root"],
                        "user_ids": ["alice"],
                        "node_ids": ["trigger-ticket", "effect-ticket"],
                    },
                    {
                        "id": "graph-cron",
                        "name": "Scheduled cleanup",
                        "directory_paths": ["root/team-a"],
                        "user_ids": ["bob"],
                        "node_ids": ["trigger-cron", "effect-cron"],
                    },
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-ticket",
                            "display_name": "Ticket created",
                            "type": "trigger:ticket:created",
                            "directories": ["root"],
                        },
                        {
                            "id": "effect-ticket",
                            "display_name": "Prompt ticket",
                            "type": "effect:agent:prompt",
                            "prompt": "Handle ticket",
                        },
                        {
                            "id": "trigger-cron",
                            "display_name": "Every hour",
                            "type": "trigger:cron",
                            "expression": "0 * * * *",
                        },
                        {
                            "id": "effect-cron",
                            "display_name": "Cleanup",
                            "type": "effect:bash:script",
                            "script_path": "/bin/echo",
                        },
                    ],
                    "edges": [
                        {"source_id": "trigger-ticket", "target_id": "effect-ticket"},
                        {"source_id": "trigger-cron", "target_id": "effect-cron"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    config_path.write_text(
        json.dumps({"runtime_dir": str(tmp_path / "runtime"), "workflow_packs": ["demo.v1.json"]}),
        encoding="utf-8",
    )
    service = AdminService(write_config(tmp_path))

    document = service.read_workflow_editor_document()

    assert [
        (graph.id, graph.name, graph.directory_paths, graph.user_ids, graph.node_ids)
        for graph in document.graphs
    ] == [
        ("graph-ticket", "Ticket intake", ["root"], ["alice"], ["trigger-ticket", "effect-ticket"]),
        (
            "graph-cron",
            "Scheduled cleanup",
            ["root/team-a"],
            ["bob"],
            ["trigger-cron", "effect-cron"],
        ),
    ]

    edited_graphs = [graph.model_dump(mode="json") for graph in document.graphs]
    edited_graphs[0]["name"] = "Root ticket intake"
    edited_graphs[0]["description"] = "Visible root-directory graph edit."
    save_result = service.save_workflow_editor_document(
        config=document.config,
        layout=document.layout,
        graphs=edited_graphs,
    )

    assert save_result.graphs[0].name == "Root ticket intake"
    wrapper_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert wrapper_config["workflow_packs"] == ["demo.v1.json"]
    assert "workflow_graphs" not in wrapper_config
    pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
    assert pack_payload["graphs"] == [
        {
            "id": "graph-ticket",
            "name": "Root ticket intake",
            "description": "Visible root-directory graph edit.",
            "directory_paths": ["root"],
            "user_ids": ["alice"],
            "node_ids": ["trigger-ticket", "effect-ticket"],
        },
        {
            "id": "graph-cron",
            "name": "Scheduled cleanup",
            "directory_paths": ["root/team-a"],
            "user_ids": ["bob"],
            "node_ids": ["trigger-cron", "effect-cron"],
        },
    ]
    assert pack_payload["graphs"][1] == {
        "id": "graph-cron",
        "name": "Scheduled cleanup",
        "directory_paths": ["root/team-a"],
        "user_ids": ["bob"],
        "node_ids": ["trigger-cron", "effect-cron"],
    }


def test_workflow_graph_activity_report_groups_runtime_records_by_graph(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_path = packs_dir / "demo.v1.json"
    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "demo",
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "graph-ticket",
                        "name": "Ticket intake",
                        "directory_paths": ["root"],
                        "user_ids": ["alice"],
                        "node_ids": ["trigger-ticket", "gate-filter", "effect-ticket"],
                    },
                    {
                        "id": "graph-cron",
                        "name": "Scheduled cleanup",
                        "directory_paths": ["root"],
                        "node_ids": ["trigger-cron", "effect-cron"],
                    },
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-ticket",
                            "display_name": "Ticket created",
                            "type": "trigger:ticket:created",
                            "directories": ["root"],
                        },
                        {
                            "id": "gate-filter",
                            "display_name": "Filter ticket",
                            "type": "gate:filter",
                            "when": {
                                "all": [
                                    {
                                        "equals": "root",
                                        "left": "${{trigger-ticket.ticket.directory_path}}",
                                    }
                                ]
                            },
                        },
                        {
                            "id": "effect-ticket",
                            "display_name": "Prompt ticket",
                            "type": "effect:agent:prompt",
                            "prompt": "Handle ticket",
                        },
                        {
                            "id": "trigger-cron",
                            "display_name": "Every hour",
                            "type": "trigger:cron",
                            "expression": "0 * * * *",
                        },
                        {
                            "id": "effect-cron",
                            "display_name": "Cleanup",
                            "type": "effect:bash:script",
                            "script_path": "/bin/echo",
                        },
                    ],
                    "edges": [
                        {"source_id": "trigger-ticket", "target_id": "gate-filter"},
                        {"source_id": "gate-filter", "target_id": "effect-ticket"},
                        {"source_id": "trigger-cron", "target_id": "effect-cron"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps({"runtime_dir": str(runtime_dir), "workflow_packs": ["demo.v1.json"]}),
        encoding="utf-8",
    )
    (runtime_dir / "trigger-orchestrator-events.json").write_text(
        json.dumps(
            {
                "evt-ticket": {
                    "event_id": "evt-ticket",
                    "trigger_id": "trigger-ticket",
                    "trigger_type": "trigger:ticket:created",
                    "trace_id": "trace-ticket",
                    "occurred_at": "2026-05-01T10:00:00Z",
                    "delivery_state": "delivered",
                    "payload": {},
                },
                "evt-cron": {
                    "event_id": "evt-cron",
                    "trigger_id": "trigger-cron",
                    "trigger_type": "trigger:cron",
                    "trace_id": "trace-cron",
                    "occurred_at": "2026-05-01T11:00:00Z",
                    "delivery_state": "delivered",
                    "payload": {},
                },
                "evt-old": {
                    "event_id": "evt-old",
                    "trigger_id": "trigger-ticket",
                    "trigger_type": "trigger:ticket:created",
                    "trace_id": "trace-old",
                    "occurred_at": "2026-04-30T10:00:00Z",
                    "delivery_state": "delivered",
                    "payload": {},
                },
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "gate-orchestrator-executions.json").write_text(
        json.dumps(
            {
                "gate-ticket": {
                    "execution_id": "gate-ticket",
                    "event_id": "evt-ticket",
                    "trace_id": "trace-ticket",
                    "gate_id": "gate-filter",
                    "status": "succeeded",
                    "started_at": "2026-05-01T10:01:00Z",
                    "finished_at": "2026-05-01T10:01:02Z",
                }
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "effect-orchestrator-executions.json").write_text(
        json.dumps(
            {
                "effect-ticket": {
                    "execution_id": "effect-ticket",
                    "event_id": "evt-ticket",
                    "trace_id": "trace-ticket",
                    "effect_id": "effect-ticket",
                    "status": "succeeded",
                    "started_at": "2026-05-01T10:02:00Z",
                    "finished_at": "2026-05-01T10:03:00Z",
                },
                "effect-cron-old": {
                    "execution_id": "effect-cron-old",
                    "event_id": "evt-cron-old",
                    "trace_id": "trace-cron-old",
                    "effect_id": "effect-cron",
                    "status": "succeeded",
                    "started_at": "2026-04-30T10:02:00Z",
                    "finished_at": "2026-04-30T10:03:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    service = AdminService(write_config(tmp_path))

    report = service.read_workflow_graph_activity_report(
        period_start=datetime(2026, 5, 1, tzinfo=UTC),
        period_end=datetime(2026, 5, 2, tzinfo=UTC),
    )

    assert [graph.graph_id for graph in report.graphs] == ["graph-ticket", "graph-cron"]
    ticket_graph = report.graphs[0]
    assert ticket_graph.activity_count == 3
    assert ticket_graph.run_count == 1
    assert ticket_graph.trigger_event_count == 1
    assert ticket_graph.gate_execution_count == 1
    assert ticket_graph.effect_execution_count == 1
    assert ticket_graph.status_counts == {"delivered": 1, "succeeded": 2}
    assert ticket_graph.user_ids == ["alice"]
    assert [(node.node_id, node.activity_count) for node in ticket_graph.nodes] == [
        ("effect-ticket", 1),
        ("gate-filter", 1),
        ("trigger-ticket", 1),
    ]
    cron_graph = report.graphs[1]
    assert cron_graph.activity_count == 1
    assert cron_graph.run_count == 1
    assert cron_graph.effect_execution_count == 0


def test_workflow_graph_activity_report_initializes_fresh_sqlite_backend(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "demo.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "demo",
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "graph-ticket",
                        "name": "Ticket intake",
                        "directory_paths": ["root"],
                        "node_ids": ["trigger-ticket"],
                    }
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-ticket",
                            "display_name": "Ticket created",
                            "type": "trigger:ticket:created",
                            "directories": ["root"],
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps({"runtime_dir": str(runtime_dir), "workflow_packs": ["demo.v1.json"]}),
        encoding="utf-8",
    )
    db_path = runtime_dir / "orchestration.db"
    service = AdminService(write_config(tmp_path))

    report = service.read_workflow_graph_activity_report(
        period_start=datetime(2026, 5, 1, tzinfo=UTC),
        period_end=datetime(2026, 5, 2, tzinfo=UTC),
    )

    assert db_path.exists()
    assert [(graph.graph_id, graph.activity_count) for graph in report.graphs] == [
        ("graph-ticket", 0)
    ]
    with sqlite3.connect(db_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"trigger_events", "effect_executions", "gate_executions"} <= table_names


def test_admin_service_read_workflow_run_detail_from_sqlite_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AdminService(write_config(tmp_path))
    runtime_dir = tmp_path / "runtime"
    db_path = runtime_dir / "orchestration.db"
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-sqlite",
            vm_provider="container_worker_vm",
            vm_provider_id="target-sqlite",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime(2026, 6, 1, 11, 0, tzinfo=UTC),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-sqlite",
            },
        )
    )
    ContainerWorkerExecutionRepository(
        runtime_dir / "container-worker-executions.json"
    ).put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-sqlite-agent",
            session_id="session-sqlite",
            worker_job_id="job-sqlite-agent",
            worker_url="http://worker.test",
            target_name="target-sqlite",
            last_status="running",
            directory_path="root",
            metadata={"image": "runner:sqlite"},
        )
    )
    artifact_store = CommunicationArtifactStore(runtime_dir)
    artifact = artifact_store.stage(
        provider="teams",
        session_id="session-sqlite",
        content=b"sqlite run detail artifact\n",
        original_filename="sqlite-notes.txt",
        created_at=datetime(2026, 5, 16, 11, 0, tzinfo=UTC),
        expires_at=datetime(2026, 6, 23, 11, 0, tzinfo=UTC),
        metadata={"source": "sqlite-fixture"},
    )
    service.runtime.approvals.put(
        "approval-sqlite",
        ApprovalRequest(
            request_id="approval-sqlite",
            session_id="session-sqlite",
            action_type="effect:agent:prompt",
            payload_digest="digest-sqlite",
            human_summary="Approve the sqlite run detail fixture.",
            payload={
                "request_id": "approval-sqlite",
                "session_id": "session-sqlite",
                "artifact_ids": [artifact.artifact_id],
            },
            directory_path="root",
            tool_name="codex",
            status=ApprovalStatus.APPROVED,
            approver="sqlite-reviewer",
            decision_at=datetime(2026, 5, 3, 11, 0, 30, tzinfo=UTC),
            decision_reason="Approved for the sqlite run detail fixture.",
            expires_at=datetime(2026, 5, 4, 11, 0, tzinfo=UTC),
            created_at=datetime(2026, 5, 3, 11, 0, tzinfo=UTC),
        ),
    )
    queue_store = SqliteTriggerEventQueue(db_path)
    queue_store.publish(
        TriggerEvent(
            event_id="evt-sqlite",
            trigger_id="trigger-ticket",
            trigger_type="trigger:ticket:created",
            occurred_at=datetime(2026, 5, 3, 11, 0, tzinfo=UTC),
            payload={"issue_number": "11"},
            trace_id="trace-sqlite",
        ),
    )
    effect_store = SqliteExecutionStore(db_path)
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="exec-sqlite",
            idempotency_key="evt-sqlite::effect-prompt",
            event_id="evt-sqlite",
            trace_id="trace-sqlite",
            source_node_id="trigger-ticket",
            source_trigger_type="trigger:ticket:created",
            effect_id="effect-prompt",
            effect_type="effect:agent:prompt",
            status="failed",
            started_at=datetime(2026, 5, 3, 11, 0, 5, tzinfo=UTC),
            finished_at=datetime(2026, 5, 3, 11, 0, 6, tzinfo=UTC),
            resolved_config_hash="hash-sqlite",
            resolved_config={
                "session_id": "session-sqlite",
                "vm_name": "target-sqlite",
                "prompt": "Run the sqlite flow",
                "dispatch_id": "dispatch-sqlite",
                "thread_id": "thread-sqlite",
                "execution_id": "execution-sqlite",
                "artifact_ids": [artifact.artifact_id],
                "request_id": "approval-sqlite",
            },
            input_payload={
                "issue_number": "11",
                "session_id": "session-sqlite",
                "request_id": "approval-sqlite",
            },
            outputs={"dispatch_id": "dispatch-sqlite", "execution_id": "execution-sqlite"},
            binding_trace={
                "consumed_output_keys": ["trigger-ticket.issue_number"],
                "entries": [],
            },
            config_revision="rev-sqlite",
            error="sqlite failure",
        )
    )
    gate_store = SqliteGateExecutionStore(db_path)
    gate_store.record_execution(
        GateExecutionRecord(
            execution_id="gate-sqlite",
            idempotency_key="evt-sqlite::gate-await",
            event_id="evt-sqlite",
            trace_id="trace-sqlite",
            source_node_id="effect-prompt",
            source_trigger_type="trigger:ticket:created",
            gate_id="gate-await",
            gate_type="gate:await-agent",
            status="waiting",
            started_at=datetime(2026, 5, 3, 11, 0, 7, tzinfo=UTC),
            finished_at=datetime(2026, 5, 3, 11, 0, 8, tzinfo=UTC),
            resolved_config_hash="hash-sqlite",
            resolved_config={
                "session_id": "session-sqlite",
                "vm_name": "vm-sqlite",
                "dispatch_id": "dispatch-sqlite",
            },
            input_payload={"session_id": "session-sqlite", "vm_name": "vm-sqlite"},
            outputs={"dispatch_id": "dispatch-sqlite"},
            inputs_by_source={"effect-prompt": {"issue_number": "11"}},
            input_event_ids_by_source={"effect-prompt": "evt-sqlite"},
            input_occurred_at_by_source={
                "effect-prompt": datetime(2026, 5, 3, 11, 0, tzinfo=UTC)
            },
            config_revision="rev-sqlite",
        )
    )
    SqliteWorkflowConfigSnapshotRepository(db_path).put_snapshot(
        {
            "revision": "rev-sqlite",
            "captured_at": "2026-05-03T11:00:30Z",
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
                {"id": "gate-await", "type": "gate:await-agent", "display_name": "Await Agent"}
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
                {"id": "edge-1", "source": "trigger-ticket", "target": "effect-prompt"},
                {"id": "edge-2", "source": "effect-prompt", "target": "gate-await"},
            ],
            "workflow_graphs": [
                {
                    "id": "graph-sqlite",
                    "name": "SQLite Graph",
                    "description": "SQLite run detail fixture",
                    "directory_paths": ["root"],
                    "user_ids": ["operator"],
                    "node_ids": ["trigger-ticket", "effect-prompt", "gate-await"],
                }
            ],
        }
    )
    _write_run_detail_audit_log(
        runtime_dir / "audit.jsonl",
        session_id="session-sqlite",
        vm_id="target-sqlite",
    )
    monkeypatch.setattr(
        SqliteTriggerEventQueue,
        "read_all",
        lambda self: pytest.fail("run detail should not scan every trigger event"),
    )
    monkeypatch.setattr(
        SqliteExecutionStore,
        "read_all",
        lambda self: pytest.fail("run detail should not scan every effect execution"),
    )
    monkeypatch.setattr(
        SqliteGateExecutionStore,
        "read_all",
        lambda self: pytest.fail("run detail should not scan every gate execution"),
    )

    detail = service.read_workflow_run_detail(event_id="evt-sqlite")
    detail_by_trace = service.read_workflow_run_detail(trace_id="trace-sqlite")

    assert detail.event_id == "evt-sqlite"
    assert detail_by_trace.event_id == "evt-sqlite"
    assert detail_by_trace.trace_id == "trace-sqlite"
    assert detail.graph.config_revision == "rev-sqlite"
    assert detail.graph.snapshot is not None
    assert detail.graph.snapshot.revision == "rev-sqlite"
    assert detail.graph.snapshot.snapshot_type == "compiled_runtime_graph"
    assert detail.graph.snapshot.workflow_graphs[0]["id"] == "graph-sqlite"
    assert detail.graph.data_availability.graph_snapshot == "available"
    assert detail.graph.data_availability.agent_links == "available"
    assert detail.graph.data_availability.communication_artifacts == "available"
    assert detail.graph.data_availability.tool_calls == "available"
    assert detail.recovery.resume.eligible is True
    assert detail.trigger_event is not None
    assert detail.trigger_event.payload == {"issue_number": "11"}
    effect_node = next(node for node in detail.nodes if node.node_id == "effect-prompt")
    assert effect_node.status == "failed"
    assert effect_node.duration_ms == 1000
    assert effect_node.resolved_config["prompt"] == "Run the sqlite flow"
    assert effect_node.binding_trace is not None
    assert effect_node.binding_trace["entries"] == []
    assert effect_node.retry_resume_eligible is True
    tool_call_link = effect_node.tool_call_links[0]
    assert tool_call_link.tool_name == "prompt_agent"
    assert tool_call_link.decision == "allowed"
    assert tool_call_link.status == "success"
    assert tool_call_link.request_id == "request-sqlite-node"
    assert tool_call_link.correlation_id == "correlation-sqlite-node"
    assert tool_call_link.approval_request_id == "approval-sqlite"
    assert tool_call_link.workflow_run_id == "evt-sqlite"
    assert tool_call_link.trace_id == "trace-sqlite"
    assert tool_call_link.href == "#tool-calls?q=request-sqlite-node"
    assert tool_call_link.args["token"] == "<redacted>"
    assert tool_call_link.result["secret"] == "<redacted>"
    assert tool_call_link.redacted_args["token"] == "<redacted>"
    assert tool_call_link.redacted_result["secret"] == "<redacted>"
    assert tool_call_link.args_summary is not None
    assert tool_call_link.result_summary is not None
    assert tool_call_link.result_status == "available"
    approval_link = effect_node.approval_links[0]
    assert approval_link.request_id == "approval-sqlite"
    assert approval_link.status == "approved"
    assert approval_link.approver == "sqlite-reviewer"
    assert approval_link.decision_at == datetime(2026, 5, 3, 11, 0, 30, tzinfo=UTC)
    assert approval_link.decision_reason == "Approved for the sqlite run detail fixture."
    assert approval_link.expires_at == datetime(2026, 5, 4, 11, 0, tzinfo=UTC)
    agent_link = effect_node.agent_links[0]
    assert agent_link.session_id == "session-sqlite"
    assert agent_link.prompt == "Run the sqlite flow"
    assert agent_link.thread_id == "thread-sqlite"
    assert agent_link.execution_id == "execution-sqlite"
    assert agent_link.artifact_ids == [artifact.artifact_id]
    assert agent_link.session_status == "available"
    assert agent_link.session_available is True
    assert agent_link.agent_available is True
    assert agent_link.log_status == "log_available"
    assert agent_link.log_available is True
    assert agent_link.log_href is not None
    assert agent_link.result_status == "available"
    assert agent_link.result_summary is not None
    assert agent_link.log_reference == {
        "source_kind": "effect_execution",
        "node_id": "effect-prompt",
        "session_id": "session-sqlite",
        "vm_name": "target-sqlite",
        "thread_id": "thread-sqlite",
        "execution_id": "execution-sqlite",
        "dispatch_id": "dispatch-sqlite",
    }
    artifact_link = effect_node.communication_artifact_links[0]
    assert artifact_link.artifact_id == artifact.artifact_id
    assert artifact_link.session_id == "session-sqlite"
    assert artifact_link.provider == "teams"
    assert artifact_link.original_filename == "sqlite-notes.txt"
    assert artifact_link.metadata == {"storage": "host_artifact_store", "source": "sqlite-fixture"}
    assert [link.tool_name for link in detail.tool_call_links] == [
        "make_phone_call",
        "make_phone_call",
    ]
    assert detail.tool_call_links[0].decision == "denied"
    assert detail.tool_call_links[0].status == "denied"
    assert detail.tool_call_links[0].href == "#tool-calls?q=correlation-sqlite-run-level"
    assert detail.tool_call_links[0].session_id == "session-sqlite"
    assert detail.tool_call_links[1].decision == "allowed"
    assert detail.tool_call_links[1].status == "success"
    assert detail.tool_call_links[1].href == "#tool-calls?q=trace-sqlite"
    assert detail.tool_call_links[1].session_id == "session-sqlite"
    assert detail.data_availability.tool_calls == "available"
    assert detail.data_availability.agent_links == "available"
    assert detail.data_availability.communication_artifacts == "available"


def test_admin_service_keeps_completion_pending_workflow_runs_visible_and_clearable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AdminService(write_config(tmp_path))
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stores = {
        "runtime_dir": runtime_dir,
        "orchestration_store_backend": "json",
        "orchestration_store_path": runtime_dir / "orchestration.db",
        "orchestration_retention_days": 30,
        "event_queue_store_path": runtime_dir / "trigger-orchestrator-events.json",
        "effect_execution_store_path": runtime_dir / "effect-orchestrator-executions.json",
        "gate_execution_store_path": runtime_dir / "gate-orchestrator-executions.json",
        "workflow_config_snapshot_store_path": runtime_dir / "workflow-config-snapshots.json",
        "await_agent_store_path": runtime_dir / "providers" / "gates" / "await-agent" / "pending.json",
        "legacy_await_agent_store_path": runtime_dir / "await-agent-pending.json",
    }
    workflow_admin = service.worker_admin._deps.read_workflow_run_snapshot.__self__
    monkeypatch.setattr(workflow_admin, "_workflow_run_store_paths", lambda: stores)
    monkeypatch.setattr(
        workflow_admin, "_read_workflow_editor_document", lambda: SimpleNamespace(graphs=[])
    )

    started_at = datetime(2026, 5, 20, 11, 0, tzinfo=UTC)
    finished_at = datetime(2026, 5, 20, 11, 1, tzinfo=UTC)
    effect_store = JsonExecutionStore(stores["effect_execution_store_path"])
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="exec-effect-1",
            idempotency_key="evt-1::effect-1",
            event_id="evt-1",
            trace_id="trace-1",
            source_node_id="trigger-1",
            source_trigger_type="trigger:test",
            effect_id="effect-1",
            effect_type="effect:test",
            status="completion_pending",
            started_at=started_at,
            finished_at=finished_at,
            resolved_config_hash="hash-effect-1",
            resolved_config={},
            input_payload={},
            outputs={"dispatch_id": "dispatch-1"},
            completion_pending_status="succeeded",
            completion_event_branch_port="main",
        )
    )
    gate_store = JsonGateExecutionStore(stores["gate_execution_store_path"])
    gate_store.record_execution(
        GateExecutionRecord(
            execution_id="exec-gate-1",
            idempotency_key="evt-1::gate-1",
            event_id="evt-1",
            trace_id="trace-1",
            source_node_id="effect-1",
            source_trigger_type="trigger:test",
            gate_id="gate-1",
            gate_type="gate:test",
            status="completion_pending",
            started_at=started_at,
            finished_at=finished_at,
            resolved_config_hash="hash-gate-1",
            resolved_config={},
            input_payload={},
            outputs={"ok": True},
            inputs_by_source={"effect-1": {"dispatch_id": "dispatch-1"}},
            input_event_ids_by_source={"effect-1": "evt-1"},
            input_occurred_at_by_source={"effect-1": started_at},
            completion_pending_status="succeeded",
            completion_event_branch_port="main",
            completion_event_for_execution_ids={"effect-1": "exec-effect-1"},
        )
    )

    snapshot = service.read_workflow_run_snapshot()
    assert len(snapshot.runs) == 1
    snapshot_run = snapshot.runs[0]
    assert snapshot_run.run_state == "active"
    assert {node.node_id for node in snapshot_run.active_nodes} == {"effect-1", "gate-1"}
    assert {node.status for node in snapshot_run.active_nodes} == {"completion_pending"}

    search = service.read_workflow_run_search(node_id="effect-1")
    assert len(search.runs) == 1
    search_run = search.runs[0]
    assert search_run.run_state == "active"
    assert search_run.status_counts == {"completion_pending": 2}

    clear = service.clear_workflow_run("evt-1")
    assert clear.effect_records_updated == 1
    assert clear.gate_records_updated == 1

    cleared_effect = JsonExecutionStore(stores["effect_execution_store_path"]).get_execution(
        "exec-effect-1"
    )
    cleared_gate = JsonGateExecutionStore(stores["gate_execution_store_path"]).get_execution(
        "exec-gate-1"
    )
    assert cleared_effect is not None
    assert cleared_effect.status == "failed"
    assert cleared_effect.clear_reason == "manual_clear_stale_workflow_runs"
    assert cleared_gate is not None
    assert cleared_gate.status == "failed"
    assert cleared_gate.clear_reason == "manual_clear_stale_workflow_runs"


def test_conformance_reports_summarize_present_runtime_artifacts(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "trigger-conformance-report.json").write_text(
        json.dumps(
            {
                "generated_at": "2099-01-01T00:00:00Z",
                "status": "pass",
                "requirements": [
                    {
                        "requirement_id": "schema:trigger:cron",
                        "status": "pass",
                        "coverage": ["cron shape"],
                        "details": "Cron trigger validates.",
                    },
                    {
                        "requirement_id": "provider:cursor",
                        "status": "pass",
                        "coverage": ["cursor"],
                        "details": "Cursor persists.",
                    },
                ],
                "traceability_matrix": {"task_trigger_schema": ["schema:trigger:cron"]},
                "missing_coverage": {},
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "effect-conformance-report.json").write_text(
        json.dumps(
            {
                "generated_at": "2099-01-01T00:00:00Z",
                "status": "fail",
                "requirements": [
                    {
                        "requirement_id": "schema:supported-effect-types",
                        "status": "pass",
                        "coverage": ["effect schema"],
                        "details": "Schema validates.",
                    },
                    {
                        "requirement_id": "binding:resolution-and-chaining",
                        "status": "fail",
                        "coverage": ["binding"],
                        "details": "Binding output was missing.",
                        "remediation": "Restore binding coverage.",
                    },
                ],
                "traceability_matrix": {
                    "task_schema_validation": ["schema:supported-effect-types"],
                    "task_binding_and_chain": ["binding:resolution-and-chaining"],
                },
                "missing_coverage": {"task_queue_idempotency": ["queue:idempotency"]},
            }
        ),
        encoding="utf-8",
    )
    service = AdminService(config_path)

    payload = service.read_conformance_reports()

    assert payload.stale_after_seconds == 7 * 24 * 60 * 60
    reports = {report.kind: report for report in payload.reports}
    assert set(reports) == {"trigger", "effect"}
    trigger = reports["trigger"]
    assert trigger.artifact_path == "trigger-conformance-report.json"
    assert trigger.state == "pass"
    assert trigger.status == "pass"
    assert trigger.requirement_counts == {"pass": 2}
    assert trigger.requirement_count == 2
    assert trigger.missing_coverage_count == 0
    assert trigger.traceability_task_count == 1
    assert trigger.failing_requirements == []
    effect = reports["effect"]
    assert effect.artifact_path == "effect-conformance-report.json"
    assert effect.state == "fail"
    assert effect.status == "fail"
    assert effect.requirement_counts == {"fail": 1, "pass": 1}
    assert effect.missing_coverage_count == 1
    assert effect.missing_coverage == {"task_queue_idempotency": ["queue:idempotency"]}
    assert effect.traceability_task_count == 2
    assert [item.requirement_id for item in effect.failing_requirements] == [
        "binding:resolution-and-chaining"
    ]
    assert effect.remediation_snippets[0].remediation == "Restore binding coverage."
    assert not Path(effect.artifact_path).is_absolute()


def test_conformance_reports_return_missing_guidance(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    payload = service.read_conformance_reports()

    reports = {report.kind: report for report in payload.reports}
    assert reports["trigger"].state == "missing"
    assert reports["trigger"].guidance == (
        "PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py"
    )
    assert reports["effect"].state == "missing"
    assert reports["effect"].guidance == (
        "PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py"
    )


def test_conformance_reports_return_invalid_for_malformed_and_unsupported_shapes(
    tmp_path: Path,
) -> None:
    config_path = write_config(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "trigger-conformance-report.json").write_text("{not json", encoding="utf-8")
    (runtime_dir / "effect-conformance-report.json").write_text(
        json.dumps(
            {
                "generated_at": "2099-01-01T00:00:00Z",
                "status": "pass",
                "requirements": [],
                "traceability_matrix": {},
            }
        ),
        encoding="utf-8",
    )
    service = AdminService(config_path)

    reports = {report.kind: report for report in service.read_conformance_reports().reports}

    assert reports["trigger"].state == "invalid"
    assert "not valid JSON" in (reports["trigger"].error or "")
    assert reports["effect"].state == "invalid"
    assert "missing_coverage must be an object" in (reports["effect"].error or "")


def test_conformance_reports_marks_old_valid_artifacts_stale(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("trigger-conformance-report.json", "effect-conformance-report.json"):
        (runtime_dir / filename).write_text(
            json.dumps(
                {
                    "generated_at": "2000-01-01T00:00:00Z",
                    "status": "pass",
                    "requirements": [],
                    "traceability_matrix": {},
                    "missing_coverage": {},
                }
            ),
            encoding="utf-8",
        )
    service = AdminService(config_path)

    reports = {report.kind: report for report in service.read_conformance_reports().reports}

    assert reports["trigger"].state == "stale"
    assert reports["trigger"].status == "pass"
    assert reports["effect"].state == "stale"
    assert reports["effect"].status == "pass"


def test_run_conformance_report_executes_fixed_suite_and_refreshes_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = AdminService(config_path)
    service.project_dir = REPO_ROOT
    calls: list[dict[str, Any]] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        text: bool,
        capture_output: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(
            {
                "command": command,
                "cwd": cwd,
                "env": env,
                "text": text,
                "capture_output": capture_output,
                "timeout": timeout,
            }
        )
        Path(env["AGENT_SANDBOX_TRIGGER_CONFORMANCE_REPORT"]).write_text(
            json.dumps(
                {
                    "generated_at": "2099-01-01T00:00:00Z",
                    "status": "pass",
                    "requirements": [
                        {
                            "requirement_id": "schema:trigger:cron",
                            "status": "pass",
                            "coverage": ["cron"],
                            "details": "Cron trigger validates.",
                        }
                    ],
                    "traceability_matrix": {"task_trigger_schema": ["schema:trigger:cron"]},
                    "missing_coverage": {},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(admin_service.subprocess, "run", fake_run)

    payload = service.run_conformance_report("trigger")

    assert payload.kind == "trigger"
    assert payload.exit_code == 0
    assert payload.output == "ok"
    assert len(calls) == 1
    assert calls[0]["command"][1:] == ["-m", "pytest", "-q", "tests/test_trigger_conformance_suite.py"]
    assert calls[0]["cwd"] == REPO_ROOT
    assert calls[0]["text"] is True
    assert calls[0]["capture_output"] is True
    assert calls[0]["timeout"] == admin_service.CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS
    assert (
        calls[0]["env"]["AGENT_SANDBOX_TRIGGER_CONFORMANCE_REPORT"]
        == str(runtime_dir / "trigger-conformance-report.json")
    )
    reports = {report.kind: report for report in payload.reports.reports}
    assert reports["trigger"].state == "pass"
    assert reports["trigger"].requirement_count == 1
    assert reports["effect"].state == "missing"


def test_run_conformance_report_rejects_unknown_kind(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(ValueError, match="Unknown conformance report"):
        service.run_conformance_report("workflow")


def test_run_conformance_report_timeout_decodes_captured_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.project_dir = REPO_ROOT

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        text: bool,
        capture_output: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, env, text, capture_output
        raise subprocess.TimeoutExpired(
            command,
            timeout=timeout,
            output=b"partial stdout\n",
            stderr=b"partial stderr\n",
        )

    monkeypatch.setattr(admin_service.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        service.run_conformance_report("trigger")

    message = str(exc_info.value)
    assert "Trigger conformance suite timed out after" in message
    assert "partial stdout" in message
    assert "partial stderr" in message
    assert "b'partial" not in message


def test_run_conformance_report_returns_payload_for_pytest_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service.project_dir = REPO_ROOT

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        text: bool,
        capture_output: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, env, text, capture_output, timeout
        Path(env["AGENT_SANDBOX_EFFECT_CONFORMANCE_REPORT"]).write_text(
            json.dumps(
                {
                    "generated_at": "2099-01-01T00:00:00Z",
                    "status": "fail",
                    "requirements": [
                        {
                            "requirement_id": "binding:resolution-and-chaining",
                            "status": "fail",
                            "coverage": ["binding"],
                            "details": "Binding coverage failed.",
                        }
                    ],
                    "traceability_matrix": {
                        "task_binding_and_chain": ["binding:resolution-and-chaining"]
                    },
                    "missing_coverage": {
                        "task_queue_idempotency": ["queue:idempotency"],
                    },
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 1, stdout="failed stdout", stderr="failed stderr")

    monkeypatch.setattr(admin_service.subprocess, "run", fake_run)

    payload = service.run_conformance_report("effect")

    assert payload.exit_code == 1
    assert payload.output == "failed stdout\nfailed stderr"
    reports = {report.kind: report for report in payload.reports.reports}
    assert reports["effect"].state == "fail"
    assert reports["effect"].missing_coverage_count == 1


def test_workflow_editor_rejects_graph_metadata_that_crosses_connected_components(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(ValueError, match="node_ids must match one connected graph"):
        service.validate_workflow_editor_document(
            config={
                "runtime_dir": str(tmp_path / "runtime"),
                "triggers": [
                    {
                        "id": "trigger-a",
                        "display_name": "Trigger A",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    },
                    {
                        "id": "trigger-b",
                        "display_name": "Trigger B",
                        "type": "trigger:cron",
                        "expression": "*/5 * * * *",
                    },
                ],
                "effects": [],
                "edges": [],
            },
            layout={},
            graphs=[
                {
                    "id": "graph-crossed",
                    "name": "Crossed graph",
                    "node_ids": ["trigger-a", "trigger-b"],
                }
            ],
        )


def test_workflow_editor_validation_rejects_unknown_layout_nodes(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(
        ValueError, match="Workflow layout references unknown node IDs: missing-node"
    ):
        service.validate_workflow_editor_document(
            config={
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
            layout={"nodes": {"missing-node": {"x": 1, "y": 2}}},
        )


def test_workflow_editor_validation_derives_filter_outputs_server_side(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    result = service.validate_workflow_editor_document(
        config={
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
                    "id": "effect-vm-create-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-1",
                }
            ],
            "gates": [
                {
                    "id": "gate-filter-1",
                    "display_name": "Check if successfully created",
                    "type": "gate:filter",
                    "outputs": {},
                    "when": {
                        "all": [
                            {"left": "${{effect-vm-create-1.success}}", "equals": True},
                            {"left": "${{effect-vm-create-1.session_id}}", "is_not_null": True},
                        ]
                    },
                }
            ],
            "edges": [
                {"source_id": "cron-15m", "target_id": "effect-vm-create-1"},
                {"source_id": "effect-vm-create-1", "target_id": "gate-filter-1"},
            ],
        },
        layout={},
    )

    gate = next(item for item in result.config["gates"] if item["id"] == "gate-filter-1")
    assert gate["outputs"] == {
        "provider": {"type": "string"},
        "session_id": {"type": "string"},
        "labels": {"type": "object"},
        "success": {"type": "boolean"},
        "message": {"type": "string"},
        "vm_name": {"type": "string"},
    }


def test_workflow_editor_validation_derives_deserialize_outputs_server_side(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))

    result = service.validate_workflow_editor_document(
        config={
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "cron-15m",
                    "display_name": "Every 15 minutes",
                    "type": "trigger:cron",
                    "expression": "*/15 * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-deserialize-1",
                    "display_name": "Deserialize",
                    "type": "gate:deserialize",
                    "content": '{"title":"Fix relay","metadata":{"priority":"P1"}}',
                    "format": "json",
                }
            ],
            "effects": [
                {
                    "id": "prompt-agent",
                    "display_name": "Prompt agent",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Review ${{gate-deserialize-1.metadata.priority}}",
                }
            ],
            "edges": [
                {"source_id": "cron-15m", "target_id": "gate-deserialize-1"},
                {"source_id": "gate-deserialize-1", "target_id": "prompt-agent"},
            ],
        },
        layout={},
    )

    gate = next(item for item in result.config["gates"] if item["id"] == "gate-deserialize-1")
    assert gate["outputs"] == {
        "title": {"type": "string"},
        "metadata": {"type": "object"},
    }
    assert result.validation_diagnostics == []


def test_workflow_editor_validation_derives_serialize_outputs_server_side(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))

    result = service.validate_workflow_editor_document(
        config={
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "cron-15m",
                    "display_name": "Every 15 minutes",
                    "type": "trigger:cron",
                    "expression": "*/15 * * * *",
                    "outputs": {"ticket": {"type": "object"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-serialize-1",
                    "display_name": "Serialize",
                    "type": "gate:serialize",
                    "value": "${{cron-15m.ticket}}",
                    "format": "json",
                }
            ],
            "effects": [
                {
                    "id": "prompt-agent",
                    "display_name": "Prompt agent",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Serialized ${{gate-serialize-1.content}}",
                }
            ],
            "edges": [
                {"source_id": "cron-15m", "target_id": "gate-serialize-1"},
                {"source_id": "gate-serialize-1", "target_id": "prompt-agent"},
            ],
        },
        layout={},
    )

    gate = next(item for item in result.config["gates"] if item["id"] == "gate-serialize-1")
    assert gate["outputs"] == {
        "content": {"type": "string"},
        "format": {"type": "string"},
    }
    assert result.validation_diagnostics == []


def test_workflow_editor_save_allows_disconnected_draft_nodes(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    result = service.save_workflow_editor_document(
        config={
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
        layout={
            "nodes": {
                "cron-15m": {"x": 100, "y": 50},
                "prompt-agent": {"x": 300, "y": 50},
            }
        },
    )

    assert result.config["edges"] == []
    reread = service.read_workflow_editor_document()
    assert reread.config["effects"][0]["id"] == "prompt-agent"


def test_workflow_editor_save_writes_via_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    replace_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def tracking_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        replace_calls.append((os.fspath(src), os.fspath(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(admin_service.os, "replace", tracking_replace)

    service.save_workflow_editor_document(
        config={
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
        layout={"nodes": {"cron-15m": {"x": 100, "y": 50}, "prompt-agent": {"x": 300, "y": 50}}},
    )

    assert len(replace_calls) == 2
    assert any(
        dst.endswith("config/trigger-orchestrator/config.json") for _src, dst in replace_calls
    )
    assert any(
        dst.endswith("config/trigger-orchestrator/layout.json") for _src, dst in replace_calls
    )


def test_create_issue_vm_uses_directory_vm_defaults_overlay_for_guest_env(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.create_directory_child("root", "team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "vm",
        {
            "gateway_url": "http://10.20.30.40:8000/mcp",
            "openai_relay_port": 12000,
            "nuget_relay_port": 13000,
        },
    )
    assert write_result.ok is True

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="123",
        session_id="issue-123-session",
        directory_path=team_path,
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["GATEWAY_URL"] == "http://10.20.30.40:8000/mcp"
    assert captured_env["LLM_BASE_URL"] == "http://10.20.30.40:12000/v1"
    assert captured_env["NUGET_RELAY_BASE_URL"] == "http://10.20.30.40:13000"


def test_create_issue_vm_uses_issue_directory_when_called_with_yaml_path(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.create_directory_child("root", "team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "vm",
        {
            "gateway_url": "http://10.20.30.40:8000/mcp",
            "openai_relay_port": 12000,
            "nuget_relay_port": 13000,
        },
    )
    assert write_result.ok is True
    issue = service.create_local_issue(title="Team issue", body="body", directory_path=team_path)

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue=issue.yaml_path,
        session_id="issue-1-session",
        model="gpt-5.2-codex",
    )

    manifest = service.runtime.session_repository.get_session("issue-1-session")
    assert result.success is True
    assert manifest is not None
    assert manifest.directory_path == team_path
    assert captured_env["GATEWAY_URL"] == "http://10.20.30.40:8000/mcp"


def test_create_issue_vm_uses_yaml_path_owner_when_issue_number_is_ambiguous(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    team_a = service.create_directory_child("root", "team-a")
    team_b = service.create_directory_child("root", "team-b")
    assert team_a.ok is True and team_a.data is not None
    assert team_b.ok is True and team_b.data is not None
    team_a_path = team_a.data.details.directory.path
    team_b_path = team_b.data.details.directory.path

    assert (
        service.write_directory_domain_config(
            team_a_path,
            "vm",
            {
                "gateway_url": "http://10.20.30.41:8000/mcp",
                "openai_relay_port": 12001,
                "nuget_relay_port": 13001,
            },
        ).ok
        is True
    )
    assert (
        service.write_directory_domain_config(
            team_b_path,
            "vm",
            {
                "gateway_url": "http://10.20.30.42:8000/mcp",
                "openai_relay_port": 12002,
                "nuget_relay_port": 13002,
            },
        ).ok
        is True
    )

    (tmp_path / "project-a").mkdir()
    (tmp_path / "project-b").mkdir()

    project_a_source = tmp_path / "project-a" / "proxy-issue-42.yaml"
    project_a_source.write_text(
        "\n".join(
            [
                "issue_number: 42",
                'title: "Project A issue"',
                'description: "from project a"',
                'state: "New"',
                'url: "https://ado.example/work/42"',
                "source:",
                '  organization: "https://dev.azure.com/example"',
                '  project: "project-a"',
                '  work_item_url: "https://dev.azure.com/example/project-a/_workitems/edit/42"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    project_b_source = tmp_path / "project-b" / "proxy-issue-42.yaml"
    project_b_source.write_text(
        "\n".join(
            [
                "issue_number: 42",
                'title: "Project B issue"',
                'description: "from project b"',
                'state: "New"',
                'url: "https://ado.example/work/42"',
                "source:",
                '  organization: "https://dev.azure.com/example"',
                '  project: "project-b"',
                '  work_item_url: "https://dev.azure.com/example/project-b/_workitems/edit/42"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    project_a_issue = service.create_local_issue(
        directory_path=team_a_path,
        yaml_path=str(project_a_source),
    )
    service.create_local_issue(
        directory_path=team_b_path,
        yaml_path=str(project_b_source),
    )

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue=project_a_issue.yaml_path,
        session_id="issue-42-session",
        model="gpt-5.2-codex",
    )

    manifest = service.runtime.session_repository.get_session("issue-42-session")
    assert result.success is True
    assert manifest is not None
    assert manifest.directory_path == team_a_path
    assert captured_env["GATEWAY_URL"] == "http://10.20.30.41:8000/mcp"


def test_create_issue_vm_uses_issue_setup_domain_for_setup_env(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.create_directory_child("root", "team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "issue_setup",
        {
            "git_root": "/tmp/git-remotes",
            "git_host": "10.1.2.3",
            "git_user": "gitbot",
            "git_ssh_alias": "issue-git",
            "post_exec_verification_prompt": "verify everything",
            "enable_guest_heartbeat": True,
            "guest_heartbeat_prompt": "continue working",
        },
    )
    assert write_result.ok is True

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="124",
        session_id="issue-124-session",
        directory_path=team_path,
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_GIT_ROOT"] == "/tmp/git-remotes"
    assert captured_env["AGENT_SANDBOX_GIT_HOST"] == "10.1.2.3"
    assert captured_env["AGENT_SANDBOX_GIT_USER"] == "gitbot"
    assert captured_env["AGENT_SANDBOX_GIT_SSH_ALIAS"] == "issue-git"
    assert captured_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] == "verify everything"
    assert captured_env["ENABLE_GUEST_HEARTBEAT"] == "1"
    assert captured_env["GUEST_HEARTBEAT_PROMPT"] == "continue working"
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "AZURE_DEVOPS_PR_PAT"


def test_create_issue_vm_uses_issue_setup_azure_pat_override(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.create_directory_child("root", "team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "issue_setup",
        {
            "git_root": "/tmp/git-remotes",
            "git_host": "10.1.2.3",
            "git_user": "gitbot",
            "git_ssh_alias": "issue-git",
            "issue_setup_azure_devops_pr_pat_env": "TEAM_ISSUE_SETUP_ADO_PAT",
        },
    )
    assert write_result.ok is True

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="125",
        session_id="issue-125-session",
        directory_path=team_path,
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "TEAM_ISSUE_SETUP_ADO_PAT"


def test_create_issue_vm_uses_issue_setup_fallback_env_when_domain_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_GIT_ROOT", "/tmp/from-env")
    monkeypatch.setenv("AGENT_SANDBOX_GIT_HOST", "10.55.0.1")
    monkeypatch.setenv("AGENT_SANDBOX_GIT_USER", "env-user")
    monkeypatch.setenv("AGENT_SANDBOX_GIT_SSH_ALIAS", "env-git")
    monkeypatch.setenv("AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV", "ENV_PAT_NAME")
    monkeypatch.setenv("AGENT_POST_EXEC_VERIFICATION_PROMPT", "verify from env")
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="126",
        session_id="issue-126-session",
        directory_path="root",
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_GIT_ROOT"] == "/tmp/from-env"
    assert captured_env["AGENT_SANDBOX_GIT_HOST"] == "10.55.0.1"
    assert captured_env["AGENT_SANDBOX_GIT_USER"] == "env-user"
    assert captured_env["AGENT_SANDBOX_GIT_SSH_ALIAS"] == "env-git"
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "ENV_PAT_NAME"
    assert captured_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] == "verify from env"


def test_resolve_agent_model_prefers_session_model_over_issue_setup_default(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.write_directory_domain_config("root", "issue_setup", {"default_model": "gpt-5.2-codex"})
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-123",
        directory_path="root",
        allowed_tools=[],
        model="gpt-5.3-codex",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.session_repository.upsert_session(manifest)

    resolved = service._resolve_agent_model_for_session("session-1")

    assert resolved == "gpt-5.3-codex"


def test_resolve_agent_model_uses_issue_setup_default_when_session_model_missing(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.write_directory_domain_config("root", "issue_setup", {"default_model": "gpt-5.2-codex"})
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-123",
        directory_path="root",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.session_repository.upsert_session(manifest)

    resolved = service._resolve_agent_model_for_session("session-1")

    assert resolved == "gpt-5.2-codex"


def test_resolve_agent_model_fails_when_session_and_issue_setup_models_missing(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-123",
        directory_path="root",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.session_repository.upsert_session(manifest)

    with pytest.raises(
        ValueError, match="missing session.model and missing issue_setup.default_model"
    ):
        service._resolve_agent_model_for_session("session-1")


def test_create_issue_vm_never_uses_pat_secret_as_env_var_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV", raising=False)
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "super-secret-pat")
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="127",
        session_id="issue-127-session",
        directory_path="root",
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "AZURE_DEVOPS_PR_PAT"


def test_create_issue_vm_deletes_session_when_setup_fails_before_vm_exists(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Issue VM", body="create and fail")
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._run_subprocess",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=17, stdout="", stderr="boom"
        ),
    )

    result = service.create_issue_vm(
        issue=issue.issue_number,
        session_id="issue-1-session",
        model="gpt-5.2-codex",
    )

    assert result.success is False
    assert result.message == "boom"
    assert service.runtime.session_repository.get_session("issue-1-session") is None


def test_create_issue_vm_deletes_session_when_dominfo_probe_fails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Issue VM", body="create and fail")
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field",
        lambda vm_name, field_name: (_ for _ in ()).throw(RuntimeError("virsh unavailable")),
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._run_subprocess",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=17, stdout="", stderr="boom"
        ),
    )

    result = service.create_issue_vm(
        issue=issue.issue_number,
        session_id="issue-1-session",
        model="gpt-5.2-codex",
    )

    assert result.success is False
    assert result.message == "boom"
    assert service.runtime.session_repository.get_session("issue-1-session") is None


def test_admin_service_rejects_directory_edits_when_feature_flag_disabled(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    result = service.create_directory_child("root", "team")

    assert result.ok is False
    assert result.errors[0].code == "feature_disabled"


def test_renew_session_sets_expiration_from_now(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14084-session",
            vm_id="vm-14084",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(days=5),
        ),
    )

    before = datetime.now(UTC)
    result = service.renew_session("14084", extension="6h")
    after = datetime.now(UTC)

    renewed = service.runtime.session_repository.get_session("issue-14084-session")
    assert renewed is not None
    assert result.success is True
    assert result.session_id == "issue-14084-session"
    assert before + timedelta(hours=6) <= renewed.expires_at <= after + timedelta(hours=6)


def test_revoke_session_expires_immediately(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    before = datetime.now(UTC)
    result = service.revoke_session("session-1")
    after = datetime.now(UTC)

    revoked = service.runtime.session_repository.get_session("session-1")
    assert revoked is not None
    assert result.success is True
    assert before <= revoked.expires_at <= after


def test_delete_session_removes_detached_session(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            allowed_tools=[],
            created_at=datetime(2026, 3, 25, 8, 0, tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-b",
            allowed_tools=[],
            created_at=datetime(2026, 3, 25, 9, 0, tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.delete_session("session-a")

    remaining = service.runtime.session_repository.read_all()
    assert result.success is True
    assert result.session_id == "session-a"
    assert set(remaining) == {"session-b"}


def test_delete_session_rejects_vm_linked_agent_session(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13301-session",
            vm_id="vm-13301",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    with pytest.raises(ValueError, match="Delete the agent instead"):
        service.delete_session("issue-13301-session")

    assert service.runtime.session_repository.get_session("issue-13301-session") is not None


def test_delete_session_rejects_container_agent_session(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        ),
    )

    with pytest.raises(ValueError, match="Delete the agent instead"):
        service.delete_session("container-session")

    assert service.runtime.session_repository.get_session("container-session") is not None


def test_delete_session_cleans_detached_worker_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="detached-session",
            allowed_tools=[],
            expires_at=now + timedelta(hours=1),
        ),
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="detached-session",
            worker_job_id="job-1",
            worker_url="http://worker.test",
            target_name="detached-target",
            last_status="exited",
        )
    )

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.deleted_sessions: list[str] = []

        def delete_session(self, session_id: str) -> dict[str, object]:
            self.deleted_sessions.append(session_id)
            attempted_at = now
            completed_at = now
            for record in repository.read_all().values():
                if record.session_id != session_id:
                    continue
                record.last_status = "removed"
                record.cleanup_status = "cleanup_succeeded"
                record.cleanup_attempted_at = attempted_at
                record.cleanup_completed_at = completed_at
                record.cleanup_error = None
                record.metadata = {
                    **record.metadata,
                    "worker_status": "removed",
                    "cleanup_status": "cleanup_succeeded",
                    "cleanup_attempted_at": attempted_at.isoformat(),
                    "cleanup_completed_at": completed_at.isoformat(),
                }
                repository.put(record)
            return {"deleted": True, "session_id": session_id}

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service),
    )

    result = service.delete_session("detached-session")

    assert result.success is True
    assert fake_backend.deleted_sessions == ["detached-session"]
    assert service.runtime.session_repository.get_session("detached-session") is None
    assert repository.read_all() == {}
    assert "worker_session_deleted=1" in result.message
    assert "removed_mappings=1" in result.message


def test_cleanup_container_session_records_cleaned_up_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(admin_service, "_utc_now", lambda: now)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=now + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
                "worker_url": "http://worker.test",
            },
        ),
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="container-session",
            worker_job_id="job-1",
            worker_url="http://worker.test",
            target_name="container-target",
            started_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=4),
            last_status="exited",
            directory_path="root",
        )
    )

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.deleted_sessions: list[str] = []

        def delete_session(self, session_id: str) -> dict[str, object]:
            self.deleted_sessions.append(session_id)
            return {"deleted": True, "session_id": session_id}

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service),
    )

    result = service.cleanup_container_session("container-session")

    assert result.success is True
    assert result.action == "cleanup_container_session"
    assert fake_backend.deleted_sessions == ["container-session"]
    updated = service.runtime.session_repository.get_session("container-session")
    assert updated is not None
    assert updated.metadata["container_lifecycle"]["state"] == "cleaned_up"
    assert updated.metadata["container_lifecycle"]["cleanup_status"] == "cleanup_succeeded"
    assert updated.expires_at <= now
    summaries = {item.session_id: item for item in service.list_sessions(include_expired=True)}
    lifecycle = summaries["container-session"].container_lifecycle
    assert lifecycle is not None
    assert lifecycle.state == ContainerSessionLifecycleState.CLEANED_UP
    assert lifecycle.actions.cleanup.supported is False
    assert lifecycle.cleanup_error is None
    assert lifecycle.last_signal_at == now - timedelta(minutes=5)


def test_cleanup_container_session_records_failure_and_keeps_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(admin_service, "_utc_now", lambda: now)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=now + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
                "worker_url": "http://worker.test",
            },
        ),
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="container-session",
            worker_job_id="job-1",
            worker_url="http://worker.test",
            target_name="container-target",
            started_at=now - timedelta(minutes=15),
            finished_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=4),
            last_status="exited",
            directory_path="root",
        )
    )

    class FakeContainerBackend:
        def delete_session(self, session_id: str) -> dict[str, object]:
            attempted_at = now
            completed_at = now
            for record in repository.read_all().values():
                if record.session_id != session_id:
                    continue
                record.last_status = "cleanup_failed"
                record.cleanup_status = "cleanup_failed"
                record.cleanup_attempted_at = attempted_at
                record.cleanup_completed_at = completed_at
                record.cleanup_error = "container worker DELETE /v1/sessions/container-session failed: cleanup exploded"
                record.metadata = {
                    **record.metadata,
                    "worker_status": "cleanup_failed",
                    "cleanup_status": "cleanup_failed",
                    "cleanup_attempted_at": attempted_at.isoformat(),
                    "cleanup_completed_at": completed_at.isoformat(),
                    "cleanup_error": record.cleanup_error,
                }
                repository.put(record)
            raise ContainerWorkerClientError(
                f"container worker DELETE /v1/sessions/{session_id} failed: cleanup exploded"
            )

    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: FakeContainerBackend(), service),
    )

    result = service.cleanup_container_session("container-session")

    assert result.success is False
    assert result.action == "cleanup_container_session"
    assert "cleanup exploded" in result.message
    updated = service.runtime.session_repository.get_session("container-session")
    assert updated is not None
    assert updated.metadata["container_lifecycle"]["state"] == "cleanup_failed"
    assert updated.metadata["container_lifecycle"]["cleanup_status"] == "cleanup_failed"
    assert "cleanup exploded" in updated.metadata["container_lifecycle"]["cleanup_error"]
    summaries = {item.session_id: item for item in service.list_sessions(include_expired=True)}
    lifecycle = summaries["container-session"].container_lifecycle
    assert lifecycle is not None
    assert lifecycle.state == ContainerSessionLifecycleState.CLEANUP_FAILED
    assert lifecycle.actions.cleanup.supported is True
    assert lifecycle.cleanup_error == "container worker DELETE /v1/sessions/container-session failed: cleanup exploded"
    assert lifecycle.last_signal_at == now - timedelta(minutes=5)


def test_delete_admin_vm_agent_removes_chat_assistant_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-shared",
            vm_id="assistant-shared",
            vm_provider="libvirt",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    mappings = JsonChatAssistantSessionRepository(
        service.config.runtime_dir / "chat-assistant-session-mappings.json"
    )
    mappings.upsert_mapping(
        user_id="operator@example.com",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        session_id="assistant-shared",
        agent_thread_id="",
        thread_id="thread-1",
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="destroyed", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.delete_admin_agent("assistant-shared", backend="libvirt_vm")

    assert result["success"] is True
    assert result["status"] == "deleted"
    assert service.runtime.session_repository.get_session("assistant-shared") is None
    assert (
        mappings.get_mapping(
            user_id="operator@example.com",
            backend="libvirt_vm",
            assistant_scope="admin_chat",
        )
        is None
    )
    assert "removed_assistant_mappings=1" in result["message"]


def test_delete_admin_agent_deletes_container_session_and_prunes_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        ),
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="container-session",
            worker_job_id="job-1",
            worker_url="http://worker.test",
            target_name="container-target",
            last_status="exited",
        )
    )
    service.config.pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:6070": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6070",
                        "session_id": "container-session",
                    },
                    "azure_devops:org/other:1": {
                        "provider": "azure_devops",
                        "repo": "org/other",
                        "pr_id": "1",
                        "session_id": "other-session",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    feedback_store = PrFeedbackStore(service.config.runtime_dir / "pr-feedback-store.json")
    feedback_store.set_cursor("azure_devops:org/repo:6070", "2026-05-15T10:00:00Z")
    feedback_store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="azure_devops:thread_comment:6070:1",
            provider="azure_devops",
            repo="org/repo",
            pr_id="6070",
            thread_id="6070",
            comment_id="1",
            author="reviewer",
            body="Looks good",
            created_at=datetime(2026, 5, 15, 10, 0, tzinfo=UTC),
        )
    )

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.deleted_sessions: list[str] = []

        def delete_session(self, session_id: str) -> dict[str, object]:
            self.deleted_sessions.append(session_id)
            return {"deleted": True, "session_id": session_id}

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service),
    )

    result = service.delete_admin_agent("container-session", backend="container_worker_vm")

    assert result["success"] is True
    assert result["status"] == "deleted"
    assert result["action"] == "delete"
    assert fake_backend.deleted_sessions == ["container-session"]
    pr_map_payload = json.loads(service.config.pr_map_path.read_text(encoding="utf-8"))
    assert set(pr_map_payload["entries"]) == {"azure_devops:org/other:1"}
    feedback_payload = PrFeedbackStore(
        service.config.runtime_dir / "pr-feedback-store.json"
    ).read_raw()
    assert feedback_payload["cursors"] == {}
    assert feedback_payload["feedback"] == {}
    assert service.runtime.session_repository.get_session("container-session") is None
    assert repository.read_all() == {}


def test_delete_admin_agent_deletes_prepared_container_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
                "worker_url": "http://worker.test",
            },
        ),
    )

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.destroyed: list[str] = []
            self.deleted_sessions: list[str] = []

        def destroy(self, target: str) -> None:
            self.destroyed.append(target)

        def delete_session(self, session_id: str) -> dict[str, object]:
            self.deleted_sessions.append(session_id)
            return {"deleted": True, "session_id": session_id}

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service),
    )

    result = service.delete_admin_agent("container-session", backend="container_worker_vm")

    assert result["success"] is True
    assert result["status"] == "deleted"
    assert fake_backend.destroyed == []
    assert fake_backend.deleted_sessions == ["container-session"]
    assert service.runtime.session_repository.get_session("container-session") is None


def test_delete_admin_agent_destroys_running_container_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        ),
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-1",
            session_id="container-session",
            worker_job_id="job-1",
            worker_url="http://worker.test",
            target_name="container-target",
            last_status="running",
        )
    )

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.destroyed: list[str] = []
            self.deleted_sessions: list[str] = []

        def destroy(self, target: str) -> None:
            self.destroyed.append(target)

        def delete_session(self, session_id: str) -> dict[str, object]:
            self.deleted_sessions.append(session_id)
            return {"deleted": True, "session_id": session_id}

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service),
    )

    result = service.delete_admin_agent("exec-1", backend="container_worker_vm")

    assert result["success"] is True
    assert result["status"] == "deleted"
    assert fake_backend.destroyed == ["exec-1"]
    assert fake_backend.deleted_sessions == ["container-session"]
    assert service.runtime.session_repository.get_session("container-session") is None
    assert repository.read_all() == {}


def test_set_session_allowed_tools_updates_manifest(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.set_session_allowed_tools(
        "13302",
        [
            ToolName.COMPLETE_ISSUE.value,
            ToolName.COMPLETE_ISSUE.value,
            ToolName.APPROVAL_CHECK.value,
        ],
    )

    updated = service.runtime.session_repository.get_session("issue-13302-session")
    assert result.success is True
    assert result.session_id == "issue-13302-session"
    assert updated is not None
    assert updated.allowed_tools == [ToolName.COMPLETE_ISSUE, ToolName.APPROVAL_CHECK]


def test_set_session_allowed_script_names_updates_manifest(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            allowed_script_names=["old-script"],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.set_session_allowed_script_names(
        "13302",
        [" deploy-review ", "collect-logs", "deploy-review"],
    )

    updated = service.runtime.session_repository.get_session("issue-13302-session")
    assert result.success is True
    assert result.session_id == "issue-13302-session"
    assert updated is not None
    assert updated.allowed_script_names == ["deploy-review", "collect-logs"]


def test_update_session_policy_updates_allowlists_and_metadata(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            allowed_script_names=["old-script"],
            approval_required_tools=[ToolName.COMPLETE_ISSUE],
            tool_argument_policies={
                "create_ado_backlog_item": [
                    {
                        "id": "session-policy",
                        "constraints": [
                            {
                                "id": "title",
                                "path": "title",
                                "operator": "required",
                            }
                        ],
                    }
                ]
            },
            discord_channel_allowlist=["old-discord"],
            teams_channel_allowlist=["team-a/channel-1"],
            teams_chat_allowlist=["chat:chat-old"],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            metadata={"agent_thread_id": "old-thread", "keep": "value"},
        ),
    )

    result = service.update_session_policy(
        "13302",
        {
            "approval_required_tools": [" complete_issue ", "approval_submit"],
            "tool_argument_policies": {
                "create_ado_backlog_item": [
                    {
                        "id": "session-policy",
                        "constraints": [
                            {
                                "id": "title",
                                "path": "title",
                                "operator": "required",
                            },
                            {
                                "id": "iteration",
                                "path": "iteration",
                                "operator": "required",
                            },
                        ],
                    }
                ]
            },
            "allowed_script_names": [" deploy-review ", "deploy-review", "collect-logs"],
            "discord_channel_allowlist": [" chan-a ", "chan-a", "chan-b"],
            "teams_channel_allowlist": [" team-b/channel-2 ", "chat:chat-1"],
            "teams_chat_allowlist": [" chat:chat-2 ", "chat:chat-2", "chat:chat-3 "],
            "communication_file_receive_enabled": True,
            "communication_file_send_enabled": True,
            "communication_file_max_bytes": 2048,
            "communication_file_allowed_mime_types": ["text/plain", " text/plain ", "image/png"],
            "communication_file_allowed_extensions": [".txt", " txt ", ".png"],
            "communication_file_blocked_extensions": [".exe", " exe "],
            "communication_file_require_approval_for_send": True,
            "metadata": {"keep": "new-value"},
        },
    )

    updated = service.runtime.session_repository.get_session("issue-13302-session")
    assert result.success is True
    assert result.session_id == "issue-13302-session"
    assert updated is not None
    assert updated.approval_required_tools == [
        ToolName.COMPLETE_ISSUE,
        ToolName.APPROVAL_SUBMIT,
    ]
    assert updated.tool_argument_policies["create_ado_backlog_item"][0].id == "session-policy"
    assert updated.allowed_script_names == ["deploy-review", "collect-logs"]
    assert updated.discord_channel_allowlist == ["chan-a", "chan-b"]
    assert updated.teams_channel_allowlist == ["team-b/channel-2", "chat:chat-1"]
    assert updated.teams_chat_allowlist == ["chat:chat-2", "chat:chat-3"]
    assert updated.communication_file_receive_enabled is True
    assert updated.communication_file_send_enabled is True
    assert updated.communication_file_max_bytes == 2048
    assert updated.communication_file_allowed_mime_types == ["text/plain", "image/png"]
    assert updated.communication_file_allowed_extensions == [".txt", "txt", ".png"]
    assert updated.communication_file_blocked_extensions == [".exe", "exe"]
    assert updated.communication_file_require_approval_for_send is True
    assert updated.metadata == {"keep": "new-value"}


def test_effective_tool_policy_payload_combines_directory_and_session_rules(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.write_directory_domain_config(
        "root",
        "policy",
        {
            "approval_required_tools": ["approval_check"],
            "tool_argument_policies": {
                "create_ado_backlog_item": [
                    {
                        "id": "directory-policy",
                        "constraints": [
                            {
                                "id": "project",
                                "path": "project",
                                "operator": "fixed",
                                "value": "Example Project",
                            }
                        ],
                    }
                ]
            },
        },
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE, ToolName.APPROVAL_CHECK],
            approval_required_tools=[ToolName.COMPLETE_ISSUE],
            tool_argument_policies={
                "create_ado_backlog_item": [
                    {
                        "id": "session-policy",
                        "constraints": [
                            {
                                "id": "title",
                                "path": "title",
                                "operator": "required",
                            }
                        ],
                    }
                ]
            },
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    effective = service.effective_tool_policy_payload(
        service.runtime.session_repository.get_session("issue-13302-session")
    )

    assert effective is not None
    assert effective["allowed_tools"] == ["claim_next_issue", "approval_check"]
    assert effective["approval_required_tools"] == [
        "approval_check",
        "complete_issue",
    ]
    assert [item["id"] for item in effective["tool_argument_policies"]["create_ado_backlog_item"]] == [
        "directory-policy",
        "session-policy",
    ]


def test_update_admin_agent_updates_chat_allowlist(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="agent-13304",
            vm_id="vm-13304",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.update_admin_agent(
        "agent-13304",
        payload={"teams_chat_allowlist": [" chat:chat-4 ", "chat:chat-4"]},
    )

    updated = service.runtime.session_repository.get_session("agent-13304")
    assert result["status"] == "updated"
    assert updated is not None
    assert updated.teams_chat_allowlist == ["chat:chat-4"]


def test_set_session_model_updates_manifest(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13303-session",
            vm_id="vm-13303",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.set_session_model("13303", "  gpt-5.5  ")

    updated = service.runtime.session_repository.get_session("issue-13303-session")
    assert result.success is True
    assert result.session_id == "issue-13303-session"
    assert updated is not None
    assert updated.model == "gpt-5.5"


def test_set_session_model_rejects_empty_value(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13304-session",
            vm_id="vm-13304",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    with pytest.raises(ValueError, match="model must be non-empty"):
        service.set_session_model("13304", "   ")


def test_list_background_workers_reads_systemd_state(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))

    payloads = {
        "agent-sandbox-ado-worker.service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "UnitFileState": "enabled",
            "Description": "ADO worker",
            "MainPID": "123",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
        "agent-sandbox-proxy-issue-sync.service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "UnitFileState": "enabled",
            "Description": "Proxy issue sync worker",
            "MainPID": "124",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
        "agent-sandbox-discord-ingest.service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "UnitFileState": "enabled",
            "Description": "Discord ingest worker",
            "MainPID": "456",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
        "agent-sandbox-discord-wake.service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "UnitFileState": "enabled",
            "Description": "Discord wake worker",
            "MainPID": "555",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
        "agent-sandbox-scheduled-wake.service": {
            "ActiveState": "failed",
            "SubState": "failed",
            "Result": "failed",
            "UnitFileState": "enabled",
            "Description": "Scheduled wake worker",
            "MainPID": "0",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "2",
        },
        "agent-sandbox-teams-ingest.service": {
            "ActiveState": "inactive",
            "SubState": "dead",
            "Result": "success",
            "UnitFileState": "disabled",
            "Description": "Teams ingest worker",
            "MainPID": "0",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
        "agent-sandbox-teams-meeting-ingest.service": {
            "ActiveState": "inactive",
            "SubState": "dead",
            "Result": "success",
            "UnitFileState": "disabled",
            "Description": "Teams meeting ingest worker",
            "MainPID": "0",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
        "agent-sandbox-effect-orchestrator.service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "UnitFileState": "enabled",
            "Description": "Effect orchestrator worker",
            "MainPID": "777",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
        "agent-sandbox-trigger-orchestrator.service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "UnitFileState": "enabled",
            "Description": "Trigger orchestrator worker",
            "MainPID": "789",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
    }
    monkeypatch.setattr("agent_sandbox.admin.service._systemctl_show", lambda unit: payloads[unit])

    summaries = service.list_background_workers()

    assert [summary.worker_id for summary in summaries] == [
        "ado",
        "proxy-issue-sync",
        "discord-ingest",
        "discord-wake",
        "scheduled-wake",
        "teams-ingest",
        "teams-meeting-ingest",
        "effect-orchestrator",
        "trigger-orchestrator",
    ]
    assert summaries[0].running is True
    assert summaries[0].failed is False
    assert summaries[0].main_pid == 123
    assert summaries[0].unit_file_state == "enabled"
    assert summaries[1].running is True
    assert summaries[1].failed is False
    assert summaries[1].main_pid == 124
    assert summaries[2].running is True
    assert summaries[2].failed is False
    assert summaries[2].main_pid == 456
    assert summaries[3].running is True
    assert summaries[3].failed is False
    assert summaries[3].main_pid == 555
    assert summaries[4].running is False
    assert summaries[4].failed is True
    assert summaries[4].detail == "load=loaded, exec=1:2"
    assert summaries[5].running is False
    assert summaries[5].failed is False
    assert summaries[5].unit_file_state == "disabled"
    assert summaries[6].running is False
    assert summaries[6].failed is False
    assert summaries[6].unit_file_state == "disabled"
    assert summaries[7].running is True
    assert summaries[7].failed is False
    assert summaries[7].main_pid == 777
    assert summaries[8].running is True
    assert summaries[8].failed is False
    assert summaries[8].main_pid == 789


def test_list_background_workers_marks_unavailable_when_systemctl_fails(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))

    def _raise(unit: str) -> dict[str, str]:
        raise RuntimeError(f"cannot inspect {unit}")

    monkeypatch.setattr("agent_sandbox.admin.service._systemctl_show", _raise)

    summaries = service.list_background_workers()

    assert all(summary.active_state == "unavailable" for summary in summaries)
    assert all(summary.running is False for summary in summaries)
    assert all(summary.failed is False for summary in summaries)


def test_get_background_worker_log_snapshot_returns_unit_journal(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(
        "agent_sandbox.admin.service._journalctl_tail",
        lambda unit, lines: f"{unit} log tail ({lines})",
    )

    text = service.get_background_worker_log_snapshot("ado", lines=25)

    assert "Worker: ADO Worker" in text
    assert "Unit: agent-sandbox-ado-worker.service" in text
    assert "agent-sandbox-ado-worker.service log tail (25)" in text


def test_start_background_worker_controls_systemd_unit(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    calls: list[tuple[str, str]] = []

    class _Completed:
        returncode = 0
        stdout = "started"
        stderr = ""

    def _fake_control(action: str, unit: str):
        calls.append((action, unit))
        return _Completed()

    monkeypatch.setattr("agent_sandbox.admin.service._run_systemd_control", _fake_control)

    result = service.start_background_worker("ado")

    assert calls == [("start", "agent-sandbox-ado-worker.service")]
    assert result.success is True
    assert result.worker_id == "ado"
    assert result.action == "start"
    assert result.message == "started"


def test_stop_background_worker_controls_systemd_unit(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    calls: list[tuple[str, str]] = []

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_control(action: str, unit: str):
        calls.append((action, unit))
        return _Completed()

    monkeypatch.setattr("agent_sandbox.admin.service._run_systemd_control", _fake_control)

    result = service.stop_background_worker("discord-wake")

    assert calls == [("stop", "agent-sandbox-discord-wake.service")]
    assert result.success is True
    assert result.worker_id == "discord-wake"
    assert result.action == "stop"
    assert "Stopped Discord Wake Worker" == result.message


def test_background_worker_actions_reject_unknown_worker(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    start = service.start_background_worker("missing")
    stop = service.stop_background_worker("missing")

    assert start.success is False
    assert stop.success is False
    assert start.message == "Unknown worker: missing"
    assert stop.message == "Unknown worker: missing"


def test_issue_actions_use_local_behavior_for_non_proxy_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Local", body="only local")

    comment_result = service.post_issue_comment(issue.issue_number, "hello local")
    update_result = service.update_issue(
        issue.issue_number, state="Completed", fields={"priority": "high"}
    )
    read_result = service.read_issue(issue.issue_number)

    assert comment_result["source_type"] == "local"
    assert update_result["source_type"] == "local"
    assert read_result["source_type"] == "local"
    assert read_result["state"] == "Completed"
    assert read_result["comments"][-1]["text"] == "hello local"


def test_set_issue_plan_updates_local_issue_metadata_and_yaml(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Planned", body="only local")

    result = service.set_issue_plan(
        issue.issue_number,
        ["Analyze repo", "Implement fix", "Verify tests"],
        summary="Understand the repo, make the fix, and verify the outcome.",
    )
    read_result = service.read_issue(issue.issue_number)
    yaml_text = Path(issue.yaml_path).read_text(encoding="utf-8")

    assert result["source_type"] == "local"
    assert result["updated"] is True
    assert result["step_count"] == 3
    assert result["steps"] == ["Analyze repo", "Implement fix", "Verify tests"]
    assert result["summary"] == "Understand the repo, make the fix, and verify the outcome."
    assert read_result["metadata"]["steps"] == ["Analyze repo", "Implement fix", "Verify tests"]
    assert (
        read_result["metadata"]["plan_summary"]
        == "Understand the repo, make the fix, and verify the outcome."
    )
    assert "steps:" in yaml_text
    assert "plan_summary:" in yaml_text
    assert "- Analyze repo" in yaml_text


def test_set_issue_plan_rejects_empty_steps(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Planned", body="only local")

    with pytest.raises(ValueError, match="steps must contain at least one non-empty item"):
        service.set_issue_plan(issue.issue_number, ["", "   "])


def test_set_issue_plan_clears_existing_summary_when_omitted(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Planned", body="only local")

    service.set_issue_plan(issue.issue_number, ["Analyze repo"], summary="Original summary")
    result = service.set_issue_plan(issue.issue_number, ["Implement fix"])
    read_result = service.read_issue(issue.issue_number)
    yaml_text = Path(issue.yaml_path).read_text(encoding="utf-8")

    assert result["summary"] is None
    assert "plan_summary" not in read_result["metadata"]
    assert "plan_summary:" not in yaml_text


def test_worker_admin_dependencies_backwards_compatible_without_clear_workflow_run() -> None:
    facade = WorkerAdminFacade(
        WorkerAdminDependencies(
            runtime=object(),
            config=object(),
            agent_control_store=object(),
            normalize_directory_path=lambda directory_path: directory_path or "root",
            direct_owned_entity_ids=lambda directory_path, **kwargs: set(),
            read_worker_global_config=lambda worker_id: {"worker_id": worker_id},
            write_worker_global_config=lambda worker_id, payload, **kwargs: {
                "worker_id": worker_id,
                "payload": payload,
                **kwargs,
            },
            read_workflow_editor_document=lambda: {"kind": "document"},
            read_workflow_editor_catalog=lambda: {"kind": "catalog"},
            validate_workflow_editor_document=lambda **kwargs: {"kind": "validation", **kwargs},
            save_workflow_editor_document=lambda **kwargs: {"kind": "save", **kwargs},
            read_workflow_run_snapshot=lambda: {"kind": "snapshot"},
            workflow_event_journal=lambda: object(),
            upsert_trigger_config=lambda trigger_id, payload: {
                "trigger_id": trigger_id,
                "payload": payload,
            },
            delete_trigger_config=lambda trigger_id: {"trigger_id": trigger_id},
            upsert_effect_config=lambda effect_id, payload: {
                "effect_id": effect_id,
                "payload": payload,
            },
            delete_effect_config=lambda effect_id: {"effect_id": effect_id},
        )
    )

    assert facade.read_workflow_editor_document() == {"kind": "document"}
    with pytest.raises(NotImplementedError, match="clear_workflow_run is not configured"):
        facade.clear_workflow_run("evt-1")


def test_get_issue_returns_full_local_record_for_closed_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Closed task", body="only local")
    service.update_issue(issue.issue_number, state="Closed", fields={"Reason": "Out of scope"})

    result = service.get_issue(issue.issue_number)

    assert result["issue_id"] == issue.issue_id
    assert result["issue_number"] == issue.issue_number
    assert result["source_type"] == "local"
    assert result["directory_path"] == "root"
    assert result["state"] == "Closed"
    assert result["reason"] == "Out of scope"
    assert result["rejection_reason"] == "Out of scope"
    assert result["yaml_path"] == issue.yaml_path


def test_create_local_issue_defaults_to_new_state(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    issue = service.create_local_issue(title="Local", body="only local")

    stored = service.local_issue_store.get(issue.issue_id)
    assert stored is not None
    assert stored.state == "New"


def test_update_issue_rejects_invalid_local_state(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Local", body="only local")

    with pytest.raises(ValueError, match="Invalid issue state"):
        service.update_issue(issue.issue_number, state="Done")


def test_update_issue_accepts_approved_state(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Local", body="only local")

    result = service.update_issue(issue.issue_number, state="Approved", fields={"Priority": "P1"})

    stored = service.local_issue_store.get(issue.issue_id)
    assert result["source_type"] == "local"
    assert stored is not None
    assert stored.state == "Approved"
    assert stored.metadata["fields"]["Priority"] == "P1"


def test_update_issue_updates_local_yaml_file(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Task", body="only local")
    original_yaml = Path(issue.yaml_path).read_text(encoding="utf-8")

    result = service.update_issue(issue.issue_number, state="Committed", fields={"Priority": "P1"})

    updated_yaml = Path(issue.yaml_path).read_text(encoding="utf-8")
    assert result["source_type"] == "local"
    assert updated_yaml != original_yaml
    assert "state: Committed" in updated_yaml
    assert "Priority: P1" in updated_yaml


def test_issue_actions_reject_ambiguous_bare_number_across_directories(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    root_yaml = tmp_path / "root-issue.yaml"
    root_yaml.write_text("issue_number: 7\ntitle: Root issue\nbody: Root body\n", encoding="utf-8")
    team_yaml = tmp_path / "team-issue.yaml"
    team_yaml.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")

    root_issue = service.create_local_issue(yaml_path=str(root_yaml), directory_path="root")
    team_issue = service.create_local_issue(yaml_path=str(team_yaml), directory_path=team_path)

    with pytest.raises(ValueError, match=r"Ambiguous local issue identifier: 7\."):
        service.read_issue("7")
    with pytest.raises(ValueError, match=r"Ambiguous local issue identifier: 7\."):
        service.update_issue("7", state="Completed")
    with pytest.raises(ValueError, match=r"Ambiguous local issue identifier: 7\."):
        service.update_local_issue(
            "7",
            title="Edited through bare number",
            body="Edited body",
            state="In Progress",
        )

    root_read = service.read_issue(root_issue.issue_id)
    team_updated = service.update_local_issue(
        team_issue.issue_id,
        title="Edited team issue",
        body="Updated team body",
        state="In Progress",
    )

    assert root_read["title"] == "Root issue"
    assert team_updated.issue_id == team_issue.issue_id
    assert service.local_issue_store.get(root_issue.issue_id).title == "Root issue"


def test_vm_and_delete_actions_reject_ambiguous_bare_number_across_directories(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    root_yaml = tmp_path / "root-issue.yaml"
    root_yaml.write_text("issue_number: 7\ntitle: Root issue\nbody: Root body\n", encoding="utf-8")
    team_yaml = tmp_path / "team-issue.yaml"
    team_yaml.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")

    service.create_local_issue(yaml_path=str(root_yaml), directory_path="root")
    service.create_local_issue(yaml_path=str(team_yaml), directory_path=team_path)

    create_result = service.create_issue_vm_from_local_issue("7")
    delete_result = service.delete_local_issue("7")

    assert create_result.success is False
    assert delete_result.success is False
    assert create_result.message.startswith("Ambiguous local issue identifier: 7.")
    assert delete_result.message.startswith("Ambiguous local issue identifier: 7.")


def test_create_issue_vm_rejects_ambiguous_bare_local_issue_number_in_primary_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    root_yaml = tmp_path / "root-issue.yaml"
    root_yaml.write_text("issue_number: 7\ntitle: Root issue\nbody: Root body\n", encoding="utf-8")
    team_yaml = tmp_path / "team-issue.yaml"
    team_yaml.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")

    service.create_local_issue(yaml_path=str(root_yaml), directory_path="root")
    service.create_local_issue(yaml_path=str(team_yaml), directory_path=team_path)

    def _fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(
            "create_issue_vm should reject ambiguous identifiers before invoking setup"
        )

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fail_run)

    result = service.create_issue_vm(issue="7")

    assert result.success is False
    assert result.message.startswith("Ambiguous local issue identifier: 7.")


def test_claim_next_issue_prefers_highest_priority_committed_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    low = service.create_local_issue(title="Low", body="x")
    high = service.create_local_issue(title="High", body="x")
    service.update_issue(low.issue_number, state="Committed", fields={"Priority": "P2"})
    service.update_issue(high.issue_number, state="Committed", fields={"Priority": "P0"})
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="worker-session",
            vm_id="issue-worker-1-vm",
            directory_path="root",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    claimed = service.claim_next_issue(session_id="worker-session")

    assert claimed is not None
    assert claimed.issue_number == high.issue_number
    assert claimed.state == "In Progress"
    assert claimed.metadata["fields"]["claimed_by_session_id"] == "worker-session"
    assert claimed.metadata["fields"]["claimed_by_vm_id"] == "issue-worker-1-vm"


def test_complete_issue_marks_claimed_issue_completed(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Task", body="x")
    service.update_issue(issue.issue_number, state="Committed", fields={"Priority": "P1"})
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="worker-session",
            vm_id="issue-worker-1-vm",
            directory_path="root",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE, ToolName.COMPLETE_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    claimed = service.claim_next_issue(session_id="worker-session")
    assert claimed is not None

    completed = service.complete_issue(
        session_id="worker-session",
        issue_id=claimed.issue_number,
        summary="Implemented and pushed",
    )

    assert completed.state == "Completed"
    assert completed.metadata["fields"]["completed_summary"] == "Implemented and pushed"
    assert completed.metadata["fields"]["completed_by_session_id"] == "worker-session"


def test_create_local_issue_from_ado_yaml_creates_proxy_record_and_repoll_updates_without_duplicate(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    first_yaml = _write_proxy_issue_yaml(
        tmp_path,
        issue_number=42,
        title="Proxy issue",
        description="from remote",
        state="New",
    )
    updated_yaml = _write_proxy_issue_yaml(
        tmp_path,
        issue_number=42,
        title="Proxy issue updated",
        description="updated body",
        state="Active",
    )

    created = service.create_local_issue(yaml_path=str(first_yaml))
    updated = service.create_local_issue(yaml_path=str(updated_yaml))

    assert created.issue_number == "42"
    assert updated.issue_number == "42"
    issues = service.list_local_issues()
    assert [issue.issue_number for issue in issues] == ["42"]
    stored = service.local_issue_store.get(created.issue_id)
    assert stored is not None
    assert stored.source == "proxy_azure_devops"
    assert stored.source_type == "remote_proxy"
    assert stored.remote_reference is not None
    assert stored.remote_reference.provider == "azure_devops"
    assert stored.remote_reference.work_item_id == "42"
    assert stored.state == "Active"


def test_issue_actions_forward_proxy_issues_and_sync_cache(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(
        title="Proxy issue",
        body="from remote",
        yaml_path=str(_write_proxy_issue_yaml(tmp_path)),
    )

    calls: list[tuple[str, dict[str, Any]]] = []

    class _Adapter:
        provider_name = "azure_devops"

        def read_issue(self, **kwargs):
            calls.append(("read", kwargs))
            return {
                "provider": "azure_devops",
                "remote_issue_id": kwargs["remote_issue_id"],
                "title": "Remote title",
                "description": "Remote description",
                "state": "Active",
                "url": "https://ado.example/work/42",
                "updated_at": "2026-03-28T10:11:12Z",
                "comments": [{"id": "1", "text": "remote comment"}],
                "fields": {"System.State": "Active"},
            }

        def post_comment(self, **kwargs):
            calls.append(("comment", kwargs))
            return {
                "provider": "azure_devops",
                "remote_issue_id": kwargs["remote_issue_id"],
                "comment": {"id": "9"},
            }

        def update_issue(self, **kwargs):
            calls.append(("update", kwargs))
            return {
                "provider": "azure_devops",
                "remote_issue_id": kwargs["remote_issue_id"],
                "updated": True,
            }

    service.issue_service.adapters["azure_devops"] = _Adapter()

    read_result = service.read_issue(
        issue.issue_number, include_remote_comments=True, sync_proxy_cache=True
    )
    comment_result = service.post_issue_comment(
        issue.issue_number, "push to remote", sync_proxy_cache=True
    )
    update_result = service.update_issue(
        issue.issue_number, state="Resolved", fields={"Custom.Priority": "1"}
    )

    assert read_result["source_type"] == "proxy"
    assert comment_result["source_type"] == "proxy"
    assert update_result["source_type"] == "proxy"
    stored = service.local_issue_store.get(issue.issue_id)
    assert stored is not None
    assert stored.title == "Remote title"
    assert stored.state == "Active"
    comment_call = next(kwargs for name, kwargs in calls if name == "comment")
    update_call = next(kwargs for name, kwargs in calls if name == "update")
    assert comment_call["remote_issue_id"] == issue.issue_number
    assert update_call["state"] == "Resolved"


def test_get_issue_retains_local_metadata_for_proxy_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(
        title="Proxy issue",
        body="from remote",
        yaml_path=str(_write_proxy_issue_yaml(tmp_path)),
    )

    class _Adapter:
        provider_name = "azure_devops"

        def read_issue(self, **kwargs):
            return {
                "provider": "azure_devops",
                "remote_issue_id": kwargs["remote_issue_id"],
                "title": "Remote title",
                "description": "Remote description",
                "state": "Closed",
                "comments": [],
                "fields": {"Reason": "Remote reject"},
            }

    service.issue_service.adapters["azure_devops"] = _Adapter()

    result = service.get_issue(
        issue.issue_number, include_remote_comments=True, sync_proxy_cache=True
    )

    assert result["issue_id"] == issue.issue_id
    assert result["issue_number"] == issue.issue_number
    assert result["source_type"] == "proxy"
    assert result["directory_path"] == issue.directory_path
    assert result["yaml_path"] == issue.yaml_path
    assert result["state"] == "Closed"


def test_proxy_identifier_rejects_ambiguous_ado_work_item_ids_across_projects(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    project_a_issue = service.create_local_issue(
        yaml_path=str(
            _write_proxy_issue_yaml(
                tmp_path,
                issue_number=42,
                title="Project A issue",
                description="from project a",
                project="project-a",
            )
        )
    )
    project_b_issue = service.create_local_issue(
        yaml_path=str(
            _write_proxy_issue_yaml(
                tmp_path,
                issue_number=42,
                title="Project B issue",
                description="from project b",
                project="project-b",
            )
        )
    )

    with pytest.raises(ValueError, match=r"Ambiguous proxy issue identifier: proxy:ado/42\."):
        service.read_issue("proxy:ado/42")
    with pytest.raises(ValueError, match=r"Ambiguous proxy issue identifier: proxy:ado/42\."):
        service.update_issue("proxy:ado/42", state="Resolved")
    with pytest.raises(ValueError, match=r"Ambiguous proxy issue identifier: proxy:ado/42\."):
        service.post_issue_comment("proxy:ado/42", "push to remote")

    assert (
        service._resolve_local_issue_record(project_a_issue.issue_id).issue_id
        == project_a_issue.issue_id
    )
    assert (
        service._resolve_local_issue_record(project_b_issue.issue_id).issue_id
        == project_b_issue.issue_id
    )


def test_issue_actions_reject_unknown_proxy_identifier(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(ValueError, match=r"Unknown local issue identifier: proxy:ado/999"):
        service.read_issue("proxy:ado/999")
    with pytest.raises(ValueError, match=r"Unknown local issue identifier: proxy:ado/999"):
        service.update_issue("proxy:ado/999", state="Resolved")
    with pytest.raises(ValueError, match=r"Unknown local issue identifier: proxy:ado/999"):
        service.post_issue_comment("proxy:ado/999", "push to remote")


def test_create_issue_vm_accepts_unified_proxy_identifier(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    service.create_local_issue(
        yaml_path=str(
            _write_proxy_issue_yaml(
                tmp_path,
                issue_number=77,
                title="Proxy setup",
                description="from ado",
                state="Active",
            )
        )
    )
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.create_issue_vm(issue="proxy:ado/77", model="gpt-5.2-codex")

    assert result.success is True
    assert any(str(part).endswith("setup_vm.sh") for part in captured["cmd"])
    assert "vm-77" in captured["cmd"]
    assert captured["cmd"][-1] == "agent"


def test_create_issue_vm_from_local_issue_rejects_unknown_proxy_identifier_without_setup(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))

    def _fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(
            "create_issue_vm_from_local_issue should reject unknown identifiers before setup"
        )

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fail_run)

    result = service.create_issue_vm_from_local_issue("proxy:ado/999")

    assert result.success is False
    assert result.message == "Unknown local issue identifier: proxy:ado/999"


def test_create_issue_vm_from_local_issue_accepts_unified_proxy_identifier(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    service.create_local_issue(
        yaml_path=str(
            _write_proxy_issue_yaml(
                tmp_path,
                issue_number=77,
                title="Proxy setup",
                description="from ado",
                state="Active",
            )
        )
    )
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.create_issue_vm_from_local_issue("proxy:ado/77", model="gpt-5.2-codex")

    assert result.success is True
    assert any(str(part).endswith("setup_vm.sh") for part in captured["cmd"])
    assert "vm-77" in captured["cmd"]
    assert captured["cmd"][-1] == "agent"


def test_create_issue_vm_from_local_issue_rejects_ambiguous_proxy_identifier(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    service.create_local_issue(
        yaml_path=str(
            _write_proxy_issue_yaml(
                tmp_path,
                issue_number=42,
                title="Project A issue",
                description="from project a",
                project="project-a",
            )
        )
    )
    service.create_local_issue(
        yaml_path=str(
            _write_proxy_issue_yaml(
                tmp_path,
                issue_number=42,
                title="Project B issue",
                description="from project b",
                project="project-b",
            )
        )
    )

    def _fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("create_issue_vm_from_local_issue should reject ambiguous identifiers before setup")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fail_run)

    result = service.create_issue_vm_from_local_issue("proxy:ado/42")

    assert result.success is False
    assert result.message.startswith("Ambiguous proxy issue identifier: proxy:ado/42.")


def test_create_vm_launches_generic_vm_and_bootstraps_selected_toolchains(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "vm",
        {
            "gateway_url": "http://10.20.30.40:8000/mcp",
            "openai_relay_port": 12000,
            "nuget_relay_port": 13000,
        },
    )
    assert write_result.ok is True

    monkeypatch.setenv("SSH_PUBLIC_KEY_FILE", "/home/test-user/.ssh/gh_id_ed25519.pub")
    monkeypatch.setenv("SSH_PRIVATE_KEY_PATH", "/home/test-user/.ssh/gh_id_ed25519")
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs.get("env")))
        script_name = next(
            (Path(part).name for part in cmd if part.endswith(".sh")), Path(cmd[0]).name
        )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=f"ok:{script_name}", stderr=""
        )

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        labels={"workflow": "issue-worker", "agent_role": "issue"},
        dotnet=True,
        nuget=True,
        playwright=True,
        directory_path=team_path,
    )

    assert result.success is True
    assert [cmd[-1] for cmd, _env in calls] == ["agent"]
    assert calls[0][0][:2] == ["sudo", "-n"]
    assert any(
        part.startswith("--preserve-env=")
        and "SSH_PUBLIC_KEY_FILE" in part
        and "SSH_PRIVATE_KEY_PATH" in part
        for part in calls[0][0]
    )
    assert calls[0][0][-7:] == [
        str(Path(__file__).resolve().parents[1] / "scripts" / "setup_vm.sh"),
        "--dotnet",
        "--nuget",
        "--playwright",
        "lab-vm",
        "lab-session",
        "agent",
    ]
    setup_env = calls[0][1]
    assert setup_env is not None
    assert setup_env["GATEWAY_URL"] == "http://10.20.30.40:8000/mcp"
    assert setup_env["LLM_BASE_URL"] == "http://10.20.30.40:12000/v1"
    assert setup_env["NUGET_RELAY_BASE_URL"] == "http://10.20.30.40:13000"
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.vm_id == "lab-vm"
    assert manifest.directory_path == team_path
    assert manifest.metadata == {"labels": {"workflow": "issue-worker", "agent_role": "issue"}}
    assert result.message == "ok:setup_vm.sh"


def test_create_vm_falls_back_to_pkexec_when_sudo_requires_prompt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))

    monkeypatch.setenv("SSH_PUBLIC_KEY_FILE", "/home/test-user/.ssh/id_ed25519.pub")
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr("agent_sandbox.admin.service._can_prompt_for_privileges", lambda: True)

    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["sudo", "-n"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="sudo: a password is required"
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok:setup_vm.sh", stderr=""
        )

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.create_vm(vm_name="lab-vm", session_id="lab-session")

    assert result.success is True
    assert calls[0][:2] == ["sudo", "-n"]
    assert calls[1][:2] == ["pkexec", "env"]


def test_create_vm_surfaces_noninteractive_sudo_failure_without_pkexec_fallback(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr("agent_sandbox.admin.service._can_prompt_for_privileges", lambda: False)

    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="", stderr="sudo: a password is required"
        )

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.create_vm(vm_name="lab-vm", session_id="lab-session")

    assert result.success is False
    assert result.message == "Failed to create VM lab-vm: sudo: a password is required"
    assert len(calls) == 1
    assert calls[0][:2] == ["sudo", "-n"]


def test_list_vms_surfaces_shared_session_labels(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest.model_validate(
            {
                "session_id": "session-1",
                "vm_id": "lab-vm",
                "directory_path": "root",
                "allowed_tools": [],
                "expires_at": "2099-01-01T00:00:00Z",
                "metadata": {"labels": {"workflow": "issue-worker", "agent_role": "issue"}},
            }
        ),
    )

    monkeypatch.setattr("agent_sandbox.admin.service._virsh_lines", lambda args: ["lab-vm"])
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: "running"
    )
    monkeypatch.setattr("agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.0.2.10")
    monkeypatch.setattr(
        "agent_sandbox.admin.service._probe_agent_state",
        lambda vm_name, vm_ip, vm_user: (
            "idle",
            ["sample-repo"],
            datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            None,
            None,
        ),
    )

    summaries = service.list_vms()

    assert len(summaries) == 1
    assert summaries[0].labels == {"workflow": "issue-worker", "agent_role": "issue"}


def test_kill_agent_terminates_selected_guest_pid(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: "running"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ssh_access",
        lambda vm_name, vm_user: ("192.168.122.10", Path("/tmp/test-key")),
    )

    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="terminated pid=4321 state_files=1\n", stderr=""
        )

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", _fake_run)

    result = service.kill_agent("vm-123", 4321)

    assert result.success is True
    assert result.vm_name == "vm-123"
    assert result.action == "kill_agent"
    assert result.message == "terminated pid=4321 state_files=1"
    assert captured["cmd"] == [
        "ssh",
        *admin_service._ssh_options("vm-123", "192.168.122.10", Path("/tmp/test-key")),
        "agent-admin@192.168.122.10",
        "sudo",
        "-n",
        "env",
        "AGENT_USER=agent",
        "TARGET_PID=4321",
        "bash",
        "-s",
    ]
    remote_script = str(captured["input"])
    assert 'write_idle_state "${state_path}" "${repo_name}" "${dispatch_id}"' in remote_script
    assert (
        'for pid_file in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}/codex-heartbeat.pid"; do'
        in remote_script
    )


def test_kill_agent_falls_back_to_legacy_agent_user(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: "running"
    )

    def _fake_resolve_vm_ssh_access(vm_name: str, vm_user: str):
        if vm_user == "agent-admin":
            raise RuntimeError("Timed out waiting for SSH on 'agent-admin@192.168.122.10'")
        return "192.168.122.10", Path("/tmp/test-key")

    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ssh_access", _fake_resolve_vm_ssh_access
    )

    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="terminated pid=4321 state_files=1\n", stderr=""
        )

    monkeypatch.setattr("agent_sandbox.admin.service._run_subprocess", _fake_run)

    result = service.kill_agent("vm-123", 4321)

    assert result.success is True
    assert "agent@192.168.122.10" in captured["cmd"]


def test_clone_repo_into_vm_clones_repo(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_private_key_path", lambda: Path("/tmp/test-key")
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_host_python_with_pip",
        lambda: str(REPO_ROOT / ".venv" / "bin" / "python"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        if cmd[0].endswith("resolve_repo_source.sh") and "--path-only" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="/src/repo\n", stderr=""
            )
        if cmd[0].endswith("resolve_repo_source.sh") and "--name-only" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="sample-repo\n", stderr=""
            )
        if cmd == ["git", "-C", "/src/repo", "config", "--get", "branch.main.remote"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="origin\n", stderr="")
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
    )
    result = service.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        repo_source="/src/repo",
    )

    assert result.success is True
    captured_cmds = [cmd for cmd, _env in captured_runs]
    assert any(
        cmd[0].endswith("create_git_remote.sh") and cmd[1] == "sample-repo" for cmd in captured_cmds
    )
    assert any(
        cmd[0] == "git" and cmd[1].endswith("/sample-repo.git") and cmd[2] == "fetch"
        for cmd in captured_cmds
    )
    assert any(
        cmd[0] == "git"
        and cmd[1].endswith("/sample-repo.git")
        and cmd[2:5] == ["symbolic-ref", "HEAD", "refs/heads/main"]
        for cmd in captured_cmds
    )
    assert any(
        cmd[0].endswith("bootstrap_git_vm.sh") and cmd[1:] == ["lab-vm", "agent"]
        for cmd in captured_cmds
    )
    assert any(
        cmd[0] == "ssh" and cmd[-3:] == ["agent@192.168.250.10", "bash", "-s"]
        for cmd in captured_cmds
    )


def test_clone_repo_into_vm_installs_agent_push_and_pr_note_helpers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    captured_ssh_input: list[str] = []

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_private_key_path", lambda: Path("/tmp/test-key")
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0].endswith("resolve_repo_source.sh") and "--path-only" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="/src/repo\n", stderr=""
            )
        if cmd[0].endswith("resolve_repo_source.sh") and "--name-only" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="sample-repo\n", stderr=""
            )
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd[0] == "ssh":
            captured_ssh_input.append(str(kwargs.get("input") or ""))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service.create_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
    )
    result = service.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
        repo_source="/src/repo",
    )

    assert result.success is True
    assert captured_ssh_input
    remote_script = captured_ssh_input[-1]
    assert "config alias.agent-pr-note" in remote_script
    assert "config alias.push-agent" in remote_script
    assert "config alias.sync-base" in remote_script
    assert 'AGENT_PUSH_HELPER_B64="' in remote_script
    assert 'SYNC_BASE_HELPER_B64="' in remote_script
    assert '.agent-sandbox/bin/push-agent' in remote_script
    assert '.agent-sandbox/bin/sync-base' in remote_script
    assert "config agent-sandbox.prNotesRef" in remote_script
    assert "config agent-sandbox.branchSuffix" in remote_script
    assert "config agent-sandbox.workBranch" in remote_script


def test_clone_repo_into_vm_honors_clone_branch_and_records_base_branch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    captured_ssh_input: list[str] = []

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_private_key_path", lambda: Path("/tmp/test-key")
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0].endswith("resolve_repo_source.sh") and "--path-only" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="/src/repo\n", stderr=""
            )
        if cmd[0].endswith("resolve_repo_source.sh") and "--name-only" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="sample-repo\n", stderr=""
            )
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd[0] == "ssh":
            captured_ssh_input.append(str(kwargs.get("input") or ""))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service.create_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
    )
    result = service.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
        repo_source="/src/repo",
        clone_branch="release/1.2",
    )

    assert result.success is True
    remote_script = captured_ssh_input[-1]
    assert 'CLONE_BRANCH="release/1.2"' in remote_script
    assert 'git clone --branch "${CLONE_BRANCH}" "${REMOTE_URL}" "${TARGET_DIR}"' in remote_script
    assert 'BASE_BRANCH="${CLONE_BRANCH:-}"' in remote_script
    assert 'config agent-sandbox.baseBranch "${BASE_BRANCH}"' in remote_script
    assert 'git -C "${TARGET_DIR}" switch -c "${WORK_BRANCH}"' in remote_script


def test_clone_repo_into_vm_preserves_canonical_remote_for_remote_sources(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    captured_ssh_input: list[str] = []
    remote_source = "git@ssh.dev.azure.com:v3/example/project/sample-repo"

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_private_key_path", lambda: Path("/tmp/test-key")
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0].endswith("resolve_repo_source.sh") and "--path-only" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="/src/repo-cache/sample-repo\n", stderr=""
            )
        if cmd[0].endswith("resolve_repo_source.sh") and "--name-only" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="sample-repo\n", stderr=""
            )
        if cmd[:5] == ["git", "-C", "/src/repo-cache/sample-repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo-cache/sample-repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd[0] == "ssh":
            captured_ssh_input.append(str(kwargs.get("input") or ""))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service.create_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
    )
    result = service.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
        repo_source=remote_source,
    )

    assert result.success is True
    remote_script = captured_ssh_input[-1]
    assert f'CANONICAL_REMOTE_URL="{remote_source}"' in remote_script
    assert 'git -C "${TARGET_DIR}" remote set-url origin "${CANONICAL_REMOTE_URL}"' in remote_script
    assert (
        'git -C "${TARGET_DIR}" config "url.${REMOTE_URL}.insteadOf" "${CANONICAL_REMOTE_URL}"'
        in remote_script
    )
    assert 'git -C "${TARGET_DIR}" remote add agent "${REMOTE_URL}"' in remote_script


def test_bootstrap_python_in_vm_runs_python_bootstrap(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_host_python_with_pip",
        lambda: str(REPO_ROOT / ".venv" / "bin" / "python"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service.create_vm(vm_name="lab-vm", session_id="lab-session")
    result = service.bootstrap_python_in_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        host_checkout="/src/repo",
        target_dir="/home/agent/sample-repo",
    )

    assert result.success is True
    python_runs = [
        (cmd, env) for cmd, env in captured_runs if cmd[0].endswith("bootstrap_python_vm.sh")
    ]
    assert len(python_runs) == 1
    assert python_runs[0][0][1:] == [
        "--vm-user",
        "agent",
        "lab-vm",
        "/src/repo",
        "/home/agent/sample-repo",
    ]
    assert python_runs[0][1] is not None
    assert python_runs[0][1]["HOST_PYTHON_BIN"] == str(REPO_ROOT / ".venv" / "bin" / "python")


def test_create_vm_merges_additional_allowed_tools_into_session_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        allowed_tools=["list_unread_feedback", "post_pr_feedback_reply"],
        tool_argument_policies={
            "create_ado_backlog_item": [
                {
                    "id": "create-policy",
                    "constraints": [
                        {"id": "title", "path": "title", "operator": "required"}
                    ],
                }
            ]
        },
    )

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert ToolName.APPROVAL_SUBMIT in manifest.allowed_tools
    assert ToolName.APPROVAL_CHECK in manifest.allowed_tools
    assert ToolName.LIST_UNREAD_FEEDBACK in manifest.allowed_tools
    assert ToolName.POST_PR_FEEDBACK_REPLY in manifest.allowed_tools
    assert manifest.tool_argument_policies["create_ado_backlog_item"][0].id == "create-policy"


def test_create_vm_upsert_validates_tool_argument_policies_for_existing_session(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            allowed_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )
    )

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        allow_existing_session=True,
        tool_argument_policies={
            "create_ado_backlog_item": [
                {
                    "id": "upsert-policy",
                    "constraints": [
                        {"id": "project", "path": "project", "operator": "required"}
                    ],
                }
            ]
        },
    )

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    rule = manifest.tool_argument_policies["create_ado_backlog_item"][0]
    assert rule.id == "upsert-policy"
    assert rule.constraints[0].path == "project"


def test_create_vm_sets_teams_chat_allowlist_on_session_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        teams_channel_allowlist=["team-a/channel-1"],
        teams_chat_allowlist=[" chat:chat-1 ", "chat:chat-1", "chat:chat-2"],
    )

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.teams_channel_allowlist == ["team-a/channel-1"]
    assert manifest.teams_chat_allowlist == ["chat:chat-1", "chat:chat-2"]


def test_create_vm_sets_allowed_script_names_on_session_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        allowed_script_names=["deploy-review", " collect-logs ", "deploy-review"],
    )

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.allowed_script_names == ["deploy-review", "collect-logs"]


def test_bootstrap_frontend_in_vm_bootstraps_frontend_runtime(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service.create_vm(vm_name="lab-vm", session_id="lab-session")
    result = service.bootstrap_frontend_in_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        host_checkout="/src/repo",
        install_roots=["/src/repo/Src/Clients", "/src/repo/Src/Admin"],
    )

    assert result.success is True
    frontend_runs = [
        (cmd, env) for cmd, env in captured_runs if cmd[0].endswith("bootstrap_frontend_deps_vm.sh")
    ]
    assert len(frontend_runs) == 1
    assert frontend_runs[0][0][1:] == [
        "--vm-user",
        "agent",
        "lab-vm",
        "/src/repo",
        "/src/repo/Src/Clients",
        "/src/repo/Src/Admin",
    ]


def test_create_vm_passes_codex_skills_to_setup_script(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        codex_skills=["playwright", "playwright-interactive"],
    )

    assert result.success is True
    setup_run = next(
        cmd for cmd, _env in captured_runs if any(part.endswith("setup_vm.sh") for part in cmd)
    )
    setup_script_index = next(
        index for index, part in enumerate(setup_run) if part.endswith("setup_vm.sh")
    )
    setup_args = setup_run[setup_script_index + 1 :]
    assert "--codex-skill" in setup_args
    assert setup_args[:6] == [
        "--codex-skill",
        "playwright",
        "--codex-skill",
        "playwright-interactive",
        "lab-vm",
        "lab-session",
    ]


def test_create_vm_uses_runtime_skill_library_for_managed_codex_skills(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    service.create_skill({"name": "managed.skill", "content": "# Managed\n"})
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        codex_skills=["managed.skill"],
    )

    assert result.success is True
    setup_env = next(
        env for cmd, env in captured_runs if any(part.endswith("setup_vm.sh") for part in cmd)
    )
    assert setup_env is not None
    assert setup_env["HOST_REPO_CODEX_SKILLS_DIR"] == str(service.config.runtime_dir / "skills")


def test_bootstrap_frontend_in_vm_requires_install_roots(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    result = service.bootstrap_frontend_in_vm(
        vm_name="lab-vm",
        host_checkout="/src/repo",
        install_roots=[],
    )

    assert result.success is False
    assert result.message == "install_roots is required."


def test_create_vm_preserves_session_when_bootstrap_fails_after_launch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    state = {"launched": False}
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field",
        lambda vm_name, field_name: (
            "running" if state["launched"] and field_name == "State" else None
        ),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if any(part.endswith("setup_vm.sh") for part in cmd):
            state["launched"] = True
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="codex failed"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.create_vm(vm_name="lab-vm", session_id="lab-session")

    assert result.success is False
    assert result.message == (
        "Failed to create VM lab-vm: codex failed\n"
        "VM lab-vm and session lab-session were preserved for inspection."
    )
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.vm_id == "lab-vm"


def test_create_vm_failure_preserves_stdout_and_stderr(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    state = {"launched": False}
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field",
        lambda vm_name, field_name: (
            "running" if state["launched"] and field_name == "State" else None
        ),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if any(part.endswith("setup_vm.sh") for part in cmd):
            state["launched"] = True
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="Bootstrapping Codex into agent@192.168.250.204\n",
                stderr="scp: /home/agent/bin/codex: No such file or directory\n",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.create_vm(vm_name="idea-generator-vm", session_id="idea-generator")

    assert result.success is False
    assert "Bootstrapping Codex into agent@192.168.250.204" in result.message
    assert "scp: /home/agent/bin/codex: No such file or directory" in result.message
    assert "were preserved for inspection" in result.message


def test_create_vm_waits_for_ssh_before_bootstrap(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(
        "agent_sandbox.admin.service._dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._resolve_vm_ip", lambda vm_name: "192.168.250.154"
    )
    captured_cmds: list[list[str]] = []
    monkeypatch.setattr(
        "agent_sandbox.admin.service.subprocess.run",
        lambda cmd, **kwargs: (
            captured_cmds.append(cmd)
            or subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        ),
    )

    result = service.create_vm(vm_name="idea-generator-vm", session_id="idea-generator")

    assert result.success is True
    assert any(any(part.endswith("setup_vm.sh") for part in cmd) for cmd in captured_cmds)


def test_sync_repo_into_bare_remote_sets_head_to_default_branch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    issue_setup = service._resolve_issue_setup("root", root_dir=Path(__file__).resolve().parents[1])
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_cmds.append(cmd)
        if cmd[0].endswith("create_git_remote.sh"):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        if cmd[0] == "git" and "symbolic-ref" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[0] == "git" and "fetch" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service._sync_repo_into_bare_remote(
        "sample-repo", "/src/repo", issue_setup=issue_setup, repo_source="/src/repo"
    )

    assert any(
        cmd[0] == "git"
        and cmd[1].endswith("/sample-repo.git")
        and cmd[2:5] == ["symbolic-ref", "HEAD", "refs/heads/main"]
        for cmd in captured_cmds
    )
    assert any(
        cmd
        == [
            "git",
            f"--git-dir={issue_setup.git_root}/sample-repo.git",
            "config",
            "agent-sandbox.trustedRepoPath",
            "/src/repo",
        ]
        for cmd in captured_cmds
    )
    assert any(
        cmd
        == [
            "git",
            f"--git-dir={issue_setup.git_root}/sample-repo.git",
            "config",
            "agent-sandbox.mirrorSource",
            "/src/repo",
        ]
        for cmd in captured_cmds
    )
    assert any(
        cmd
        == [
            "git",
            f"--git-dir={issue_setup.git_root}/sample-repo.git",
            "config",
            "agent-sandbox.mirrorRemoteName",
            "origin",
        ]
        for cmd in captured_cmds
    )
    assert any(
        cmd
        == [
            "git",
            f"--git-dir={issue_setup.git_root}/sample-repo.git",
            "config",
            "agent-sandbox.mirrorSyncRemote",
            "1",
        ]
        for cmd in captured_cmds
    )
    assert any(
        cmd
        == [
            "git",
            f"--git-dir={issue_setup.git_root}/sample-repo.git",
            "config",
            "agent-sandbox.baseBranch",
            "main",
        ]
        for cmd in captured_cmds
    )
    assert any(
        cmd == ["git", "-C", "/src/repo", "remote", "get-url", "origin"] for cmd in captured_cmds
    )


def test_sync_repo_into_bare_remote_preserves_detected_remote_name(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    issue_setup = service._resolve_issue_setup("root", root_dir=Path(__file__).resolve().parents[1])
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_cmds.append(cmd)
        if cmd[0].endswith("create_git_remote.sh"):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        if cmd[0] == "git" and "fetch" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:5] == ["git", "-C", "/src/repo", "branch", "--show-current"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd == ["git", "-C", "/src/repo", "config", "--get", "branch.main.remote"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="upstream\n", stderr=""
            )
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="upstream/main\n", stderr=""
            )
        if cmd == ["git", "-C", "/src/repo", "remote", "get-url", "upstream"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="https://example.com/org/repo.git\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service._sync_repo_into_bare_remote(
        "sample-repo", "/src/repo", issue_setup=issue_setup, repo_source="/src/repo"
    )

    assert any(
        cmd
        == [
            "git",
            f"--git-dir={issue_setup.git_root}/sample-repo.git",
            "config",
            "agent-sandbox.mirrorRemoteName",
            "upstream",
        ]
        for cmd in captured_cmds
    )
    assert any(
        cmd == ["git", "-C", "/src/repo", "remote", "get-url", "upstream"] for cmd in captured_cmds
    )


def test_sync_repo_into_bare_remote_records_canonical_origin_url_when_available(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    issue_setup = service._resolve_issue_setup("root", root_dir=Path(__file__).resolve().parents[1])
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_cmds.append(cmd)
        if cmd[0].endswith("create_git_remote.sh"):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        if cmd[0] == "git" and "fetch" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd == ["git", "-C", "/src/repo", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="https://example.com/org/repo.git\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service._sync_repo_into_bare_remote(
        "sample-repo", "/src/repo", issue_setup=issue_setup, repo_source="/src/repo"
    )

    assert any(
        cmd
        == [
            "git",
            f"--git-dir={issue_setup.git_root}/sample-repo.git",
            "config",
            "agent-sandbox.canonicalOriginUrl",
            "https://example.com/org/repo.git",
        ]
        for cmd in captured_cmds
    )


def test_sync_repo_into_bare_remote_unwraps_repo_cache_origin_url(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    issue_setup = service._resolve_issue_setup("root", root_dir=Path(__file__).resolve().parents[1])
    captured_cmds: list[list[str]] = []
    cached_origin = "/tmp/agent-sandbox/runtime/repo-cache/example.git"

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_cmds.append(cmd)
        if cmd[0].endswith("create_git_remote.sh"):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        if cmd[0] == "git" and "fetch" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd == ["git", "-C", "/src/repo", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=f"{cached_origin}\n", stderr=""
            )
        if cmd == ["git", f"--git-dir={cached_origin}", "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="https://example.com/org/repo.git\n", stderr=""
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    service._sync_repo_into_bare_remote(
        "sample-repo", "/src/repo", issue_setup=issue_setup, repo_source="/src/repo"
    )

    assert any(
        cmd
        == [
            "git",
            f"--git-dir={issue_setup.git_root}/sample-repo.git",
            "config",
            "agent-sandbox.canonicalOriginUrl",
            "https://example.com/org/repo.git",
        ]
        for cmd in captured_cmds
    )


def test_resolve_host_python_with_pip_prefers_current_interpreter(monkeypatch: Any) -> None:
    monkeypatch.delenv("HOST_PYTHON_BIN", raising=False)
    monkeypatch.setattr(admin_service.sys, "executable", "/work/.venv/bin/python")

    def _fake_has_pip(candidate: str) -> bool:
        return candidate == "/work/.venv/bin/python"

    monkeypatch.setattr("agent_sandbox.admin.service._python_has_pip", _fake_has_pip)

    assert admin_service._resolve_host_python_with_pip() == "/work/.venv/bin/python"


def test_resolve_host_python_with_pip_honors_configured_override(monkeypatch: Any) -> None:
    monkeypatch.setenv("HOST_PYTHON_BIN", "/custom/python")
    monkeypatch.setattr(
        "agent_sandbox.admin.service._ensure_python_has_pip",
        lambda candidate: candidate == "/custom/python",
    )

    assert admin_service._resolve_host_python_with_pip() == "/custom/python"


def test_resolve_host_python_with_pip_bootstraps_pip_with_ensurepip(monkeypatch: Any) -> None:
    monkeypatch.delenv("HOST_PYTHON_BIN", raising=False)
    monkeypatch.setattr(admin_service.sys, "executable", "/work/.venv/bin/python3")
    attempts: list[str] = []

    def _fake_ensure(candidate: str) -> bool:
        attempts.append(candidate)
        return candidate == "/work/.venv/bin/python3"

    monkeypatch.setattr("agent_sandbox.admin.service._ensure_python_has_pip", _fake_ensure)

    assert admin_service._resolve_host_python_with_pip() == "/work/.venv/bin/python3"
    assert attempts == ["/work/.venv/bin/python3"]


def test_bootstrap_python_in_vm_requires_host_checkout(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    result = service.bootstrap_python_in_vm(
        vm_name="lab-vm", host_checkout="", target_dir="/home/agent/repo"
    )

    assert result.success is False
    assert result.message == "host_checkout is required."


def test_wait_for_vm_ip_uses_pkexec_when_requested(monkeypatch) -> None:
    calls: list[bool] = []

    def _fake_plain(vm_name: str) -> str | None:
        calls.append(False)
        return None

    def _fake_pkexec(vm_name: str) -> str | None:
        calls.append(True)
        return "192.168.250.10"

    monkeypatch.setattr("agent_sandbox.admin.service._resolve_vm_ip", _fake_plain)
    monkeypatch.setattr("agent_sandbox.admin.service._resolve_vm_ip_with_pkexec", _fake_pkexec)
    monkeypatch.setattr("agent_sandbox.admin.service.time.sleep", lambda _seconds: None)

    ip = admin_service._wait_for_vm_ip(
        "vm-test",
        timeout_seconds=1,
        allow_pkexec=True,
    )

    assert ip == "192.168.250.10"
    assert calls == [False, True]


def test_unlock_privileged_operations_starts_helper(monkeypatch, tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.use_privileged_helper = True
    started: list[str] = []

    class FakeHelper:
        def __init__(self, *, python_executable: str, project_dir: Path) -> None:
            started.append("init")

        def start(self) -> None:
            started.append("start")

        def is_running(self) -> bool:
            return True

        def close(self) -> None:
            started.append("close")

    monkeypatch.setattr("agent_sandbox.admin.service.PrivilegedHelperSession", FakeHelper)

    assert service.unlock_privileged_operations() == "Privileged operations unlocked."
    assert service.privileged_operations_unlocked() is True
    service.lock_privileged_operations()
    assert started == ["init", "start", "close"]


def test_destroy_vm_prunes_generic_sessions_and_feedback_state(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="other-session",
            vm_id="other-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="destroyed", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "agent_sandbox.admin.service._prune_approvals_for_sessions",
        lambda runtime, session_ids: len(session_ids),
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._prune_pr_map",
        lambda path, session_ids: (len(session_ids), {"stream-a"} if session_ids else set()),
    )
    monkeypatch.setattr(
        "agent_sandbox.admin.service._prune_pr_feedback_store",
        lambda path, streams: (len(streams), len(streams)),
    )

    result = service.destroy_vm("lab-vm")

    assert result.success is True
    assert calls[0][:2] == ["sudo", "-n"]
    assert calls[0][-2:] == [
        str(Path(__file__).resolve().parents[1] / "scripts" / "destroy_agent_vm.sh"),
        "lab-vm",
    ]
    assert service.runtime.session_repository.get_session("lab-session") is None
    assert service.runtime.session_repository.get_session("other-session") is not None
    assert "removed_sessions=1" in result.message
    assert "removed_pr_map_entries=1" in result.message


def test_destroy_vm_does_not_try_pkexec_when_no_prompt_is_available(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr("agent_sandbox.admin.service._can_prompt_for_privileges", lambda: False)
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="", stderr="sudo: a password is required"
        )

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.destroy_vm("lab-vm")

    assert result.success is False
    assert result.message == "sudo: a password is required"
    assert len(calls) == 1
    assert calls[0][:2] == ["sudo", "-n"]


def test_destroy_vm_falls_back_to_pkexec_when_prompt_is_available(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr("agent_sandbox.admin.service._can_prompt_for_privileges", lambda: True)
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["sudo", "-n"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="sudo: a password is required"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="destroyed", stderr="")

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)

    result = service.destroy_vm("lab-vm")

    assert result.success is True
    assert calls[0][:2] == ["sudo", "-n"]
    assert calls[1][:2] == ["pkexec", "env"]


def test_create_and_list_wake_schedules_include_last_dispatch(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="0 6,15 * * 1-5",
        source="admin_cli",
        resume_mode="new_context",
    )
    schedules = service.list_wake_schedules(session_id="issue-1-session")
    assert len(schedules) == 1
    assert schedules[0]["cron"] == "0 6,15 * * 1-5"
    assert schedules[0]["resume_mode"] == "new_context"
    assert schedules[0]["last_dispatch"] is None


def test_create_wake_schedule_rejects_invalid_cron(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    with pytest.raises(ValueError, match="Invalid cron expression"):
        service.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume now",
            cron="* *",
        )


def test_wake_schedule_cron_validation_accepts_standard_range_syntax() -> None:
    assert admin_service._require_valid_cron(" 0 6,15 * * 1-5 ") == "0 6,15 * * 1-5"


def test_create_wake_schedule_enforces_max_active_per_session(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.config.wake_schedule_max_active_per_session = 1
    service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
    )

    with pytest.raises(ValueError, match="max_active_per_session=1"):
        service.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume again",
            cron="*/10 * * * *",
        )


def test_create_wake_schedule_enforces_max_active_total(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.config.wake_schedule_max_active_total = 1
    service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
    )

    with pytest.raises(ValueError, match="max_active_total=1"):
        service.create_wake_schedule(
            session_id="issue-2-session",
            prompt="Resume another",
            cron="*/10 * * * *",
        )


def test_create_wake_schedule_enforces_min_interval(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.config.wake_schedule_min_interval_seconds = 600

    with pytest.raises(ValueError, match="minimum=600s actual=300s"):
        service.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume now",
            cron="*/5 * * * *",
        )


def test_edit_wake_schedule_enforces_guardrails_when_enabling_or_tightening_cron(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.config.wake_schedule_max_active_per_session = 1
    service.config.wake_schedule_min_interval_seconds = 600
    created = service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume later",
        cron="*/15 * * * *",
        enabled=False,
    )
    service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Existing active",
        cron="*/20 * * * *",
    )

    with pytest.raises(ValueError, match="max_active_per_session=1"):
        service.edit_wake_schedule(created.request_id, enabled=True)

    other = service.create_wake_schedule(
        session_id="issue-2-session",
        prompt="Safe interval",
        cron="*/15 * * * *",
    )
    with pytest.raises(ValueError, match="minimum=600s actual=300s"):
        service.edit_wake_schedule(other.request_id, cron="*/5 * * * *")


def test_edit_wake_schedule_rejects_new_context_for_mcp_source(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    created = service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        resume_at=datetime.now(UTC) + timedelta(hours=1),
        source="mcp_tool",
        resume_mode="same_context",
    )
    with pytest.raises(ValueError, match="MCP scheduling only supports"):
        service.edit_wake_schedule(created.request_id, resume_mode="new_context")


def test_create_wake_schedule_rejects_new_context_for_mcp_source(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    with pytest.raises(ValueError, match="MCP scheduling only supports"):
        service.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume now",
            resume_at=datetime.now(UTC) + timedelta(hours=1),
            source="mcp_schedule_resume",
            resume_mode="new_context",
        )


def test_list_wake_schedules_filters_disabled_entries(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    created = service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
        source="admin_cli",
    )
    service.edit_wake_schedule(created.request_id, enabled=False)

    schedules = service.list_wake_schedules(session_id="issue-1-session", include_disabled=False)

    assert schedules == []


def test_pr_create_writes_inline_body_to_temporary_file_and_cleans_it_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    captured: dict[str, Any] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = list(cmd)
        captured["env"] = dict(kwargs.get("env") or {})
        body_file = cmd[cmd.index("--body-file") + 1]
        body_path = Path(body_file)
        captured["body_file"] = body_path
        captured["body"] = body_path.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout='AGENT_PR_RESULT {"pr_url":"https://example.test/pr/14724","pr_id":"14724"}\n',
            stderr="",
        )

    monkeypatch.setattr(admin_service, "_run_subprocess", fake_run_subprocess)

    result = service.pr_create(
        host_repo_path="/tmp/repos/ExampleIndustryAPP",
        local_branch="review/issue-14724",
        canonical_origin_url="git@ssh.dev.azure.com:v3/example/project/repo",
        project="Example Industry APP",
        title="14724: Test Issue: Truman",
        body="Inline PR body",
        body_artifact_ids=["comm_11111111111111111111111111111111"],
        draft=True,
    )

    cmd = captured["cmd"]
    body_path = captured["body_file"]
    assert cmd[0].endswith("/scripts/open_agent_pr.sh")
    assert "--create-pr" in cmd
    assert "--draft" in cmd
    assert "--body-artifact-id" in cmd
    assert "comm_11111111111111111111111111111111" in cmd
    assert "--project" in cmd
    assert "Example Industry APP" in cmd
    assert (
        captured["env"]["AGENT_SANDBOX_CANONICAL_ORIGIN_URL"]
        == "git@ssh.dev.azure.com:v3/example/project/repo"
    )
    assert captured["body"] == "Inline PR body"
    assert not body_path.exists()
    assert result["success"] is True
    assert result["pr_url"] == "https://example.test/pr/14724"
    assert result["pr_id"] == "14724"


def test_mirror_remote_name_for_repo_uses_remote_name_from_detached_remote_head(
    tmp_path: Path,
) -> None:
    upstream_bare = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "init", "--bare", str(upstream_bare)], check=True, text=True, capture_output=True
    )

    source_repo = tmp_path / "source"
    subprocess.run(
        ["git", "clone", str(upstream_bare), str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }

    subprocess.run(
        ["git", "-C", str(source_repo), "checkout", "-b", "main"],
        check=True,
        text=True,
        capture_output=True,
    )
    (source_repo / "README.md").write_text("v1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(source_repo), "add", "README.md"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-m", "Initial commit"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "push", "-u", "origin", "main"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", f"--git-dir={upstream_bare}", "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "checkout", "HEAD^0"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert admin_service._mirror_remote_name_for_repo(str(source_repo)) == "origin"


def _write_proxy_issue_yaml(
    tmp_path: Path,
    *,
    issue_number: int = 42,
    title: str = "Proxy issue",
    description: str = "from remote",
    state: str = "New",
    organization_url: str = "https://dev.azure.com/example",
    project: str = "sample",
) -> Path:
    payload = {
        "issue_number": issue_number,
        "title": title,
        "description": description,
        "state": state,
        "url": f"https://ado.example/work/{issue_number}",
        "source": {
            "organization": organization_url,
            "project": project,
            "work_item_url": f"{organization_url.rstrip('/')}/{project}/_workitems/edit/{issue_number}",
        },
    }
    path = tmp_path / f"proxy-issue-{issue_number}-{state.lower()}.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
